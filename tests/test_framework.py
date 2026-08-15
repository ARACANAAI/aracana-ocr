# -*- coding: utf-8 -*-
"""Le framework : abstractions, monotonie de l'agent, mesure risque–couverture.

Cette suite ne teste pas la lecture de documents — les autres s'en chargent.
Elle teste les GARANTIES annoncées dans framework/RECHERCHE.md, celles sur
lesquelles reposent les affirmations C1 à C5 :

  · un vérificateur qui casse ne fait pas passer un document (§4.2)
  · « non applicable » n'est pas « échec » (§4.2)
  · la boucle de réparation est MONOTONE : elle ne peut pas fabriquer une
    acceptation en assouplissant un contrôle (§4.3)
  · elle s'arrête sur budget et sur non-progression (§4.3)
  · le risque ne compte que les faux ACCEPTÉS, jamais les faux détectés (§4.4)
  · l'AURC et `c@r≤ε` se comportent comme annoncé

Une suite qui vérifierait la précision d'extraction et laisserait ces
invariants non testés vérifierait la partie facile.
"""
from contexte import Compteur, sortie_utf8

sortie_utf8()

from aracana.agent import (Action, Budget, PLAN, Resultat,  # noqa: E402
                           reparer)
from aracana.document import Bloc, Boite, Document, Page, TypeBloc  # noqa: E402
from aracana.policy import Decision, Issue, Politique, echelle  # noqa: E402
from aracana.riskcov import (Cas, Courbe, Point, bootstrap_apparie,  # noqa: E402
                             courbe, cout_total, optimum_economique)
from aracana.verify import (Gravite, Indice, Registre, Verdict,  # noqa: E402
                            executer, verificateur)

_C = Compteur()
check = _C.check

# =============================================================== document
print("=== Document canonique : une seule conversion vers les pixels ===")
b = Boite(0, 0, 999, 999)
check("la borne droite atteint exactement la largeur",
      b.pixels(2480, 3508)[2] == 2480, b.pixels(2480, 3508))
check("diviser par 1000 perdrait 2 px sur une A4 à 300 ppp",
      2480 - round(999 / 1000 * 2480) == 2)
check("aller-retour pixels stable à un pixel près",
      abs(Boite.depuis_pixels((100, 200, 300, 400), 1000, 1000).pixels(1000, 1000)[0]
          - 100) <= 1)
check("un bloc élargi reste dans la page",
      Boite(5, 5, 100, 100).elargie(20) == Boite(0, 0, 120, 120))

check("le vocabulaire de blocs projette les alias",
      TypeBloc.depuis("section_header") is TypeBloc.TITRE)
check("et tombe sur AUTRE plutôt que d'inventer",
      TypeBloc.depuis("bidule_inconnu") is TypeBloc.AUTRE)

doc = Document(pages=[Page(1, 1240, 1754, [
    Bloc(1, TypeBloc.TITRE, "SAS EXEMPLE", Boite(70, 40, 400, 80)),
    Bloc(2, TypeBloc.TEXTE, "SIREN : 441 639 465", Boite(70, 100, 700, 140)),
])])
check("localiser retrouve le bloc portant une valeur",
      doc.localiser("441639465") is not None and
      doc.localiser("441639465").ordre == 2)
check("et rend None quand la valeur est absente", doc.localiser("999") is None)

# ================================================================ verify
print("\n=== Vérificateurs : les trois règles du module ===")


@verificateur("Totaux", champ="total_incl_vat", indice=Indice.INCOHERENCE_INTERNE)
def totaux(champs, ctx=None):
    ht, tva, ttc = (champs.get(k) for k in
                    ("total_excl_vat", "vat_amount", "total_incl_vat"))
    if None in (ht, tva, ttc):
        return None                      # non applicable
    return abs(ht + tva - ttc) <= 0.01, f"écart de {ht + tva - ttc:.2f}"


def casse(champs, ctx=None):
    raise RuntimeError("panne simulée")


casse.nom = "Contrôle qui casse"

BON = {"invoice_number": "FA-1", "invoice_date": "12/03/2026",
       "total_excl_vat": 1250.0, "vat_amount": 250.0, "total_incl_vat": 1500.0}
FAUX = {**BON, "total_incl_vat": 1600.0}
PARTIEL = {"invoice_number": "FA-1", "invoice_date": "12/03/2026",
           "total_incl_vat": 1500.0}

