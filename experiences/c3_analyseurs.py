# -*- coding: utf-8 -*-
"""Expérience C3 — le plancher de risque tient-il quand on change d'analyseur ?

L'AFFIRMATION MISE À L'ÉPREUVE
  RECHERCHE.md §3.7, C3 : « Le plancher de risque est fixé par les
  vérificateurs, pas par l'analyseur. » Substituer Docling, Marker, MinerU ou
  ARACANA doit déplacer la COUVERTURE — un meilleur analyseur lit plus de
  documents correctement — sans dégrader le RISQUE à couverture donnée.

  Réfutation : si le risque à couverture fixée varie fortement d'un analyseur
  à l'autre, C3 est fausse. Ce script est fait pour pouvoir le montrer.

DEUX ÉTAPES, ET C'EST UNE NÉCESSITÉ TECHNIQUE
  Docling, Marker et MinerU tirent chacun des piles lourdes — torch,
  transformers, opencv — à des versions qui s'excluent mutuellement. Les
  installer ensemble échoue, ou pire, réussit et produit une pile bancale dont
  on n'apprend rien.

      1. COLLECTE — un analyseur, un environnement, un fichier JSON.
         python c3_analyseurs.py collecter --analyseur docling --corpus …

      2. COMPARAISON — lit les JSON, ne dépend d'aucun analyseur.
         python c3_analyseurs.py comparer relevés/*.json

  Ce découpage n'est pas une commodité : c'est ce qui rend l'expérience
  reproductible par un tiers qui n'a pas notre machine.

LA VÉRITÉ TERRAIN EST GRATUITE, ET C'EST LE POINT MÉTHODOLOGIQUE CENTRAL
  Chaque Factur-X du corpus de conformité contient un XML normatif ET une page
  rendue. L'analyseur lit la page ; le XML donne les champs attendus. Aucune
  annotation manuelle, aucun étiquetage, aucun coût.

  Objection légitime : et si la page diffère réellement du XML ? C'est
  précisément l'objet de C2, et cela ne fausse pas C3 — un système qui lit la
  page fidèlement alors qu'elle contredit le XML doit S'ABSTENIR, ce que fait
  le vérificateur de réconciliation. Il compte alors en « détecté », pas en
  « faux accepté ». La métrique reste juste.

CE QUE CE SCRIPT NE FAIT PAS
  Il ne conclut pas à votre place. Il produit des courbes, des AURC et des
  intervalles ; l'interprétation est dans le rapport, avec ses réserves.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "src"))

from aracana.decision import decider  # noqa: E402
from aracana.detect import diagnostiquer  # noqa: E402
from aracana.extract import extraire_champs  # noqa: E402
from aracana.policy import Decision, Issue, Politique, echelle  # noqa: E402
from aracana.reconcile import lire_xml  # noqa: E402
from aracana.riskcov import (Cas, bootstrap_apparie, comparer_analyseurs,  # noqa: E402
                             cout_total, optimum_economique)

#: Les champs sur lesquels le risque est mesuré. Pas la transcription entière :
#: c'est exactement la critique adressée à OmniDocBench (RECHERCHE.md §2.2).
#: Un nom mal accentué ne coûte rien ; un montant faux coûte une écriture.
CHAMPS_DECISIFS = ("invoice_number", "invoice_date", "total_excl_vat",
                   "vat_amount", "total_incl_vat", "currency")


# ═══════════════════════════════════════════════════════════════ collecte

def verite_terrain(octets: bytes) -> dict[str, Any] | None:
    """Les champs attendus, lus dans le XML embarqué.

    Rend `None` si le document ne porte pas de XML : il ne peut alors pas
    servir à cette expérience, et l'inclure avec une vérité vide gonflerait
    artificiellement le risque de tous les analyseurs à la fois.
    """
    d = diagnostiquer(octets)
    if not d.xml:
        return None
    try:
        x = lire_xml(d.xml)
    except Exception:
        return None
    return {k: v for k, v in x.items()
            if k in CHAMPS_DECISIFS and v is not None}


def collecter(analyseur, corpus: Path, sortie: Path,
              url: str | None = None, cle: str | None = None) -> dict:
    """Lit tout le corpus avec UN analyseur et dépose le relevé.

    Le relevé contient, par document : la vérité terrain, les champs extraits,
    les verdicts, et le temps. Il ne contient AUCUN texte de document — un
    relevé partageable ne doit pas emporter les factures.
    """
    fichiers = sorted(p for p in corpus.iterdir()
                      if p.suffix.lower() == ".pdf")
    if not fichiers:
        sys.exit(f"{corpus} : aucun PDF.")

    ocr = _ocr_de(analyseur, url, cle)
    releves, ignores = [], []
    t_debut = time.time()

    for i, f in enumerate(fichiers, 1):
        octets = f.read_bytes()
        verite = verite_terrain(octets)
        if not verite:
            ignores.append(f.name)
            print(f"  [{i}/{len(fichiers)}] {f.name} — ignoré : pas de XML "
                  f"embarqué, donc pas de vérité terrain")
            continue

        t0 = time.time()
        try:
            plats, controles, route = _lire_la_page(octets, analyseur, ocr)
        except Exception as e:  # noqa: BLE001
            # Un analyseur qui casse sur un document est une donnée, pas un
            # incident : on l'enregistre comme un échec de lecture plutôt que
            # d'interrompre la collecte et de perdre le reste.
            print(f"  [{i}/{len(fichiers)}] {f.name} — ÉCHEC "
                  f"{type(e).__name__}: {str(e)[:70]}")
            releves.append({"document": f.name, "verite": verite,
                            "champs": {}, "controles": [],
                            "erreur": f"{type(e).__name__}: {e}"[:200],
                            "secondes": round(time.time() - t0, 2)})
            continue
        justes = sum(1 for c in CHAMPS_DECISIFS
                     if c in verite and _egal(plats.get(c), verite[c]))
        echoues = sum(1 for c in controles if not c["passe"])
        print(f"  [{i}/{len(fichiers)}] {f.name[:38]:40s} "
              f"{justes}/{len(verite)} champs justes · "
              f"{len(controles)} contrôles ({echoues} en échec) · "
              f"{time.time() - t0:.1f}s")

        releves.append({
            "document": f.name,
            "verite": {k: str(v) for k, v in verite.items()},
            "champs": {k: (str(v) if v is not None else None)
                       for k, v in plats.items() if k in CHAMPS_DECISIFS},
            "controles": controles,
            "route": route,
            "secondes": round(time.time() - t0, 2),
        })

    releve = {
        "analyseur": getattr(analyseur, "nom", None) or "sans-lecture",
        "corpus": corpus.name,
        "documents": len(releves),
        "ignores": ignores,
        "champs_decisifs": list(CHAMPS_DECISIFS),
        "secondes_total": round(time.time() - t_debut, 1),
        "releves": releves,
    }
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps(releve, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"\n  {len(releves)} document(s) relevés, {len(ignores)} ignorés")
    print(f"  -> {sortie}")
    return releve


def _lire_la_page(octets: bytes, analyseur, ocr) -> tuple[dict, list, str]:
    """Extrait les champs DE LA PAGE, jamais du XML — et voici pourquoi.

    `decider()` traite le XML embarqué comme normatif : sur un Factur-X, il en
    tire les champs et n'utilise la lecture de page que pour réconcilier.
    C'est le bon comportement en production — le XML est la source légale.

    Mais pour CETTE expérience, ce serait circulaire : la vérité terrain vient
    du XML, et l'on comparerait le XML à lui-même. La première version de ce
    script donnait 6/6 champs justes sur les dix documents, pour tous les
    analyseurs, y compris le témoin sans aucune lecture. Un résultat parfait
    qui ne mesurait rien.

    Ici, on force donc la lecture de la PAGE et l'on extrait de son texte. Le
    XML ne sert plus qu'à dire ce qui était attendu. C'est bien la question de
    C3 : l'analyseur sait-il lire la page, et les vérificateurs rattrapent-ils
    ses erreurs indépendamment de qui lit ?
    """
    from aracana.countries import deviner, pour

    if ocr is None:
        # Témoin dégénéré, conservé pour une raison : il montre ce que valent
        # les contrôles quand AUCUNE page n'est lue. Ses champs sont vides,
        # donc son risque est nul et sa couverture aussi. C'est le point
        # (0, 0) de la courbe, et il rappelle que la couverture se gagne en
        # lisant.
        return {}, [], "sans_lecture"

    r = ocr(octets)
    texte = r.get("text", "")
    champs = extraire_champs(texte)
    plats = {k: (v.get("value") if isinstance(v, dict) else v)
             for k, v in champs.items()}

    # Les MÊMES vérificateurs pour tous les analyseurs : c'est l'exigence
    # méthodologique de C3. On les applique aux champs lus sur la page.
    pack = pour(deviner(texte))
    controles = []
    for c in pack.valider(plats):
        controles.append({"nom": c.nom, "passe": c.passe,
                          "bloquant": c.bloquant})
    if (c := pack.controle_iban(plats)):
        controles.append({"nom": c.nom, "passe": c.passe,
                          "bloquant": c.bloquant})
    t = plats.get("vat_rate")
    if t is not None and (c := pack.taux_legal(_dec(t))):
        controles.append({"nom": c.nom, "passe": c.passe,
                          "bloquant": c.bloquant})

    ht, tva, ttc = (plats.get(k) for k in
                    ("total_excl_vat", "vat_amount", "total_incl_vat"))
    if None not in (ht, tva, ttc):
        ecart = abs(_dec(ht) + _dec(tva) - _dec(ttc))
        controles.append({"nom": "Totals balance", "passe": ecart <= 0.02,
                          "bloquant": True})

    # La réconciliation XML ↔ page : le vérificateur qui distingue ce
    # framework de tout système mono-vue (C2). Il s'applique ici comme en
    # production, sur les champs lus.
    diag = diagnostiquer(octets)
    if diag.xml:
        from aracana.reconcile import reconcilier
        rec = reconcilier(diag.xml, champs)
        for e in rec.ecarts:
            controles.append({"nom": f"XML vs page · {e.champ}", "passe": False,
                              "bloquant": e.gravite == "blocking"})
        if not rec.ecarts and rec.compares:
            controles.append({"nom": "XML vs page", "passe": True,
                              "bloquant": True})

    return plats, controles, diag.route.value


def _dec(v):
    from decimal import Decimal, InvalidOperation
    try:
        return Decimal(str(v).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _ocr_de(analyseur, url: str | None, cle: str | None):
    """Adapte un `Parser` en fonction OCR pour `decider()`.

    `decider` attend `{"text": …, "pages": [...]}` ; un analyseur rend un
    `Document`. La conversion est ici, une fois, plutôt que dans chaque
    adaptateur — ce n'est pas leur rôle.
    """
    if analyseur is None:
        return None

    def lire(source: bytes):
        doc = analyseur.analyser(source)
        return {"text": doc.texte,
                "pages": [{"page": p.numero, "blocks": []} for p in doc.pages]}
    return lire


def _egal(a: Any, b: Any) -> bool:
    from aracana.riskcov import _equivalent
    return _equivalent(a, b)


# ═════════════════════════════════════════════════════════════ comparaison

def _cas_depuis(releve: dict) -> list[Cas]:
    """Reconstruit les cas évaluables depuis un relevé, sans rien relire."""
    cas = []
    for r in releve["releves"]:
        # On rejoue la politique sur les verdicts figés. Le document n'est pas
        # relu : c'est ce qui rend le balayage instantané ET garantit que
        # SEULE la politique varie (exigence de Traub et al., NeurIPS 2024).
        cas.append(Cas(
            identifiant=r["document"],
            champs=r["champs"],
            verite=r["verite"],
            decision=Decision(Issue.REVUE, "", [], [])))
        cas[-1].__dict__["_controles"] = r["controles"]
        cas[-1].__dict__["_erreur"] = r.get("erreur")
    return cas


def _rejouer(cas: Cas, politique: Politique) -> Decision:
    """Applique une politique aux verdicts figés d'un cas."""
    controles = cas.__dict__.get("_controles", [])
    if cas.__dict__.get("_erreur"):
        return Decision(Issue.REJET, "lecture impossible", [], ["erreur"],
                        politique.nom)

    motifs = []
    manquants = [c for c in politique.champs_requis
                 if not cas.champs.get(c)]
    if manquants:
        motifs.append(f"champs absents : {', '.join(manquants)}")
    echecs = [c for c in controles if not c["passe"]]
    motifs += [c["nom"] for c in echecs
               if c["bloquant"] or not politique.tolere_avertissements]
    if len(controles) < politique.min_controles:
        motifs.append(f"{len(controles)} contrôle(s) seulement")

    if motifs:
        return Decision(Issue.REVUE, motifs[0], [], motifs, politique.nom)
    return Decision(Issue.ACCEPTE, f"{len(controles)} contrôles passés", [],
                    [], politique.nom)


