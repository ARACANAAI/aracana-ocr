"""Boucle de réparation guidée par les vérificateurs.

CE QUI REND CETTE BOUCLE DÉFENDABLE, ET PAS UN SLOGAN
  Une boucle agentique a besoin d'un signal de récompense. En extraction
  documentaire on n'en a normalement pas : il faudrait la vérité terrain, que
  l'on n'a pas au moment de lire. C'est pour cela que les systèmes agentiques
  du marché font vérifier un modèle par un autre modèle — et la littérature
  dit pourquoi ce signal plafonne (RECHERCHE.md §1.3 : F1 = 0,685 pour un VLM
  entraîné explicitement à dire son incertitude).

  Ici, les vérificateurs déterministes fournissent un signal **exact et
  gratuit**. Une clé de Luhn ne se trompe jamais. C'est la différence entre
  une boucle qui converge et une boucle qui dérive.

TROIS RÉSULTATS DE LA LITTÉRATURE, APPLIQUÉS TELS QUELS
  1. **VeriHarness** (arXiv 2607.14167) : un retour contenant *la localisation
     de l'échec, la valeur observée et les alternatives admissibles* améliore
     le succès de +44 points par rapport à un simple « échec ». Nos `Verdict`
     portent exactement ces trois choses — `cible`, `valeur`, `attendu`.
  2. **Verify, Repair, Repeat, or Stop?** (arXiv 2607.17641) : avec un
     vérificateur bruité, réparer peut abîmer ce qui était juste. Notre
     vérificateur n'est pas bruité, et la règle de monotonie ci-dessous
     supprime le risque par construction.
  3. **Diagnosis Before Recovery** (arXiv 2608.11772) : diagnostiquer le type
     de panne avant de dépenser. C'est le rôle de `Indice`.

LES DEUX INVARIANTS, NON NÉGOCIABLES

  **Monotonie.** Une réparation rejoue la LECTURE, jamais le CONTRÔLE. Le
  registre de vérificateurs passé à la boucle est le même à chaque tour, et la
  politique aussi. Un document ne peut passer d'arrêté à accepté que parce
  qu'une nouvelle lecture satisfait les mêmes exigences. Sans cette règle, une
  boucle agentique devient une machine à fabriquer des acceptations —
  précisément le détournement de récompense que la littérature décrit.

  **Budget.** Nombre d'actions et temps plafonnés, arrêt sur non-progression.
  Un agent sans budget sur une facture illisible brûle un GPU indéfiniment.
  On mesure la progression au nombre de verdicts bloquants : si une action
  n'en supprime aucun, on ne la retente pas.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable

from .document import Boite, Document
from .policy import Decision, Issue, Politique
from .verify import Gravite, Indice, Rapport, Registre, executer


class Action(str, Enum):
    """Ce qu'un agent peut tenter. Court, parce qu'une action inventée est une
    action jamais testée."""

    RELIRE_FINEMENT = "reread_hi_dpi"       # même page, résolution supérieure
    RECADRER = "crop_to_field"              # zoom sur la boîte du champ fautif
    AUTRE_ANALYSEUR = "second_parser"       # une seconde opinion indépendante
    QUESTION_CIBLEE = "targeted_query"      # demander UN champ précis à un VLM
    REFERENTIEL = "external_registry"       # VIES, INSEE : confronter au réel
    ABANDONNER = "give_up"


@dataclass
class Tentative:
    """Une action et son résultat. La trace de la boucle, pour l'audit."""

    tour: int
    action: Action
    motif: str
    bloquants_avant: int
    bloquants_apres: int
    secondes: float
    retenue: bool                 # la lecture a-t-elle été conservée ?

    @property
    def a_progresse(self) -> bool:
        return self.bloquants_apres < self.bloquants_avant


@dataclass
class Budget:
    """Ce que l'on s'autorise à dépenser par document."""

    actions_max: int = 3
    secondes_max: float = 120.0
    # Arrêt sur non-progression : deux actions consécutives sans réduire le
    # nombre de bloquants signifient que la lecture n'est pas le problème.
    echecs_consecutifs_max: int = 2

    def epuise(self, tours: int, ecoule: float, echecs: int) -> str | None:
        if tours >= self.actions_max:
            return f"budget d'actions atteint ({self.actions_max})"
        if ecoule >= self.secondes_max:
            return f"budget de temps atteint ({self.secondes_max:.0f}s)"
        if echecs >= self.echecs_consecutifs_max:
            return (f"{echecs} actions consécutives sans progrès — la lecture "
                    f"n'est pas la cause")
        return None


@runtime_checkable
class Outil(Protocol):
    """Une capacité de relecture. L'agent en dispose, il ne les implémente pas.

    Séparer les deux est ce qui rend la boucle testable sans GPU : on branche
    des outils factices et l'on vérifie la logique de décision, la monotonie et
    le budget.
    """

    action: Action

    def applicable(self, verdict, doc: Document) -> bool:
        ...

    def executer(self, source: bytes, doc: Document, verdict,
                 **kw: Any) -> Document | None:
        ...


# ------------------------------------------------------- choix de l'action