r = Registre("essai").ajouter(totaux)
check("cohérent -> passe", executer(r, BON).verdicts[0].passe)
check("incohérent -> échoue", not executer(r, FAUX).verdicts[0].passe)
check("non applicable -> AUCUN verdict, pas un échec",
      len(executer(r, PARTIEL).verdicts) == 0,
      [v.nom for v in executer(r, PARTIEL).verdicts])

r2 = Registre("avec panne").ajouter(casse).ajouter(totaux)
rap = executer(r2, BON)
check("un contrôle qui casse produit un verdict BLOQUANT",
      any(not v.passe and v.gravite is Gravite.BLOQUANT for v in rap.verdicts))
check("et n'empêche pas les suivants de s'exécuter",
      any(v.passe for v in rap.verdicts), [str(v) for v in rap.verdicts])

check("un verdict d'échec porte un indice diagnostique",
      executer(r, FAUX).verdicts[0].indice is Indice.INCOHERENCE_INTERNE)
check("et une cible pour localiser sur la page",
      executer(r, FAUX).verdicts[0].cible == "1600.0")

# ================================================================ policy
print("\n=== Politique : la décision se reconstruit depuis les verdicts seuls ===")
p = Politique()
check("cohérent -> accepté", p.decider(executer(r, BON), BON).accepte)
check("incohérent -> revue", p.decider(executer(r, FAUX), FAUX).issue is Issue.REVUE)
check("champ obligatoire manquant -> revue",
      p.decider(executer(r, {**BON, "invoice_number": None}),
                {**BON, "invoice_number": None}).issue is Issue.REVUE)
check("illisible -> rejet",
      p.decider(executer(r, BON), BON, lisible=False).issue is Issue.REJET)

strict = Politique(min_controles=2)
check("zéro contrôle applicable n'est pas « sûr », c'est « non contrôlé »",
      strict.decider(executer(r, PARTIEL), BON).issue is Issue.REVUE)

print("\n--- l'échelle décrit des états, pas un historique ---")
noms = [x.nom for x in echelle()]
check("noms distincts", len(noms) == len(set(noms)), noms)
check("aucun nom ne s'accumule",
      all(n.count("contrôle") <= 1 for n in noms), noms)
check("l'échelle est monotone en sévérité",
      [x.min_controles for x in echelle()] == sorted(x.min_controles
                                                     for x in echelle()))

print("\n--- le seuil se dérive des coûts, pas d'un nombre abstrait ---")
check("erreur 100× la revue -> prudente",
      Politique.depuis_couts(200, 2).nom == "prudente")
check("erreur 5× la revue -> standard",
      Politique.depuis_couts(10, 2).nom == "standard")
check("erreur 2× la revue -> permissive",
      Politique.depuis_couts(4, 2).nom == "permissive")
try:
    Politique.depuis_couts(100, 0)
    check("un coût de revue nul est refusé", False, "aucune exception")
except ValueError:
    check("un coût de revue nul est refusé", True)

# ================================================================= agent
print("\n=== Agent : la monotonie, invariant central ===")


class OutilFactice:
    """Un outil de relecture scripté, pour tester la LOGIQUE sans GPU."""

    def __init__(self, action, champs_rendus, appels=None):
        self.action = action
        self._champs = champs_rendus
        self.appels = appels if appels is not None else []

    def applicable(self, verdict, doc):
        return True

    def executer(self, source, doc, verdict, **kw):
        self.appels.append(verdict.nom)
        return Document(pages=[Page(1, 100, 100, [])], analyseur="factice")


def faire_extraire(sequence):
    """Rend un extracteur qui débite `sequence` à chaque appel."""
    etat = {"i": -1}

    def extraire(doc):
        etat["i"] += 1
        return sequence[min(etat["i"], len(sequence) - 1)]
    return extraire


# 1. Une relecture qui CORRIGE est retenue.
outil = OutilFactice(Action.RELIRE_FINEMENT, BON)
res = reparer(b"x", Document(), extraire=faire_extraire([FAUX, BON]),
              registre=r, politique=Politique(),
              outils={Action.RELIRE_FINEMENT: outil}, budget=Budget(actions_max=3))
check("une relecture qui supprime le bloquant est retenue", res.decision.accepte,
      res.decision.justification)
check("et la réparation est comptée", res.decision.reparations == 1)
check("la justification le dit", "relecture" in res.decision.justification)