def comparer(chemins: list[Path], cout_erreur: float, cout_revue: float,
             epsilon: float) -> int:
    releves = []
    for c in chemins:
        try:
            releves.append(json.loads(c.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            sys.exit(f"{c} : illisible ({e})")

    if len(releves) < 2:
        print("\n  UN SEUL ANALYSEUR RELEVÉ.\n")
        print("  C3 affirme que le plancher de risque ne dépend PAS de")
        print("  l'analyseur. Avec un seul, l'affirmation n'est ni vérifiée ni")
        print("  réfutée — elle n'est pas testée. Collectez-en au moins deux :")
        print()
        print("      python c3_analyseurs.py collecter --analyseur docling …")
        print("      python c3_analyseurs.py collecter --analyseur tesseract …")
        print()
        print("  Publier une courbe unique en la présentant comme une")
        print("  validation de C3 serait une faute.")
        return 1

    # Les analyseurs doivent avoir vu LES MÊMES documents. Comparer des
    # courbes tracées sur des sous-ensembles différents ne mesure rien.
    ensembles = [{r["document"] for r in rv["releves"]} for rv in releves]
    communs = set.intersection(*ensembles)
    for rv, ens in zip(releves, ensembles):
        if ens - communs:
            print(f"  ! {rv['analyseur']} : {len(ens - communs)} document(s) "
                  f"hors intersection, écartés pour garder la comparaison "
                  f"appariée")
    if not communs:
        sys.exit("Aucun document commun : la comparaison serait vide.")

    jeux = {}
    for rv in releves:
        rv2 = dict(rv, releves=[r for r in rv["releves"]
                                if r["document"] in communs])
        jeux[rv["analyseur"]] = _cas_depuis(rv2)

    print(f"\n{'=' * 74}")
    print(f"EXPÉRIENCE C3 — {len(communs)} documents communs, "
          f"{len(jeux)} analyseur(s)")
    print(f"{'=' * 74}")

    courbes = comparer_analyseurs(
        jeux, _rejouer, politiques=echelle(),
        champs_decisifs=CHAMPS_DECISIFS)

    for nom, cb in courbes.items():
        print(f"\n{cb}")

    # ------------------------------------------------------- le verdict
    print(f"\n{'=' * 74}")
    print("CE QUE CELA DIT DE C3")
    print(f"{'=' * 74}\n")

    print(f"  {'analyseur':16s} {'AURC':>8s} {'c@r≤' + f'{epsilon:.1%}':>12s} "
          f"{'risque là':>10s} {'coût/doc':>10s}")
    print(f"  {'-' * 60}")
    lignes = []
    for nom, cb in courbes.items():
        pt = cb.couverture_a_risque(epsilon)
        opt = optimum_economique(cb, cout_erreur=cout_erreur,
                                 cout_revue=cout_revue)
        lignes.append((nom, cb.aurc, pt, opt))
        print(f"  {nom:16s} {cb.aurc:8.4f} "
              f"{(f'{pt.couverture:.1%}' if pt else '—'):>12s} "
              f"{(f'{pt.risque:.2%}' if pt else '—'):>10s} "
              f"{(f'{opt[1]:.2f} €' if opt else '—'):>10s}")

    # Le test de C3 : l'écart de RISQUE à couverture comparable.
    print()
    couvertures = [p.couverture for _, _, p, _ in lignes if p]
    risques = [p.risque for _, _, p, _ in lignes if p]

    # ── LE GARDE-FOU QUI MANQUAIT ────────────────────────────────────────
    # Première version : quand toutes les couvertures valent zéro, les deux
    # étendues valent zéro, le test `etendue_c > etendue_r * 2` est faux, et
    # le script concluait « NON compatible avec C3 ». Un verdict de réfutation
    # tiré de zéro donnée — la faute la plus grave qu'un harnais de mesure
    # puisse commettre, parce qu'elle est plausible et publiable.
    #
    # Une courbe dégénérée ne réfute rien. Elle dit que l'expérience n'a pas
    # eu lieu, et pourquoi.
    couverture_max = max(couvertures) if couvertures else 0.0
    if couverture_max <= 0:
        print("  ═══ EXPÉRIENCE NON CONCLUANTE — ET CE N'EST PAS UNE ═══")
        print("  ═══ RÉFUTATION DE C3                              ═══\n")
        print("  Aucun document n'est accepté par aucune politique, avec aucun")
        print("  analyseur. La courbe est réduite au point (0, 0) : il n'y a")
        print("  rien à comparer, donc rien à conclure.")
        print()
        _diagnostiquer_couverture_nulle(jeux)
        return 2

    if len(risques) < 2:
        print("  Un seul point exploitable : pas de comparaison possible.")
        return 2

    etendue_r = max(risques) - min(risques)
    etendue_c = max(couvertures) - min(couvertures)
    print(f"  Étendue du risque à c@r≤{epsilon:.1%} : "
          f"{etendue_r:.2%} ({min(risques):.2%} → {max(risques):.2%})")
    print(f"  Étendue de la couverture           : "
          f"{etendue_c:.1%} ({min(couvertures):.1%} → {max(couvertures):.1%})")
    print()
    if etendue_c == 0 and etendue_r == 0:
        print("  → Les analyseurs sont indiscernables sur ce corpus. Compatible")
        print("    avec C3, mais sans force : un corpus qui ne sépare rien ne")
        print("    départage rien.")
    elif etendue_c > etendue_r * 2:
        print("  → Compatible avec C3 : changer d'analyseur déplace surtout")
        print("    la COUVERTURE, peu le RISQUE. Le plancher est porté par")
        print("    les vérificateurs.")
    else:
        print("  → NON compatible avec C3 sur ce corpus : le risque varie")
        print("    autant que la couverture. Le plancher dépend donc aussi")
        print("    de l'analyseur, et l'affirmation doit être révisée.")
    print()
    print("  Réserve : cette lecture porte sur un point de la courbe. Une")
    print("  conclusion publiable exige les intervalles ci-dessous ET un")
    print("  corpus de plusieurs centaines de documents.")

    # ------------------------------------------------------- intervalles
    noms = list(jeux)
    if len(noms) >= 2:
        print(f"\n{'=' * 74}")
        print("ÉCARTS DE RISQUE, PAR BOOTSTRAP APPARIÉ (95 %)")
        print(f"{'=' * 74}")
        print("  Apparié : les mêmes documents des deux côtés. Sur quelques")
        print("  centaines de pièces, un test non apparié conclurait au hasard.\n")
        base = noms[0]
        p_stricte = echelle()[-1]
        for autre in noms[1:]:
            a = [_avec(c, p_stricte) for c in jeux[base]]
            b = [_avec(c, p_stricte) for c in jeux[autre]]
            med, bas, haut = bootstrap_apparie(
                a, b, champs_decisifs=CHAMPS_DECISIFS, tirages=5000)
            zero = bas <= 0 <= haut
            print(f"  {base} − {autre:14s} : {med:+.3%} "
                  f"[{bas:+.3%}, {haut:+.3%}]"
                  + ("   (contient zéro : on ne peut pas les départager)"
                     if zero else "   (écart significatif)"))

    print(f"\n{'=' * 74}")
    print("  Protocole : framework/RECHERCHE.md §4.4")
    print(f"{'=' * 74}")
    return 0


def _diagnostiquer_couverture_nulle(jeux: dict[str, list[Cas]]) -> None:
    """Pourquoi rien ne passe. Sans cela, on ne sait pas quoi corriger.

    Un harnais qui annonce « couverture nulle » et s'arrête laisse
    l'expérimentateur deviner. Il doit dire QUEL contrôle bloque, et combien
    de fois — c'est la même exigence de retour diagnostique que celle qu'on
    impose aux vérificateurs eux-mêmes.
    """
    from collections import Counter

    print("  POURQUOI RIEN NE PASSE\n")
    for nom, cas in jeux.items():
        motifs: Counter = Counter()
        absents: Counter = Counter()
        for c in cas:
            for ctrl in c.__dict__.get("_controles", []):
                if not ctrl["passe"] and ctrl["bloquant"]:
                    motifs[ctrl["nom"]] += 1
            for champ in ("invoice_number", "invoice_date", "total_incl_vat"):
                if not c.champs.get(champ):
                    absents[champ] += 1
            if not c.__dict__.get("_controles"):
                motifs["(aucun contrôle applicable)"] += 1

        print(f"  · {nom}  ({len(cas)} documents)")
        for quoi, n in motifs.most_common(5):
            print(f"      {n:3d}×  contrôle bloquant en échec : {quoi[:52]}")
        for quoi, n in absents.most_common(3):
            print(f"      {n:3d}×  champ obligatoire absent   : {quoi}")
        print()

    print("  CE QUE CELA SIGNIFIE, ET CE QU'IL FAUT FAIRE\n")
    print("  Si le motif dominant est un contrôle d'identifiant (Luhn, clé TVA,")
    print("  IDE), c'est probablement le corpus et non le système : les exemples")
    print("  de conformité du FNFE portent des SIREN et SIRET FICTIFS, qui")
    print("  échouent légitimement. Le système a raison ; le corpus n'est pas")
    print("  fait pour mesurer une couverture.")
    print()
    print("  Deux issues, toutes deux honnêtes :")
    print("    1. Mesurer sur un corpus à identifiants réels — c'est la voie")
    print("       pour une publication.")
    print("    2. Mesurer avec un registre de vérificateurs qui exclut les")
    print("       contrôles d'identité, en le DISANT dans le résultat. On")
    print("       mesure alors la lecture, pas l'identité. C'est légitime et")
    print("       cela doit figurer sur la courbe.")
    print()
    print("  Ce qu'il ne faut PAS faire : desserrer un contrôle pour obtenir")
    print("  une courbe. Ce serait mesurer la complaisance du système.")


def _avec(cas: Cas, politique: Politique) -> Cas:
    """Copie d'un cas, décidé sous une politique donnée."""
    c2 = Cas(cas.identifiant, cas.champs, cas.verite, _rejouer(cas, politique))
    c2.__dict__["_controles"] = cas.__dict__.get("_controles", [])
    c2.__dict__["_erreur"] = cas.__dict__.get("_erreur")
    return c2


# ══════════════════════════════════════════════════════════════════ main

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sous = p.add_subparsers(dest="commande", required=True)

    c = sous.add_parser("collecter", help="un analyseur, un relevé JSON")
    c.add_argument("--analyseur", required=True,
                   help="docling | marker | mineru | tesseract | api | aucun")
    c.add_argument("--corpus", default=str(RACINE.parent / "tests" / "corpus_reel"))
    c.add_argument("--sortie")
    c.add_argument("--url", help="pour --analyseur api")
    c.add_argument("--cle")

    k = sous.add_parser("comparer", help="lit des relevés, trace les courbes")
    k.add_argument("releves", nargs="+")
    k.add_argument("--cout-erreur", type=float, default=200.0)
    k.add_argument("--cout-revue", type=float, default=2.0)
    k.add_argument("--epsilon", type=float, default=0.01)

    d = sous.add_parser("disponibles", help="quels analyseurs tournent ici")
    a = p.parse_args()

    if a.commande == "disponibles":
        from aracana.parsers import disponibles
        etat = disponibles()
        print("Analyseurs installés dans CET environnement :\n")
        for nom, present in etat.items():
            print(f"  [{'oui' if present else 'non'}] {nom}")
        n = sum(etat.values())
        print()
        if n >= 2:
            print(f"  {n} analyseurs : C3 est testable ici.")
        else:
            print(f"  {n} analyseur(s). C3 exige au moins deux relevés.")
            print("  Leurs dépendances entrent en conflit : installez-les dans")
            print("  des environnements SÉPARÉS, collectez dans chacun, puis")
            print("  comparez les JSON — la comparaison ne dépend d'aucun d'eux.")
        return 0

    if a.commande == "collecter":
        analyseur = _construire(a.analyseur, a.url, a.cle)
        sortie = Path(a.sortie or (RACINE / "experiences" / "releves" /
                                   f"{a.analyseur}.json"))
        print(f"Collecte avec « {a.analyseur} » sur {a.corpus}\n")
        collecter(analyseur, Path(a.corpus), sortie, a.url, a.cle)
        return 0

    return comparer([Path(x) for x in a.releves], a.cout_erreur,
                    a.cout_revue, a.epsilon)


def _construire(nom: str, url: str | None, cle: str | None):
    from aracana.parsers import ADAPTATEURS, ApiParser, AnalyseurIndisponible
    if nom == "aucun":
        # Sert de témoin : la chaîne sans aucune lecture de page. Sur un
        # Factur-X, le XML suffit à remplir les champs — ce point de référence
        # montre ce que l'analyseur AJOUTE, et il n'est pas nul.
        return None
    if nom == "api":
        if not url:
            sys.exit("--analyseur api exige --url")
        return ApiParser(url, cle)
    if nom not in ADAPTATEURS:
        sys.exit(f"Analyseur inconnu : {nom}. "
                 f"Connus : {', '.join(sorted(ADAPTATEURS))}, api, aucun.")
    try:
        return ADAPTATEURS[nom]()
    except AnalyseurIndisponible as e:
        sys.exit(str(e))


if __name__ == "__main__":
    sys.exit(main())