#: Quelle action tenter selon ce que l'échec suggère. C'est la table de
#: `Diagnosis Before Recovery` : le diagnostic précède la dépense.
PLAN: dict[Indice, tuple[Action, ...]] = {
    Indice.CARACTERE_MAL_LU: (Action.RECADRER, Action.RELIRE_FINEMENT,
                              Action.AUTRE_ANALYSEUR),
    Indice.CHAMP_ABSENT: (Action.QUESTION_CIBLEE, Action.RELIRE_FINEMENT),
    Indice.INCOHERENCE_INTERNE: (Action.RELIRE_FINEMENT, Action.AUTRE_ANALYSEUR),
    # Une divergence entre deux sources n'est pas une erreur de lecture : c'est
    # peut-être un fait. Une seconde opinion indépendante tranche — si deux
    # analyseurs lisent la même chose contre le XML, la divergence est réelle
    # et aucune relecture n'y changera rien.
    Indice.DIVERGENCE_SOURCES: (Action.AUTRE_ANALYSEUR,),
    # Une entité de forme correcte mais inconnue ne se relit pas : elle se
    # vérifie auprès d'un référentiel.
    Indice.HORS_REFERENTIEL: (Action.REFERENTIEL,),
    Indice.AUCUN: (),
}


def _prochaine_action(rapport: Rapport, deja: set[Action],
                      outils: dict[Action, Outil], doc: Document):
    """Le premier couple (verdict, action) applicable et non encore tenté.

    On traite les bloquants avant les avertissements : réparer un
    avertissement pendant qu'un bloquant subsiste ne change pas la décision et
    dépense le budget pour rien.
    """
    for v in sorted(rapport.reparable,
                    key=lambda x: 0 if x.gravite is Gravite.BLOQUANT else 1):
        for action in PLAN.get(v.indice, ()):
            outil = outils.get(action)
            if action in deja or outil is None:
                continue
            if outil.applicable(v, doc):
                return v, action, outil
    return None, None, None


# ------------------------------------------------------------- la boucle

@dataclass
class Resultat:
    decision: Decision
    document: Document
    champs: dict[str, Any]
    rapport: Rapport
    tentatives: list[Tentative] = field(default_factory=list)
    arret: str = ""

    @property
    def repare(self) -> bool:
        return any(t.retenue and t.a_progresse for t in self.tentatives)

    def dict(self) -> dict[str, Any]:
        return {
            **self.decision.dict(),
            "repairs": [
                {"round": t.tour, "action": t.action.value, "reason": t.motif,
                 "blocking_before": t.bloquants_avant,
                 "blocking_after": t.bloquants_apres,
                 "kept": t.retenue, "seconds": round(t.secondes, 2)}
                for t in self.tentatives],
            "stopped_because": self.arret,
        }


def reparer(
    source: bytes,
    document: Document,
    *,
    extraire: Callable[[Document], dict[str, Any]],
    registre: Registre,
    politique: Politique,
    outils: dict[Action, Outil] | None = None,
    budget: Budget | None = None,
    contexte: dict[str, Any] | None = None,
) -> Resultat:
    """Lit, vérifie, et retente de façon ciblée tant que cela progresse.

    `registre` et `politique` sont capturés UNE fois et réutilisés tels quels à
    chaque tour. C'est la mise en œuvre de la monotonie : la boucle n'a
    matériellement pas la possibilité d'assouplir un contrôle.
    """
    outils = outils or {}
    budget = budget or Budget()
    debut = time.monotonic()

    champs = extraire(document)
    rapport = executer(registre, champs, contexte)
    decision = politique.decider(rapport, champs)

    tentatives: list[Tentative] = []
    tentees: set[Action] = set()
    echecs = 0
    arret = ""

    while not decision.accepte:
        raison = budget.epuise(len(tentatives), time.monotonic() - debut, echecs)
        if raison:
            arret = raison
            break

        verdict, action, outil = _prochaine_action(rapport, tentees, outils,
                                                   document)
        if action is None:
            arret = ("aucune action ne s'applique — l'échec n'est pas une "
                     "erreur de lecture")
            break

        tentees.add(action)
        avant = len(rapport.bloquants)
        t0 = time.monotonic()
        try:
            doc2 = outil.executer(source, document, verdict)
        except Exception as e:  # noqa: BLE001
            # Un outil qui casse ne doit pas emporter le document : on note
            # l'échec et l'on passe au suivant. Le document garde sa décision.
            tentatives.append(Tentative(
                len(tentatives) + 1, action, f"outil en erreur : {e}",
                avant, avant, time.monotonic() - t0, False))
            echecs += 1
            continue

        if doc2 is None:
            tentatives.append(Tentative(
                len(tentatives) + 1, action, "l'outil n'a rien rendu",
                avant, avant, time.monotonic() - t0, False))
            echecs += 1
            continue

        champs2 = extraire(doc2)
        rapport2 = executer(registre, champs2, contexte)
        apres = len(rapport2.bloquants)

        # ─── LA RÈGLE DE MONOTONIE ────────────────────────────────────────
        # On ne retient une relecture que si elle réduit STRICTEMENT le nombre
        # de bloquants. Une relecture qui en ajoute, ou qui n'en retire aucun,
        # est jetée et le document conserve son état précédent.
        #
        # Sans ce test, une seconde lecture pourrait « réussir » en produisant
        # des champs différents mais tout aussi faux, et la boucle
        # transformerait du bruit en acceptations. C'est le mode d'échec
        # décrit par arXiv 2607.17641 ; il est ici structurellement impossible.
        retenue = apres < avant
        if retenue:
            document, champs, rapport = doc2, champs2, rapport2
            decision = politique.decider(rapport, champs)
            echecs = 0
        else:
            echecs += 1

        tentatives.append(Tentative(
            len(tentatives) + 1, action,
            verdict.detail or verdict.nom, avant, apres,
            time.monotonic() - t0, retenue))

    decision.reparations = sum(1 for t in tentatives if t.retenue)
    if decision.reparations:
        decision.justification += (
            f" — après {decision.reparations} relecture(s) ciblée(s), "
            f"contrôles inchangés.")

    return Resultat(decision, document, champs, rapport, tentatives, arret)