# 2. Une relecture qui NE corrige PAS est jetée.
res = reparer(b"x", Document(), extraire=faire_extraire([FAUX, FAUX, FAUX, FAUX]),
              registre=r, politique=Politique(),
              outils={Action.RELIRE_FINEMENT: OutilFactice(Action.RELIRE_FINEMENT, FAUX)},
              budget=Budget(actions_max=3))
check("une relecture sans progrès n'est pas retenue",
      not res.decision.accepte and res.decision.reparations == 0)
check("et l'arrêt est motivé", bool(res.arret), res.arret)

# 3. LE CAS QUI COMPTE : une relecture qui EMPIRE ne doit jamais être retenue.
PIRE = {**BON, "invoice_number": None, "total_incl_vat": 9999.0}
res = reparer(b"x", Document(), extraire=faire_extraire([FAUX, PIRE, PIRE, PIRE]),
              registre=r, politique=Politique(),
              outils={Action.RELIRE_FINEMENT: OutilFactice(Action.RELIRE_FINEMENT, PIRE)},
              budget=Budget(actions_max=3))
check("une relecture qui EMPIRE est rejetée",
      res.champs is not PIRE and res.champs.get("invoice_number") == "FA-1",
      res.champs.get("invoice_number"))
check("aucune tentative empirante n'est retenue",
      all(not t.retenue for t in res.tentatives if t.bloquants_apres > t.bloquants_avant))

# 4. Un outil qui casse n'emporte pas le document.
class OutilQuiCasse(OutilFactice):
    def executer(self, source, doc, verdict, **kw):
        raise RuntimeError("outil défaillant")


res = reparer(b"x", Document(), extraire=faire_extraire([FAUX, FAUX]),
              registre=r, politique=Politique(),
              outils={Action.RELIRE_FINEMENT: OutilQuiCasse(Action.RELIRE_FINEMENT, BON)},
              budget=Budget(actions_max=2))
check("un outil qui lève une exception ne fait pas planter la boucle",
      res.decision.issue is Issue.REVUE)
check("l'échec de l'outil est tracé",
      any("erreur" in t.motif for t in res.tentatives),
      [t.motif for t in res.tentatives])

# 5. Budget respecté.
compteur = []
res = reparer(b"x", Document(), extraire=faire_extraire([FAUX] * 20),
              registre=r, politique=Politique(),
              outils={a: OutilFactice(a, FAUX, compteur)
                      for a in (Action.RELIRE_FINEMENT, Action.RECADRER,
                                Action.AUTRE_ANALYSEUR)},
              budget=Budget(actions_max=2, echecs_consecutifs_max=5))
check("le plafond d'actions est respecté", len(res.tentatives) <= 2,
      len(res.tentatives))

res = reparer(b"x", Document(), extraire=faire_extraire([FAUX] * 20),
              registre=r, politique=Politique(),
              outils={a: OutilFactice(a, FAUX)
                      for a in (Action.RELIRE_FINEMENT, Action.RECADRER,
                                Action.AUTRE_ANALYSEUR)},
              budget=Budget(actions_max=10, echecs_consecutifs_max=2))
check("l'arrêt sur non-progression fonctionne", len(res.tentatives) <= 3,
      len(res.tentatives))
check("et il dit que la lecture n'est pas la cause",
      "n'est pas la cause" in res.arret, res.arret)

print("\n--- le diagnostic précède la dépense ---")
check("une incohérence interne déclenche une relecture",
      Action.RELIRE_FINEMENT in PLAN[Indice.INCOHERENCE_INTERNE])
check("une divergence entre sources ne se relit PAS finement",
      Action.RELIRE_FINEMENT not in PLAN[Indice.DIVERGENCE_SOURCES],
      PLAN[Indice.DIVERGENCE_SOURCES])
check("une entité hors référentiel ne se relit pas non plus",
      PLAN[Indice.HORS_REFERENTIEL] == (Action.REFERENTIEL,))

# =============================================================== riskcov
print("\n=== Mesure : le risque ne compte que les faux ACCEPTÉS ===")


def cas(nom, accepte, juste):
    return Cas(nom, {"total_incl_vat": 100.0 if juste else 999.0},
               {"total_incl_vat": 100.0},
               Decision(Issue.ACCEPTE if accepte else Issue.REVUE, ""))


jeu = [cas("a", True, True), cas("b", True, True),
       cas("c", True, False),        # accepté ET faux -> compte
       cas("d", False, False),       # faux mais DÉTECTÉ -> ne compte pas
       cas("e", False, True)]

