"""Politique — des verdicts à une décision, et rien d'autre.

CE QUE CE MODULE EST
  La fonction d'abstention `g` du cadre de prédiction sélective (Geifman &
  El-Yaniv, NeurIPS 2017). Le prédicteur `f` est la chaîne analyseur +
  extracteur ; `g` est ici. Les séparer n'est pas une élégance : c'est la
  condition pour mesurer l'apport de chacun (voir Traub et al., NeurIPS 2024,
  et §4.4 de RECHERCHE.md).

CE QU'IL NE FAIT PAS
  Il ne regarde jamais le document, ni le texte, ni la confiance d'un modèle.
  Il ne voit que des verdicts. Toute décision doit être reconstructible depuis
  les verdicts seuls — c'est ce qui la rend auditable et rejouable, et ce qui
  satisfait l'article 12 de l'AI Act sans effort particulier.

LE SEUIL EST UN CHOIX ÉCONOMIQUE, PAS TECHNIQUE
  Durcir la politique déplace un point sur la courbe risque–couverture. Le bon
  point dépend de `C_erreur / C_revue` chez le client, pas de nous. D'où
  `Politique.depuis_couts()` : on prend les deux coûts et l'on rend la
  politique correspondante, plutôt que de demander un seuil abstrait à
  quelqu'un qui n'a aucun moyen de le choisir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .verify import Gravite, Rapport, Verdict


class Issue(str, Enum):
    """Trois issues, et pas quatre. « Probablement bon » n'est pas une issue :
    c'est une manière de ne pas décider."""

    ACCEPTE = "auto_post"
    REVUE = "human_review"
    REJET = "rejected"


@dataclass
class Decision:
    issue: Issue
    justification: str
    verdicts: list[Verdict] = field(default_factory=list)
    motifs: list[str] = field(default_factory=list)
    politique: str = ""
    reparations: int = 0

    @property
    def accepte(self) -> bool:
        return self.issue is Issue.ACCEPTE

    def dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue.value,
            "justification": self.justification,
            "reasons": self.motifs,
            "policy": self.politique,
            "repairs": self.reparations,
            "checks": [v.dict() for v in self.verdicts],
        }


