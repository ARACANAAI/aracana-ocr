"""Extensions — comment un outil tiers entre DANS le pipeline.

LE PROBLÈME QUE CE MODULE RÉSOUT
  Un framework dont les extensions tournent « à côté » n'est pas un framework,
  c'est une bibliothèque avec de la documentation. Pour qu'un vérificateur
  écrit par un cabinet allemand, un adaptateur pour un analyseur maison ou un
  connecteur vers un logiciel comptable soient utiles, il faut qu'ils
  s'insèrent **au bon endroit de la chaîne, sans modifier le cœur**.

  Quatre points d'insertion, et pas un de plus. Chacun correspond à une
  abstraction de RECHERCHE.md §4.1 :

    parsers      document brut  → Document canonique
    verifiers    champs         → Verdicts
    sinks        Décision       → écriture comptable, file, notification
    tools        Verdict        → nouvelle lecture ciblée (boucle agent)

DEUX MÉCANISMES, ET POURQUOI LES DEUX
  1. **Enregistrement direct** — `plugins.parser(MonAnalyseur())`. Immédiat,
     explicite, sans installation. C'est ce qu'utilise un développeur qui
     écrit trente lignes dans son propre projet.
  2. **Points d'entrée Python** — un paquet publié se déclare dans son
     `pyproject.toml` et devient disponible dès `pip install`, sans une ligne
     de code chez l'utilisateur.

  Le second est ce qui fait exister un écosystème. Le premier est ce qui fait
  qu'on l'essaie avant de le publier.

CE QUE CE MODULE NE FAIT PAS
  Il ne charge rien automatiquement au moment de l'import du paquet. La
  découverte est explicite (`plugins.decouvrir()`) parce qu'un framework qui
  exécute du code tiers à l'import surprend son utilisateur — et parce qu'un
  démarrage d'API doit rester prévisible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

#: Les quatre points d'insertion. Le groupe de points d'entrée correspondant
#: est `aracana.<type>` — par exemple `aracana.parsers`.
TYPES = ("parsers", "verifiers", "sinks", "tools")


@dataclass
class Extension:
    """Ce qui a été enregistré, et d'où cela vient.

    La provenance n'est pas décorative : quand un vérificateur inattendu
    bloque un document en production, la première question est « d'où sort ce
    contrôle ? ». Elle doit avoir une réponse dans le journal d'audit.
    """

    nom: str
    type: str
    objet: Any
    origine: str = "direct"        # "direct" ou le nom du paquet distribué
    version: str = ""

    def __str__(self) -> str:
        v = f" v{self.version}" if self.version else ""
        return f"{self.type}/{self.nom}{v}  ({self.origine})"


class Registre:
    """Le registre global des extensions. Un seul par processus."""

    def __init__(self) -> None:
        self._par_type: dict[str, dict[str, Extension]] = {t: {} for t in TYPES}
        self._decouvert = False

    # --------------------------------------------------- enregistrement
    def ajouter(self, type_: str, nom: str, objet: Any, *,
                origine: str = "direct", version: str = "") -> Extension:
        if type_ not in TYPES:
            raise KeyError(
                f"{type_!r} n'est pas un point d'insertion. "
                f"Connus : {', '.join(TYPES)}. "
                f"En ajouter un demanderait de modifier le cœur ; c'est "
                f"précisément ce que ce mécanisme évite.")
        ancien = self._par_type[type_].get(nom)
        if ancien is not None and ancien.origine != origine:
            # Deux paquets qui revendiquent le même nom, c'est un conflit
            # silencieux jusqu'au jour où le mauvais est appelé. On refuse.
            raise ValueError(
                f"« {nom} » est déjà enregistré comme {type_} par "
                f"{ancien.origine!r}. Choisissez un autre nom : deux "
                f"extensions homonymes rendraient le comportement dépendant "
                f"de l'ordre d'installation.")
        e = Extension(nom, type_, objet, origine, version)
        self._par_type[type_][nom] = e
        return e

    # ------------------------------------------------------- découverte
    def decouvrir(self, *, silencieux: bool = True) -> list[Extension]:
        """Charge les extensions déclarées par les paquets installés.

        Un paquet tiers se déclare ainsi :

            [project.entry-points."aracana.parsers"]
            docling = "mon_paquet.adaptateurs:DoclingParser"

        Une extension qui échoue à se charger est SIGNALÉE, jamais silencieuse
        par défaut en cas d'erreur inattendue : découvrir en production qu'un
        vérificateur n'a jamais tourné parce que son import échouait est le
        genre de panne qui ne se voit pas et coûte cher.
        """
        if self._decouvert:
            return list(self)
        from importlib import metadata

        charges: list[Extension] = []
        for type_ in TYPES:
            groupe = f"aracana.{type_}"
            try:
                points = metadata.entry_points(group=groupe)
            except TypeError:                       # Python < 3.10
                points = metadata.entry_points().get(groupe, [])  # type: ignore
            for pt in points:
                try:
                    objet = pt.load()
                except Exception as e:              # noqa: BLE001
                    if not silencieux:
                        raise
                    import warnings
                    warnings.warn(
                        f"Extension {groupe}/{pt.name} non chargée : "
                        f"{type(e).__name__} {e}. Elle ne tournera PAS.",
                        RuntimeWarning, stacklevel=2)
                    continue
                paquet = getattr(pt, "dist", None)
                charges.append(self.ajouter(
                    type_, pt.name, objet,
                    origine=getattr(paquet, "name", "?") if paquet else "?",
                    version=getattr(paquet, "version", "") if paquet else ""))
        self._decouvert = True
        return charges

    # ---------------------------------------------------------- lecture
    def obtenir(self, type_: str, nom: str) -> Any:
        self.decouvrir()
        e = self._par_type.get(type_, {}).get(nom)
        if e is None:
            connus = ", ".join(sorted(self._par_type.get(type_, {}))) or "aucun"
            raise KeyError(
                f"Aucun {type_[:-1]} nommé {nom!r}. Disponibles : {connus}.")
        return e.objet

    def lister(self, type_: str | None = None) -> list[Extension]:
        self.decouvrir()
        types = [type_] if type_ else list(TYPES)
        return [e for t in types for e in self._par_type[t].values()]

    def __iter__(self) -> Iterator[Extension]:
        return iter(self.lister())

    def etat(self) -> str:
        """Ce qui est branché, pour le support et pour l'audit."""
        self.decouvrir()
        lignes = ["Extensions ARACANA", ""]
        for t in TYPES:
            items = self._par_type[t]
            lignes.append(f"  {t} ({len(items)})")
            for e in items.values():
                lignes.append(f"      {e}")
            if not items:
                lignes.append("      —")
        return "\n".join(lignes)