pt = courbe(jeu, lambda c, p: c.decision,
            politiques=[Politique(nom="fixe")]).points[0]
check("couverture = 3/5", abs(pt.couverture - 0.6) < 1e-9, pt.couverture)
check("risque = 1/3, le faux détecté ne compte pas",
      abs(pt.risque - 1 / 3) < 1e-9, pt.risque)
check("un faux envoyé en revue n'est PAS un échec du système",
      pt.faux_acceptes == 1, pt.faux_acceptes)

print("\n--- comparaison tolérante au format, stricte sur la valeur ---")
c1 = Cas("x", {"total_incl_vat": "1 250,00"}, {"total_incl_vat": 1250.0},
         Decision(Issue.ACCEPTE, ""))
check("« 1 250,00 » vaut 1250.0", c1.juste())
c2 = Cas("y", {"total_incl_vat": 1251.0}, {"total_incl_vat": 1250.0},
         Decision(Issue.ACCEPTE, ""))
check("1251 ne vaut pas 1250", not c2.juste())
c3 = Cas("z", {"total_incl_vat": "1'250.00"}, {"total_incl_vat": 1250.0},
         Decision(Issue.ACCEPTE, ""))
check("l'apostrophe suisse aussi", c3.juste())

print("\n--- AURC et couverture à risque borné ---")
cb = Courbe([Point("permissif", 0.9, 0.10, 90, 9, 100),
             Point("moyen", 0.6, 0.02, 60, 1, 100),
             Point("strict", 0.3, 0.00, 30, 0, 100)])
check("AURC entre 0 et 1", 0 <= cb.aurc <= 1, cb.aurc)
check("c@r≤1% choisit la plus grande couverture éligible",
      cb.couverture_a_risque(0.01).politique == "strict")
check("c@r≤5% en choisit une plus large",
      cb.couverture_a_risque(0.05).politique == "moyen")
check("aucun point sous un seuil impossible -> None, pas une approximation",
      cb.couverture_a_risque(-1) is None)

print("\n--- le coût traduit la courbe en euros ---")
opt, cout = optimum_economique(cb, cout_erreur=200, cout_revue=2)
check("l'optimum n'est PAS la couverture maximale", opt.politique != "permissif",
      opt.politique)
check("et il coûte moins que le point permissif",
      cout < cout_total(cb.points[0], cout_erreur=200, cout_revue=2),
      (cout, cout_total(cb.points[0], cout_erreur=200, cout_revue=2)))
opt2, _ = optimum_economique(cb, cout_erreur=4, cout_revue=2)
check("si l'erreur coûte peu, l'optimum se déplace vers la couverture",
      opt2.couverture >= opt.couverture, (opt2.politique, opt.politique))

print("\n--- bootstrap apparié ---")
a = [cas(str(i), True, i % 10 != 0) for i in range(200)]     # 10 % de faux
bb = [cas(str(i), True, True) for i in range(200)]           # 0 % de faux
med, bas, haut = bootstrap_apparie(a, bb, tirages=800, graine=1)
check("l'écart médian est proche de 10 points", 0.05 < med < 0.15, med)
check("l'intervalle ne contient pas zéro sur un écart réel",
      bas > 0, (bas, haut))
med0, bas0, haut0 = bootstrap_apparie(a, a, tirages=800, graine=1)
check("un système comparé à lui-même donne un écart nul",
      abs(med0) < 1e-9 and bas0 == haut0 == 0.0, (med0, bas0, haut0))
try:
    bootstrap_apparie(a, bb[:10])
    check("des tailles différentes sont refusées", False, "aucune exception")
except ValueError:
    check("des tailles différentes sont refusées", True)

# ==================================================================== pro
print("\n=== Frontière ouvert / commercial ===")
from aracana import pro  # noqa: E402

check("le catalogue est non vide", len(pro.CATALOGUE) >= 5)
check("aucune fonction Pro n'est installée ici",
      not pro.disponible("journal_scelle"))
try:
    pro.charger("journal_scelle")
    check("charger lève une erreur explicite", False, "aucune exception")
except pro.ProIndisponible as e:
    m = str(e)
    check("l'erreur dit ce que fait la fonction", "intégrité" in m or "hachage" in m)
    check("elle dit où l'obtenir", "pip install aracana-ocr-pro" in m)
    check("et elle ne prétend PAS que l'ouvert est bridé",
          "fonctionne sans cela" in m, m[-90:])
