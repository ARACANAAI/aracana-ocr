"""Mesure : courbe risque–couverture, AURC, couverture à risque borné.

POURQUOI CE MODULE EXISTE
  Le banc de référence du domaine, OmniDocBench, est saturé au-delà de 94 % et
  mesure une fidélité de transcription — une distance d'édition qui compte de
  la même façon un nom mal accentué et un montant faux. Aucune décision de
  déploiement ne se prend là-dessus.

  Ce module mesure ce qui décide : **quelle part du flux peut être traitée
  sans humain, à quel taux d'erreurs acceptées.**

LA DÉFINITION QUI CHANGE TOUT
  Le risque ne compte que les erreurs **acceptées**. Une erreur détectée et
  envoyée en revue n'est pas un échec du système : c'est son fonctionnement
  normal. Confondre les deux — ce que fait une précision ordinaire — revient à
  pénaliser un système parce qu'il a fait son travail.

      couverture c = |acceptés| / |total|
      risque     r = |acceptés ET faux| / |acceptés|

  Sweeper la politique du plus permissif au plus strict trace la courbe.
  L'AURC la résume ; `couverture_a_risque` répond à la question du client.

PRÉCAUTION MÉTHODOLOGIQUE
  Traub et al. (NeurIPS 2024) montrent qu'on compare souvent, sans le voir,
  des systèmes dont le prédicteur diffère — confondant l'apport de la fonction
  d'abstention avec celui du modèle. `comparer_verificateurs()` impose donc de
  **figer l'analyseur** ; `comparer_analyseurs()` impose de figer les
  vérificateurs. Il n'existe pas de fonction qui fasse varier les deux : ce
  serait produire un chiffre ininterprétable.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .policy import Decision, Issue, Politique, echelle


@dataclass
class Cas:
    """Un document évalué : ce que le système a décidé, et ce qui était vrai.

    `verite` ne contient que les **champs décisifs** — montants, numéro, date,
    identifiants bancaires. Évaluer la transcription entière ramènerait à la
    distance d'édition et à ses défauts.
    """

    identifiant: str
    champs: dict[str, Any]
    verite: dict[str, Any]
    decision: Decision

    def juste(self, champs_decisifs: Sequence[str] | None = None) -> bool:
        cles = champs_decisifs or list(self.verite)
        for c in cles:
            attendu = self.verite.get(c)
            if attendu is None:
                continue                       # pas de vérité pour ce champ
            obtenu = self.champs.get(c)
            if isinstance(obtenu, dict):
                obtenu = obtenu.get("value") if obtenu.get("found") else None
            if not _equivalent(obtenu, attendu):
                return False
        return True


def _equivalent(a: Any, b: Any) -> bool:
    """Égalité tolérante au format, stricte sur la valeur.

    « 1 250,00 » et « 1250.00 » sont le même montant ; 1250 et 1251 ne le sont
    pas. Une comparaison de chaînes brutes ferait échouer des lectures
    correctes et gonflerait artificiellement le risque.
    """
    if a is None or b is None:
        return a is b
    try:
        return abs(float(str(a).replace(" ", "").replace(" ", "")
                         .replace(" ", "").replace("'", "")
                         .replace(",", ".")) - float(str(b).replace(",", "."))) < 0.005
    except (TypeError, ValueError):
        pass
    norm = lambda x: "".join(ch for ch in str(x).lower() if ch.isalnum())
    return norm(a) == norm(b)


@dataclass
class Point:
    politique: str
    couverture: float
    risque: float
    acceptes: int
    faux_acceptes: int
    total: int

    def __str__(self) -> str:
        return (f"{self.politique:28s} couverture {self.couverture:6.1%}  "
                f"risque {self.risque:6.2%}  "
                f"({self.faux_acceptes}/{self.acceptes} acceptés faux)")


@dataclass
class Courbe:
    points: list[Point] = field(default_factory=list)
    analyseur: str = ""
    registre: str = ""

    @property
    def aurc(self) -> float:
        """Aire sous la courbe risque–couverture, par les trapèzes.

        Plus bas vaut mieux. Comparable entre systèmes évalués sur le MÊME jeu
        et avec la même échelle de politiques ; comparer deux AURC issues de
        jeux différents ne veut rien dire, et nous ne fournissons pas de
        fonction qui le permettrait.
        """
        pts = sorted(self.points, key=lambda p: p.couverture)
        if len(pts) < 2:
            return pts[0].risque if pts else 0.0
        aire = 0.0
        for a, b in zip(pts, pts[1:]):
            aire += (b.couverture - a.couverture) * (a.risque + b.risque) / 2
        etendue = pts[-1].couverture - pts[0].couverture
        return aire / etendue if etendue > 0 else pts[0].risque

    def couverture_a_risque(self, epsilon: float) -> Point | None:
        """`c@r≤ε` — la question que pose un directeur comptable.

        « Combien puis-je automatiser en restant sous un pour mille
        d'erreurs ? » Rend le point de plus grande couverture respectant la
        borne, ou `None` si aucun ne la respecte — auquel cas la réponse
        honnête est « aucun réglage ne tient votre exigence », pas un chiffre
        approché.
        """
        eligibles = [p for p in self.points if p.risque <= epsilon]
        return max(eligibles, key=lambda p: p.couverture) if eligibles else None

    def __str__(self) -> str:
        entete = f"Courbe risque–couverture · {self.analyseur} · {self.registre}"
        lignes = [entete, "-" * len(entete)]
        lignes += [f"  {p}" for p in sorted(self.points,
                                            key=lambda x: -x.couverture)]
        lignes.append(f"  AURC = {self.aurc:.4f}  (plus bas vaut mieux)")
        return "\n".join(lignes)


def courbe(cas: Iterable[Cas],
           rejouer: Callable[[Cas, Politique], Decision],
           *, politiques: Sequence[Politique] | None = None,
           champs_decisifs: Sequence[str] | None = None,
           analyseur: str = "", registre: str = "") -> Courbe:
    """Trace la courbe en durcissant progressivement la politique.

    `rejouer` reprend un cas sous une autre politique. On ne relit pas le
    document : les verdicts sont déjà calculés, seule la décision change. Cela
    rend le balayage instantané et garantit que **seule la politique varie** —
    l'exigence méthodologique de Traub et al.
    """
    cas = list(cas)
    if not cas:
        return Courbe([], analyseur, registre)
    pts = []
    for p in (politiques or echelle()):
        decisions = [rejouer(c, p) for c in cas]
        acceptes = [(c, d) for c, d in zip(cas, decisions) if d.accepte]
        faux = [c for c, _ in acceptes if not c.juste(champs_decisifs)]
        pts.append(Point(
            politique=p.nom,
            couverture=len(acceptes) / len(cas),
            risque=(len(faux) / len(acceptes)) if acceptes else 0.0,
            acceptes=len(acceptes), faux_acceptes=len(faux), total=len(cas)))
    return Courbe(pts, analyseur, registre)


# --------------------------------------------------------------- incertitude

def bootstrap_apparie(cas_a: Sequence[Cas], cas_b: Sequence[Cas],
                      *, champs_decisifs: Sequence[str] | None = None,
                      tirages: int = 5000, graine: int = 0
                      ) -> tuple[float, float, float]:
    """Intervalle de confiance à 95 % sur l'écart de risque entre A et B.

    **Apparié** : on rééchantillonne les mêmes documents pour les deux
    systèmes, pas deux échantillons indépendants. Sur des jeux de quelques
    centaines de documents, la variance entre échantillons dépasse largement
    l'écart que l'on cherche à mesurer ; un test non apparié conclurait au
    hasard.

    Rend (écart médian, borne basse, borne haute). Un intervalle qui contient
    zéro signifie qu'on ne peut pas départager — et il faut le dire.
    """
    if len(cas_a) != len(cas_b):
        raise ValueError(
            "Le bootstrap apparié exige les mêmes documents des deux côtés : "
            f"{len(cas_a)} contre {len(cas_b)}.")
    rng = random.Random(graine)
    n = len(cas_a)
    if n == 0:
        return 0.0, 0.0, 0.0

    def risque(sous: Sequence[Cas]) -> float:
        acc = [c for c in sous if c.decision.accepte]
        if not acc:
            return 0.0
        return sum(1 for c in acc if not c.juste(champs_decisifs)) / len(acc)

    ecarts = []
    for _ in range(tirages):
        idx = [rng.randrange(n) for _ in range(n)]
        ecarts.append(risque([cas_a[i] for i in idx])
                      - risque([cas_b[i] for i in idx]))
    ecarts.sort()
    return (ecarts[tirages // 2],
            ecarts[int(tirages * 0.025)],
            ecarts[int(tirages * 0.975)])


# ------------------------------------------------------------- comparaisons

def comparer_verificateurs(jeux: dict[str, list[Cas]],
                           rejouer: Callable[[Cas, Politique], Decision],
                           **kw: Any) -> dict[str, Courbe]:
    """Plusieurs ensembles de vérificateurs, **analyseur figé**. Sert C1.

    Les clés sont les noms des registres. L'appelant est responsable d'avoir
    produit tous les jeux avec le même analyseur : nous ne pouvons pas le
    vérifier, mais nous le documentons et le nommons dans la sortie.
    """
    return {nom: courbe(c, rejouer, registre=nom, **kw)
            for nom, c in jeux.items()}


def comparer_analyseurs(jeux: dict[str, list[Cas]],
                        rejouer: Callable[[Cas, Politique], Decision],
                        **kw: Any) -> dict[str, Courbe]:
    """Plusieurs analyseurs, **vérificateurs figés**. Sert C3.

    C'est l'expérience centrale : si le risque à couverture fixée varie peu
    entre Docling, Marker, MinerU et ARACANA, alors le plancher de risque est
    porté par les vérificateurs et non par l'analyseur — et le framework a
    bien la portée générale qu'il revendique. Si l'écart est grand, C3 est
    fausse et il faut le publier.
    """
    return {nom: courbe(c, rejouer, analyseur=nom, **kw)
            for nom, c in jeux.items()}


def cout_total(point: Point, *, cout_erreur: float, cout_revue: float,
               cout_calcul: float = 0.0) -> float:
    """Le coût du modèle économique de RECHERCHE.md §3.5, pour un point donné.

        coût = c·N·r·C_erreur + (1−c)·N·C_revue + N·C_calcul

    Rendu par document, pour être comparable d'un flux à l'autre. C'est la
    traduction en euros de la courbe : le point de coût minimal n'est presque
    jamais la couverture maximale, et le montrer chiffré vaut mieux que de
    l'affirmer.
    """
    c, r = point.couverture, point.risque
    return c * r * cout_erreur + (1 - c) * cout_revue + cout_calcul


def optimum_economique(courbe_: Courbe, *, cout_erreur: float,
                       cout_revue: float, cout_calcul: float = 0.0
                       ) -> tuple[Point, float] | None:
    """Le point de coût minimal, et ce qu'il coûte par document."""
    if not courbe_.points:
        return None
    couts = [(p, cout_total(p, cout_erreur=cout_erreur, cout_revue=cout_revue,
                            cout_calcul=cout_calcul))
             for p in courbe_.points]
    return min(couts, key=lambda x: x[1])