@dataclass
class Politique:
    """Comment un ensemble de verdicts devient une décision.

    Les paramètres sont peu nombreux et tous justifiés :

    `champs_requis`   sans eux, une écriture serait introuvable lors d'un
                      contrôle. Leur absence rejette, elle n'avertit pas.
    `tolere_avertissements`  un avertissement signale sans interdire. Le
                      durcir est le premier cran d'une courbe risque–couverture.
    `min_controles`   un document sur lequel aucun contrôle n'a pu s'exécuter
                      n'est pas un document sûr : c'est un document non
                      contrôlé. Les confondre est l'erreur qui fait passer les
                      pages illisibles en automatique.
    """

    nom: str = "standard"
    champs_requis: tuple[str, ...] = ("invoice_number", "invoice_date",
                                      "total_incl_vat")
    tolere_avertissements: bool = True
    min_controles: int = 1
    gravites_bloquantes: tuple[Gravite, ...] = (Gravite.BLOQUANT,)

    # ------------------------------------------------------------ décision
    def decider(self, rapport: Rapport, champs: dict[str, Any],
                *, lisible: bool = True) -> Decision:
        motifs: list[str] = []

        if not lisible:
            return Decision(Issue.REJET, "Document illisible ou non reconnu.",
                            rapport.verdicts, ["unreadable"], self.nom)

        manquants = [c for c in self.champs_requis if not _present(champs, c)]
        if manquants:
            motifs.append(
                f"Champs obligatoires absents : {', '.join(manquants)}. "
                f"Sans eux, la pièce serait introuvable lors d'un contrôle.")

        bloquants = [v for v in rapport.verdicts
                     if not v.passe and v.gravite in self.gravites_bloquantes]
        motifs += [f"{v.nom} : {v.detail or 'échec'}" for v in bloquants]

        if len(rapport.verdicts) < self.min_controles:
            motifs.append(
                f"Seulement {len(rapport.verdicts)} contrôle(s) applicable(s) — "
                f"le minimum est {self.min_controles}. Un document non contrôlé "
                f"n'est pas un document sûr.")

        if not self.tolere_avertissements:
            motifs += [f"{v.nom} : {v.detail or 'signalé'}"
                       for v in rapport.avertissements]

        if motifs:
            return Decision(Issue.REVUE, _phrase_revue(motifs),
                            rapport.verdicts, motifs, self.nom)

        return Decision(
            Issue.ACCEPTE,
            f"Accepté : {rapport.passes} contrôle(s) passés, aucun écart.",
            rapport.verdicts, [], self.nom)

    # -------------------------------------------------- réglage économique
    @classmethod
    def depuis_couts(cls, cout_erreur: float, cout_revue: float,
                     **kw: Any) -> "Politique":
        """Choisit la sévérité à partir des coûts réels du client.

        Le raisonnement, volontairement simple et explicite :

          — si une erreur coûte plus de vingt fois une relecture, on ne tolère
            aucun avertissement et l'on exige au moins deux contrôles ;
          — entre cinq et vingt fois, on tolère les avertissements mais on
            exige que des contrôles aient eu lieu ;
          — en dessous, l'automatisation large est rationnelle.

        Ces bornes ne sont pas des vérités : ce sont des points de départ, à
        remplacer par la courbe risque–couverture mesurée sur les documents du
        client. `riskcov.py` sert exactement à cela. Nous les documentons
        plutôt que de les cacher dans un « niveau de confiance » opaque.
        """
        if cout_revue <= 0:
            raise ValueError("Le coût d'une revue doit être strictement positif.")
        ratio = cout_erreur / cout_revue
        if ratio >= 20:
            return cls(nom="prudente", tolere_avertissements=False,
                       min_controles=2, **kw)
        if ratio >= 5:
            return cls(nom="standard", tolere_avertissements=True,
                       min_controles=1, **kw)
        return cls(nom="permissive", tolere_avertissements=True,
                   min_controles=0, **kw)

    def _nomme(self) -> str:
        """Un nom qui décrit l'ÉTAT, pas l'historique des durcissements.

        La première version concaténait à chaque cran et produisait
        « permissive+sans-avertissement+1contrôles+2contrôles+3contrôles ».
        Illisible, et surtout faux : ce nom prétend décrire un chemin alors
        qu'il désigne un réglage. Sur l'axe d'une courbe publiée, une étiquette
        trompeuse est un défaut de mesure, pas de présentation.
        """
        av = "strict" if not self.tolere_avertissements else "tolérant"
        return f"{av}·≥{self.min_controles}contrôle{'s' if self.min_controles > 1 else ''}"

    def durcir(self) -> "Politique | None":
        """Le cran suivant, pour tracer une courbe risque–couverture.

        Rend `None` quand il n'y a plus rien à durcir : la courbe s'arrête là,
        et prétendre le contraire produirait des points inventés.
        """
        if self.tolere_avertissements:
            suivante = Politique("", self.champs_requis, False,
                                 self.min_controles, self.gravites_bloquantes)
        elif self.min_controles < 4:
            suivante = Politique("", self.champs_requis, False,
                                 self.min_controles + 1,
                                 self.gravites_bloquantes)
        else:
            return None
        suivante.nom = suivante._nomme()
        return suivante


def _present(champs: dict[str, Any], cle: str) -> bool:
    v = champs.get(cle)
    if isinstance(v, dict):
        return bool(v.get("found")) and v.get("value") is not None
    return v is not None


def _phrase_revue(motifs: list[str]) -> str:
    tete = motifs[0]
    reste = f" (+{len(motifs) - 1} autre(s))" if len(motifs) > 1 else ""
    return f"Envoyé en revue humaine : {tete}{reste}"


def echelle(base: Politique | None = None) -> list[Politique]:
    """La suite des politiques, du plus permissif au plus strict.

    C'est l'axe de la courbe risque–couverture. On la construit ici plutôt
    que dans le module de mesure pour que la même échelle serve en production
    et en évaluation — comparer une courbe tracée avec une échelle à un
    déploiement réglé avec une autre ne voudrait rien dire.
    """
    p = base or Politique(nom="permissive", tolere_avertissements=True,
                          min_controles=0)
    suite = [p]
    while (p := p.durcir()) is not None:
        suite.append(p)
    return suite
