"""Vérificateurs — le cœur du framework, et sa raison d'être.

L'IDÉE, EN UNE PHRASE
  Un document d'entreprise porte, hors du modèle, des invariants exacts que
  l'on peut recalculer : une clé de Luhn, une clé mod-97, une identité
  arithmétique, une seconde représentation du même fait. Ces invariants sont
  un signal d'abstention meilleur que la confiance d'un modèle, et ils sont
  gratuits.

CE QUI DISTINGUE UN VERDICT D'UN BOOLÉEN
  Un vérificateur ne dit pas seulement « faux ». Il dit **quoi** est faux,
  **où** le chercher, et **quelle action** pourrait le corriger. C'est cette
  information qui rend la boucle de réparation possible (voir `agent.py`) :
  sans elle, un échec ne laisse d'autre choix que d'appeler un humain.

    Verdict(
        passe=False,
        champ="siren",
        detail="La clé de Luhn ne tombe pas.",
        indice=Indice.CARACTERE_MAL_LU,     # ce que l'échec suggère
        cible="441630465",                  # de quoi localiser sur la page
    )

TROIS RÈGLES QUI GOUVERNENT CE MODULE
  1. **Aucun modèle.** Un vérificateur est de l'arithmétique et des tables. Un
     modèle peut halluciner un total qui tombe juste ; ce code ne le peut pas.
     C'est ce qui rend le plancher de risque indépendant de l'analyseur.
  2. **Aucune correction silencieuse.** Un vérificateur constate. Il ne
     réécrit jamais un champ. Corriger sans le dire, c'est fabriquer une
     donnée fausse et confiante.
  3. **La gravité est déclarée par l'auteur du contrôle.** Elle ne se devine
     pas à partir de l'intitulé — nous avons payé cette leçon : un contrôle
     nommé « IBAN check digits » ne contenait ni « checksum » ni « key », et
     un IBAN faux passait en simple avertissement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Protocol, runtime_checkable


class Gravite(str, Enum):
    """Ce qu'un échec entraîne. Choisi par l'auteur du contrôle, pas déduit."""

    BLOQUANT = "blocking"      # interdit l'automatisation
    AVERTISSEMENT = "warning"  # signale sans interdire
    INFORMATION = "info"       # trace, n'influence rien


class Indice(str, Enum):
    """Ce que l'échec suggère — la moitié utile d'un verdict négatif.

    C'est le vocabulaire que la boucle de réparation consomme pour choisir une
    action. Volontairement court : un indice trop fin serait deviné, donc faux.
    """

    CARACTERE_MAL_LU = "misread_char"      # un ou deux caractères, la structure tient
    CHAMP_ABSENT = "missing_field"         # zone manquée ou absente du document
    INCOHERENCE_INTERNE = "internal_mismatch"  # plusieurs champs incompatibles
    DIVERGENCE_SOURCES = "source_mismatch"  # deux représentations se contredisent
    HORS_REFERENTIEL = "unknown_entity"    # forme correcte, entité inconnue
    AUCUN = "none"


@dataclass
class Verdict:
    """Le résultat d'un vérificateur. Sérialisable tel quel dans l'audit."""

    nom: str
    passe: bool
    gravite: Gravite = Gravite.BLOQUANT
    champ: str | None = None
    valeur: Any = None
    detail: str = ""
    indice: Indice = Indice.AUCUN
    cible: str | None = None          # texte à retrouver sur la page
    attendu: Any = None               # ce qu'un invariant exigeait
    cout_ms: float = 0.0

    @property
    def bloque(self) -> bool:
        return not self.passe and self.gravite is Gravite.BLOQUANT

    def dict(self) -> dict[str, Any]:
        return {
            "check": self.nom, "passed": self.passe,
            "severity": self.gravite.value, "field": self.champ,
            "value": self.valeur, "detail": self.detail,
            "hint": self.indice.value, "expected": self.attendu,
        }

    def __str__(self) -> str:
        marque = "OK" if self.passe else "ÉCHEC"
        return f"[{marque}] {self.nom}" + (f" — {self.detail}" if self.detail else "")


@runtime_checkable
class Verificateur(Protocol):
    """N'importe quel contrôle. Une fonction suffit.

        def tva_intra(champs, ctx=None):
            n = champs.get("vat_number")
            ...
            return Verdict("Numéro de TVA intracommunautaire", ok, ...)

    Rendre `None` signifie « non applicable » : un contrôle de SIREN sur une
    facture suisse ne doit pas produire un échec, il doit se taire. Confondre
    « non applicable » et « échec » est la faute la plus courante des couches
    de validation maison, et elle noie les vrais problèmes.
    """

    nom: str

    def __call__(self, champs: dict[str, Any],
                 contexte: dict[str, Any] | None = None
                 ) -> Verdict | list[Verdict] | None:
        ...


# --------------------------------------------------------------- registre

@dataclass
class Registre:
    """Un ensemble de vérificateurs, nommé et versionné.

    Versionné parce que la comparaison de deux courbes risque–couverture n'a
    de sens que si l'on sait quel ensemble de contrôles était actif. « Nos
    chiffres se sont améliorés » ne veut rien dire si l'on a entre-temps
    retiré un contrôle.
    """

    nom: str
    version: str = "1"
    verificateurs: list[Verificateur] = field(default_factory=list)

    def ajouter(self, v: Verificateur) -> "Registre":
        self.verificateurs.append(v)
        return self

    def __iadd__(self, v: Verificateur) -> "Registre":
        return self.ajouter(v)

    def executer(self, champs: dict[str, Any],
                 contexte: dict[str, Any] | None = None) -> list[Verdict]:
        """Exécute tout, mesure le coût, et n'interrompt jamais la série.

        Un vérificateur qui lève une exception ne doit pas empêcher les autres
        de s'exécuter : sur un document dégradé, c'est justement quand un
        contrôle casse que les suivants comptent le plus. L'exception devient
        elle-même un verdict bloquant — un contrôle qui n'a pas pu s'exécuter
        n'est pas un contrôle qui passe.
        """
        import time
        sortie: list[Verdict] = []
        for v in self.verificateurs:
            nom = getattr(v, "nom", getattr(v, "__name__", "vérificateur"))
            t0 = time.perf_counter()
            try:
                r = v(champs, contexte)
            except Exception as e:  # noqa: BLE001 — voir la docstring
                sortie.append(Verdict(
                    nom, False, Gravite.BLOQUANT, detail=(
                        f"Le contrôle a échoué à s'exécuter : "
                        f"{type(e).__name__} {e}. Un contrôle qui ne tourne pas "
                        f"n'est pas un contrôle qui passe."),
                    cout_ms=(time.perf_counter() - t0) * 1000))
                continue
            dt = (time.perf_counter() - t0) * 1000
            if r is None:
                continue                      # non applicable : silence
            for verdict in (r if isinstance(r, list) else [r]):
                verdict.cout_ms = dt
                sortie.append(verdict)
        return sortie

    def __len__(self) -> int:
        return len(self.verificateurs)


def verificateur(nom: str, gravite: Gravite = Gravite.BLOQUANT,
                 champ: str | None = None,
                 indice: Indice = Indice.CARACTERE_MAL_LU):
    """Décorateur : transforme un prédicat en vérificateur complet.

        @verificateur("HT + TVA = TTC", champ="total_incl_vat",
                      indice=Indice.INCOHERENCE_INTERNE)
        def totaux(champs, ctx=None):
            ht, tva, ttc = ...
            if None in (ht, tva, ttc):
                return None                   # non applicable
            return abs(ht + tva - ttc) <= 0.01, f"écart de {…}"

    Le prédicat rend `None` (non applicable), un booléen, ou un couple
    (booléen, détail). Le décorateur s'occupe du reste : c'est ce qui rend
    l'écriture d'un contrôle assez courte pour qu'on l'écrive vraiment.
    """
    def deco(f: Callable) -> Verificateur:
        def enveloppe(champs, contexte=None):
            r = f(champs, contexte)
            if r is None:
                return None
            if isinstance(r, Verdict):
                return r
            passe, detail = (r if isinstance(r, tuple) else (r, ""))
            valeur = champs.get(champ) if champ else None
            if isinstance(valeur, dict):
                valeur = valeur.get("value")
            return Verdict(nom, bool(passe), gravite, champ=valeur and champ,
                           valeur=valeur, detail="" if passe else detail,
                           indice=Indice.AUCUN if passe else indice,
                           cible=str(valeur) if valeur is not None else None)
        enveloppe.nom = nom  # type: ignore[attr-defined]
        return enveloppe  # type: ignore[return-value]
    return deco


# ------------------------------------------------------------- agrégation

@dataclass
class Rapport:
    """L'ensemble des verdicts, et ce qu'on en conclut."""

    verdicts: list[Verdict] = field(default_factory=list)
    registre: str = ""
    version_registre: str = ""

    @property
    def bloquants(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.bloque]

    @property
    def avertissements(self) -> list[Verdict]:
        return [v for v in self.verdicts
                if not v.passe and v.gravite is Gravite.AVERTISSEMENT]

    @property
    def passes(self) -> int:
        return sum(1 for v in self.verdicts if v.passe)

    @property
    def cout_ms(self) -> float:
        return sum(v.cout_ms for v in self.verdicts)

    @property
    def reparable(self) -> list[Verdict]:
        """Les échecs qu'une seconde lecture pourrait corriger.

        Un caractère mal lu ou un champ manqué se retentent. Une divergence
        entre deux sources ou une entité hors référentiel ne se règle pas en
        relisant mieux : ce sont des faits, pas des erreurs de lecture.
        """
        return [v for v in self.verdicts
                if not v.passe and v.indice in (Indice.CARACTERE_MAL_LU,
                                                Indice.CHAMP_ABSENT,
                                                Indice.INCOHERENCE_INTERNE)]

    def resume(self) -> str:
        if not self.verdicts:
            return "Aucun contrôle applicable."
        if not self.bloquants and not self.avertissements:
            return (f"{self.passes} contrôle(s) passés, aucun écart "
                    f"({self.cout_ms:.1f} ms).")
        parts = []
        if self.bloquants:
            parts.append(f"{len(self.bloquants)} bloquant(s)")
        if self.avertissements:
            parts.append(f"{len(self.avertissements)} avertissement(s)")
        return (f"{self.passes}/{len(self.verdicts)} contrôles passés — "
                + ", ".join(parts) + f". Premier : {self.bloquants[0].detail}"
                if self.bloquants else
                f"{self.passes}/{len(self.verdicts)} contrôles passés — "
                + ", ".join(parts) + ".")


def executer(registre: Registre, champs: dict[str, Any],
             contexte: dict[str, Any] | None = None) -> Rapport:
    return Rapport(registre.executer(champs, contexte),
                   registre.nom, registre.version)