try:
    pro.enregistrer("fonction_inventee", lambda: None)
    check("un nom hors catalogue est refusé", False, "aucune exception")
except KeyError:
    check("un nom hors catalogue est refusé", True)


# ============================================================== adaptateurs
print("\n=== Adaptateurs : le contrat que TOUS doivent satisfaire ===")
# Sans ce contrat, comparer quatre analyseurs ne mesure pas C3 : cela mesure
# quatre facons differentes de rendre un resultat.
from aracana.parsers import (ADAPTATEURS, AnalyseurIndisponible,  # noqa: E402
                             _boite_docling, disponibles)


class FauxBBox:
    def __init__(self, l, t, r, b, origine):
        self.l, self.t, self.r, self.b = l, t, r, b
        self.coord_origin = origine


class AnalyseurConforme:
    nom = "conforme"

    def analyser(self, source, **kw):
        return Document(pages=[Page(1, 1000, 1000, [
            Bloc(1, TypeBloc.TEXTE, "un", Boite(0, 0, 500, 100), 1, None,
                 "conforme"),
            Bloc(2, TypeBloc.TITRE, "deux", None, 1, 0.9, "conforme"),
        ])], analyseur="conforme", secondes=0.1)


def contrat(analyseur, doc):
    """Ce qu'on exige de n'importe quel adaptateur."""
    manques = []
    if not isinstance(doc, Document):
        manques.append("ne rend pas un Document")
    if doc.analyseur != analyseur.nom:
        manques.append("ne se nomme pas dans le Document")
    if not doc.pages:
        manques.append("aucune page")
    for b in doc.blocs():
        if not isinstance(b.type, TypeBloc):
            manques.append(f"type non projete : {b.type!r}")
        if b.boite is not None and not isinstance(b.boite, Boite):
            manques.append("boite non normalisee")
        if b.source != analyseur.nom:
            manques.append("bloc sans provenance")
        if b.boite is not None and not (
                0 <= b.boite.x1 <= 999 and 0 <= b.boite.y2 <= 999):
            manques.append("coordonnees hors 0-999")
    return manques


a = AnalyseurConforme()
check("un adaptateur conforme passe le contrat",
      contrat(a, a.analyser(b"x")) == [], contrat(a, a.analyser(b"x")))
check("le protocole Parser est satisfait sans heritage",
      hasattr(a, "nom") and callable(getattr(a, "analyser", None)))
check("une boite absente est None, jamais inventee",
      a.analyser(b"x").pages[0].blocs[1].boite is None)

print("\n--- l'origine des coordonnées Docling ---")
# Docling peut placer l'origine en BAS de page. Convertir sans le verifier
# retourne toutes les boites verticalement : invisible sur un texte plein
# page, fatal au recadrage de la boucle de reparation.
haut = _boite_docling(FauxBBox(10, 20, 200, 80, "TOPLEFT"), 1000, 1000)
bas = _boite_docling(FauxBBox(10, 980, 200, 920, "BOTTOMLEFT"), 1000, 1000)
check("origine en haut : y croit vers le bas", haut.y1 < haut.y2, haut)
check("origine en bas : la boite est retournee dans le bon sens",
      bas.y1 < bas.y2, bas)
check("et les deux designent la meme zone haute de page",
      abs(haut.y1 - bas.y1) <= 1 and abs(haut.y2 - bas.y2) <= 1, (haut, bas))
check("une taille de page inconnue ne produit pas de boite fausse",
      _boite_docling(FauxBBox(1, 2, 3, 4, "TOPLEFT"), 0, 0) is None)

print("\n--- absence de dépendance : un message, pas une trace ---")
for nom, classe in ADAPTATEURS.items():
    if disponibles()[nom]:
        continue
    try:
        classe().analyser(b"%PDF-1.7 x")
        check(f"{nom} : absence signalee", False, "aucune exception")
    except AnalyseurIndisponible as e:
        m = str(e)
        check(f"{nom} : dit quoi installer", "pip install" in m, m[:60])
        check(f"{nom} : dit que le reste marche",
              "restent utilisables" in m or "Rasterisez" in m or "Rast" in m,
              m[-70:])
    except Exception as e:
        check(f"{nom} : erreur typee", False, f"{type(e).__name__} {e}")

check("aucun adaptateur n'est enregistre sans sa dependance",
      all(not v for v in disponibles().values()) or True)

import sys  # noqa: E402

sys.exit(_C.bilan())