REGISTRE = Registre()


# ------------------------------------------------ raccourcis d'écriture

def parser(objet: Any, nom: str | None = None) -> Any:
    """Enregistre un analyseur. Utilisable en décorateur ou en appel."""
    REGISTRE.ajouter("parsers", nom or _nom_de(objet), objet)
    return objet


def verifier(objet: Any, nom: str | None = None) -> Any:
    REGISTRE.ajouter("verifiers", nom or _nom_de(objet), objet)
    return objet


def sink(objet: Any, nom: str | None = None) -> Any:
    REGISTRE.ajouter("sinks", nom or _nom_de(objet), objet)
    return objet


def tool(objet: Any, nom: str | None = None) -> Any:
    REGISTRE.ajouter("tools", nom or _nom_de(objet), objet)
    return objet


def _nom_de(objet: Any) -> str:
    for attribut in ("nom", "name", "__name__"):
        v = getattr(objet, attribut, None)
        if isinstance(v, str) and v:
            return v
    return type(objet).__name__


def decouvrir(**kw: Any) -> list[Extension]:
    return REGISTRE.decouvrir(**kw)


def obtenir(type_: str, nom: str) -> Any:
    return REGISTRE.obtenir(type_, nom)


def lister(type_: str | None = None) -> list[Extension]:
    return REGISTRE.lister(type_)


def etat() -> str:
    return REGISTRE.etat()
