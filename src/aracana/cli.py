"""Ligne de commande — la chaîne complète, sans écrire une ligne de Python.

    aracana trier facture.pdf              quelle route, faut-il un modèle ?
    aracana lire facture.pdf               lecture par un analyseur
    aracana verifier facture.pdf           lecture + contrôles + décision
    aracana lot ./factures --fec sortie/   traitement de masse, trois sorties
    aracana mesurer ./corpus               courbe risque–couverture
    aracana extensions                     ce qui est branché
    aracana pro                            ce qui est ouvert, ce qui est payant

POURQUOI UNE CLI DANS UN FRAMEWORK
  Parce que la première question d'un développeur qui évalue un outil n'est
  pas « comment l'intégrer », c'est « est-ce que ça marche sur MON document ».
  Une réponse en une commande, sans écrire de code, décide de l'adoption.

  Et parce qu'elle sert en production : `aracana lot` sur un dossier surveillé
  est un déploiement complet, sans serveur.

CE QUI Y EST VISIBLE, ET C'EST VOULU
  Chaque commande affiche **ce qu'elle n'a pas pu faire** : contrôles non
  applicables, champs absents, appels au modèle évités. Un outil qui ne montre
  que ses succès apprend à son utilisateur à lui faire confiance sans raison.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

VERT, JAUNE, ROUGE, GRIS, GRAS, FIN = (
    "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[1m", "\033[0m")


def _sans_couleur() -> None:
    global VERT, JAUNE, ROUGE, GRIS, GRAS, FIN
    VERT = JAUNE = ROUGE = GRIS = GRAS = FIN = ""


def _lire(chemin: str) -> bytes:
    p = Path(chemin)
    if not p.exists():
        sys.exit(f"{chemin} : fichier introuvable.")
    return p.read_bytes()


# ------------------------------------------------------------------ trier

def cmd_trier(a) -> int:
    from .detect import diagnostiquer
    for chemin in a.fichiers:
        d = diagnostiquer(_lire(chemin))
        gpu = (f"{ROUGE}appel modèle requis{FIN}" if d.besoin_modele
               else f"{VERT}sans modèle{FIN}")
        print(f"\n{GRAS}{Path(chemin).name}{FIN}")
        print(f"  route      {d.route.value}")
        print(f"  format     {d.format}" + (f" · profil {d.profil}" if d.profil else ""))
        print(f"  pages      {d.pages if d.pages is not None else '?'}")
        print(f"  coût       {gpu}")
        print(f"  {GRIS}{d.raison}{FIN}")
        for av in d.avertissements:
            print(f"  {JAUNE}! {av}{FIN}")
    return 0


# ------------------------------------------------------------------- lire

def _analyseur(nom: str):
    from . import plugins
    if nom == "auto":
        dispo = [e.nom for e in plugins.lister("parsers")]
        if not dispo:
            sys.exit(
                "Aucun analyseur disponible.\n"
                "  Le cœur du framework ne lit pas les images : il oriente,\n"
                "  contrôle et décide. Branchez-en un :\n"
                "      pip install aracana-ocr[docling]      ou  [marker], [mineru]\n"
                "  ou pointez le service ARACANA :  --analyseur api --url …")
        nom = dispo[0]
    return plugins.obtenir("parsers", nom)


def cmd_lire(a) -> int:
    analyseur = _analyseur(a.analyseur)
    for chemin in a.fichiers:
        doc = analyseur.analyser(_lire(chemin))
        if a.json:
            print(json.dumps(doc.dict(), ensure_ascii=False, indent=2))
            continue
        print(f"\n{GRAS}{Path(chemin).name}{FIN}  "
              f"{GRIS}{doc.analyseur} · {doc.secondes:.1f}s{FIN}")
        for p in doc.pages:
            print(f"  page {p.numero} — {len(p.blocs)} bloc(s)")
            for b in p.blocs[:a.limite]:
                extrait = b.texte.replace("\n", " ")[:76]
                print(f"    {b.ordre:3d}. {b.type.value:9s} {extrait}")
            if len(p.blocs) > a.limite:
                print(f"    {GRIS}… {len(p.blocs) - a.limite} de plus "
                      f"(--limite pour en voir davantage){FIN}")
    return 0


# --------------------------------------------------------------- verifier

def cmd_verifier(a) -> int:
    from .decision import decider
    from .extract import extraire_champs

    ocr = None
    if a.url:
        ocr = _ocr_par_api(a.url, a.cle)

    code = 0
    for chemin in a.fichiers:
        d = decider(_lire(chemin), ocr=ocr, extraire=extraire_champs,
                    pays_force=a.pays)
        couleur = {"auto_post": VERT, "human_review": JAUNE,
                   "rejected": ROUGE}[d.issue.value]
        etat = {"auto_post": "AUTOMATISABLE", "human_review": "REVUE HUMAINE",
                "rejected": "REJETÉ"}[d.issue.value]
        print(f"\n{couleur}{GRAS}{etat}{FIN}  {Path(chemin).name}")
        print(f"  {GRIS}{d.justification()}{FIN}")
        print(f"  route {d.route.value} · "
              f"{'modèle appelé' if d.appel_modele else 'sans modèle'} · "
              f"{d.secondes:.2f}s" + (f" · {d.pays}" if d.pays else ""))

        lus = {k: (v.get("value") if isinstance(v, dict) else v)
               for k, v in d.champs.items()
               if (v.get("found") if isinstance(v, dict) else v is not None)}
        if lus:
            print(f"\n  {GRAS}Champs lus{FIN}")
            for k, v in lus.items():
                print(f"    {k:18s} {v}")

        if d.controles:
            print(f"\n  {GRAS}Contrôles{FIN}")
            for c in d.controles:
                m = f"{VERT}✓{FIN}" if c["passed"] else f"{ROUGE}✗{FIN}"
                bloc = "" if c.get("blocking", True) else f" {GRIS}(non bloquant){FIN}"
                print(f"    {m} {c['check']}{bloc}")
                if not c["passed"] and c.get("detail"):
                    print(f"        {JAUNE}{c['detail'][:90]}{FIN}")
        else:
            # Le dire explicitement : « aucun contrôle » n'est pas « tout va bien ».
            print(f"\n  {JAUNE}Aucun contrôle applicable — ce document n'a pas "
                  f"été vérifié, il a seulement été lu.{FIN}")

        if d.reconciliation and not d.reconciliation.concordant:
            print(f"\n  {GRAS}XML contre page{FIN}")
            for e in d.reconciliation.ecarts:
                print(f"    {ROUGE}{e.champ}{FIN} : XML {e.xml} / page {e.page}")

        if d.issue.value != "auto_post":
            code = 1
    return code


def _ocr_par_api(url: str, cle: str | None):
    import base64
    import mimetypes
    import os
    import tempfile
    import urllib.request

    def lire(source: bytes):
        limite = "----aracana" + base64.urlsafe_b64encode(
            os.urandom(9)).decode().strip("=")
        nom = "page.pdf" if source[:5] == b"%PDF-" else "page.png"
        mime = mimetypes.guess_type(nom)[0] or "application/octet-stream"
        corps = (f"--{limite}\r\n".encode()
                 + f'Content-Disposition: form-data; name="files"; '
                   f'filename="{nom}"\r\n'.encode()
                 + f"Content-Type: {mime}\r\n\r\n".encode() + source + b"\r\n"
                 + f"--{limite}--\r\n".encode())
        req = urllib.request.Request(
            url.rstrip("/") + "/v1/ocr", data=corps,
            headers={"Content-Type": f"multipart/form-data; boundary={limite}",
                     "User-Agent": "aracana-cli/1.0",
                     **({"Authorization": f"Bearer {cle}"} if cle else {})})
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read())
        return {"text": d.get("text", ""), "pages": d.get("pages", [])}
    return lire


# ------------------------------------------------------------------- lot

def cmd_lot(a) -> int:
    from .batch import ecrire_sorties, traiter_lot
    from .extract import extraire_champs

    dossier = Path(a.dossier)
    if not dossier.is_dir():
        sys.exit(f"{a.dossier} : ce n'est pas un dossier.")
    fichiers = sorted(p for p in dossier.iterdir() if p.is_file())
    if not fichiers:
        sys.exit(f"{a.dossier} : aucun fichier.")

    ocr = _ocr_par_api(a.url, a.cle) if a.url else None
    print(f"{len(fichiers)} document(s)…")
    lot = traiter_lot([(f.name, f.read_bytes()) for f in fichiers],
                      ocr=ocr, extraire=extraire_champs, pays_force=a.pays)

    for e in lot.entrees:
        c = {"auto_post": VERT, "human_review": JAUNE,
             "rejected": ROUGE}[e.decision.issue.value]
        marque = "✓" if e.comptabilise else "·"
        print(f"  {c}{marque}{FIN} {e.nom[:46]:48s} {e.decision.issue.value}")

    print()
    for ligne in lot.resume().split("\n"):
        print(f"  {ligne}")

    if a.fec:
        produits = ecrire_sorties(lot, a.fec, siren=a.siren, cloture=a.cloture)
        print()
        for quoi, chemin in produits.items():
            print(f"  {quoi:8s} {chemin}")
    return 0


# --------------------------------------------------------------- mesurer

def cmd_mesurer(a) -> int:
    print(f"{GRAS}Courbe risque–couverture{FIN}\n")
    print("  Cette commande exige une vérité terrain. Deux sources possibles :")
    print()
    print("    1. Un corpus Factur-X — le XML embarqué DONNE les champs, la")
    print("       page donne l'entrée. Vérité terrain gratuite, aucun")
    print("       étiquetage. C'est la voie recommandée.")
    print("    2. Un fichier d'annotations JSON, une entrée par document.")
    print()
    print(f"  {GRIS}Voir framework/RECHERCHE.md §4.4 pour le protocole, et")
    print(f"  aracana.riskcov pour l'API. L'automatisation complète de cette")
    print(f"  commande arrive avec le jeu d'évaluation public.{FIN}")
    return 0


# ------------------------------------------------------------ extensions

def cmd_extensions(a) -> int:
    from . import plugins
    print(plugins.etat())
    print()
    print(f"  {GRIS}Un paquet tiers se déclare dans son pyproject.toml :")
    print(f"      [project.entry-points.\"aracana.parsers\"]")
    print(f"      mon-analyseur = \"mon_paquet:MonAnalyseur\"{FIN}")
    return 0


def cmd_pro(a) -> int:
    from . import pro
    print(pro.etat())
    return 0


# ------------------------------------------------------------------ main

def construire() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aracana",
        description="Lecture, contrôle et décision sur documents européens.",
        epilog="Documentation : https://aracana.ai/framework")
    p.add_argument("--sans-couleur", action="store_true")
    sous = p.add_subparsers(dest="commande", required=True)

    t = sous.add_parser("trier", help="quelle route, faut-il un modèle ?")
    t.add_argument("fichiers", nargs="+")
    t.set_defaults(fn=cmd_trier)

    l = sous.add_parser("lire", help="lecture par un analyseur")
    l.add_argument("fichiers", nargs="+")
    l.add_argument("--analyseur", default="auto")
    l.add_argument("--limite", type=int, default=12)
    l.add_argument("--json", action="store_true")
    l.set_defaults(fn=cmd_lire)

    v = sous.add_parser("verifier", help="lecture, contrôles et décision")
    v.add_argument("fichiers", nargs="+")
    v.add_argument("--url", help="service OCR pour les documents à lire")
    v.add_argument("--cle")
    v.add_argument("--pays", help="forcer la juridiction : FR, CH, …")
    v.set_defaults(fn=cmd_verifier)

    b = sous.add_parser("lot", help="traiter un dossier")
    b.add_argument("dossier")
    b.add_argument("--fec", help="dossier de sortie : FEC, revue, audit")
    b.add_argument("--siren")
    b.add_argument("--cloture", help="JJ/MM/AAAA, pour nommer le FEC")
    b.add_argument("--url")
    b.add_argument("--cle")
    b.add_argument("--pays")
    b.set_defaults(fn=cmd_lot)

    m = sous.add_parser("mesurer", help="courbe risque–couverture")
    m.add_argument("corpus")
    m.set_defaults(fn=cmd_mesurer)

    e = sous.add_parser("extensions", help="ce qui est branché")
    e.set_defaults(fn=cmd_extensions)

    pr = sous.add_parser("pro", help="ouvert et commercial")
    pr.set_defaults(fn=cmd_pro)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    a = construire().parse_args(argv)
    if a.sans_couleur or not sys.stdout.isatty():
        _sans_couleur()
    try:
        return a.fn(a)
    except KeyboardInterrupt:
        return 130
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"{ROUGE}{type(e).__name__}{FIN} : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
