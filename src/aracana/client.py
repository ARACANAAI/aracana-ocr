# -*- coding: utf-8 -*-
"""SDK — client du service ARACANA OCR, bibliothèque standard uniquement.

    from aracana.client import Client

    c = Client("https://…", cle="ak_live_…")
    d = c.lire("facture.pdf")          # PDF rastérisés côté client

POURQUOI PAS `requests` NI `httpx`
  Ce module était un exemple à copier ; il est devenu une partie du paquet.
  Imposer une dépendance HTTP à quiconque installe `aracana-ocr` pour n'utiliser
  que les vérificateurs serait un coût sans contrepartie. La bibliothèque
  standard suffit, y compris pour le multipart.

CE QU'IL SAIT FAIRE ET QUI N'EST PAS ÉVIDENT
  · rastériser les PDF avant envoi — le service prend des IMAGES de page ;
  · basculer sur la route asynchrone quand un intermédiaire coupe (Cloudflare
    tranche à 100 s, une page dense demande davantage) ;
  · s'annoncer avec un User-Agent — `urllib` est rejeté d'office par la
    plupart des pare-feux applicatifs.
  Chacun de ces trois points a été découvert en conditions réelles.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAUT = os.environ.get("ARACANA_URL", "http://127.0.0.1:8000")

# Au-delà de ce nombre de pages, la route synchrone risque d'être coupée par un
# répartiteur de charge avant la fin. Le service refuse d'ailleurs les demandes
# trop lourdes en synchrone ; on bascule sur les tâches avant qu'il ait à le
# faire, plutôt que de traiter son refus comme une erreur.
SEUIL_ASYNCHRONE = 8


class ErreurApi(RuntimeError):
    def __init__(self, statut: int, corps: dict | str):
        self.statut = statut
        self.corps = corps
        code = corps.get("code") if isinstance(corps, dict) else ""
        message = corps.get("message") if isinstance(corps, dict) else str(corps)
        super().__init__(f"HTTP {statut} [{code}] {message}")


class Client:
    def __init__(self, url: str = DEFAUT, cle: str | None = None,
                 delai: int = 300):
        self.url = url.rstrip("/")
        self.cle = cle or os.environ.get("ARACANA_CLE")
        self.delai = delai

    # ------------------------------------------------------------ transport
    def _appel(self, chemin: str, *, corps: bytes | None = None,
               type_contenu: str | None = None, methode: str = "GET") -> dict:
        # Un User-Agent explicite n'est pas de la politesse : `urllib`
        # s'annonce « Python-urllib/3.x », que les pare-feux applicatifs
        # devant les hébergeurs — Cloudflare notamment — rejettent d'office.
        # Symptôme rencontré en conditions réelles : HTTP 403 sans corps JSON,
        # alors que la même requête en curl passait. Rien dans le message
        # n'aurait mis sur la piste.
        entetes = {"Accept": "application/json",
                   "User-Agent": "aracana-ocr-client/1.0 (+https://aracana.ai)"}
        if type_contenu:
            entetes["Content-Type"] = type_contenu
        if self.cle:
            entetes["Authorization"] = f"Bearer {self.cle}"
        req = urllib.request.Request(self.url + chemin, data=corps,
                                     headers=entetes, method=methode)
        try:
            with urllib.request.urlopen(req, timeout=self.delai) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            brut = e.read()
            try:
                corps_err = json.loads(brut)
                # FastAPI enveloppe le détail d'une HTTPException sous
                # « detail » ; l'API y met sa forme d'erreur structurée.
                if isinstance(corps_err.get("detail"), dict):
                    corps_err = corps_err["detail"]
            except Exception:
                corps_err = brut.decode("utf-8", "replace")[:400]
            raise ErreurApi(e.code, corps_err) from None

    @staticmethod
    def rasteriser(pdf: Path, ppp: int = 200, pages_max: int = 8) -> list[Path]:
        """PDF -> images de page, côté client.

        `/v1/ocr` prend des IMAGES de page, pas des PDF : c'est un contrat
        volontaire — le service qui détient le GPU n'a pas à embarquer un
        analyseur de PDF, et l'appelant garde la main sur la résolution.

        Le client le faisait dire au README sans le faire : `client.lire(
        "facture.pdf")` était documenté et le service répondait 415. Le défaut
        n'apparaissait pas dans les tests unitaires, qui n'envoient que des
        images ; il serait apparu au premier appel réel.

        `pypdfium2` (Apache-2.0) et non PyMuPDF, dont l'AGPL-3.0 obligerait
        quiconque intègre ce client à publier son propre code.
        """
        try:
            import pypdfium2 as pdfium
        except ImportError:
            raise RuntimeError(
                f"{pdf.name} est un PDF. Installez pypdfium2 pour le convertir "
                f"en images (`pip install pypdfium2`), ou envoyez directement "
                f"les pages en PNG.") from None

        dossier = Path(tempfile.mkdtemp(prefix="aracana_pdf_"))
        sorties: list[Path] = []
        doc = pdfium.PdfDocument(str(pdf))
        try:
            for i in range(min(len(doc), pages_max)):
                cible = dossier / f"{pdf.stem}_p{i + 1:03d}.png"
                doc[i].render(scale=ppp / 72).to_pil().save(cible, "PNG")
                sorties.append(cible)
        finally:
            doc.close()
        if not sorties:
            raise RuntimeError(f"{pdf.name} : aucune page à lire.")
        return sorties

    @staticmethod
    def _multipart(fichiers: list[Path]) -> tuple[bytes, str]:
        """Corps multipart/form-data, à la main.

        L'API attend un champ `files` répété — une entrée par page — parce que
        plusieurs pages sont analysées en une seule passe avant du modèle. La
        bibliothèque standard ne sait pas fabriquer ce corps ; le faire ici
        évite d'imposer `requests` à l'intégrateur.
        """
        limite = "----aracana" + base64.urlsafe_b64encode(
            os.urandom(12)).decode("ascii").strip("=")
        morceaux: list[bytes] = []
        for f in fichiers:
            type_mime = (mimetypes.guess_type(f.name)[0]
                         or "application/octet-stream")
            # Le nom est cité et échappé : un guillemet dans un nom de fichier
            # casserait l'en-tête et ferait rejeter le corps entier.
            nom = f.name.replace('"', "'").replace("\r", " ").replace("\n", " ")
            morceaux += [
                f"--{limite}\r\n".encode(),
                f'Content-Disposition: form-data; name="files"; '
                f'filename="{nom}"\r\n'.encode("utf-8"),
                f"Content-Type: {type_mime}\r\n\r\n".encode(),
                f.read_bytes(),
                b"\r\n",
            ]
        morceaux.append(f"--{limite}--\r\n".encode())
        return (b"".join(morceaux),
                f"multipart/form-data; boundary={limite}")

    # --------------------------------------------------------------- lecture
    def lire(self, chemin: str | Path | list[Path], *,
             brut: bool = False) -> dict:
        """Lit un document, ou plusieurs pages d'un même document.

        Choisit seul entre la route synchrone et une tâche : l'appelant n'a pas
        à savoir combien pèse son PDF, c'est au client de router.
        """
        fichiers = ([Path(x) for x in chemin] if isinstance(chemin, list)
                    else [Path(chemin)])
        # Les PDF sont convertis ici, jamais envoyés tels quels : voir
        # `rasteriser`. On garde la trace du dossier temporaire pour le nettoyer,
        # sinon un traitement de lot laisse un PNG par page sur le disque.
        pages: list[Path] = []
        temporaires: set[Path] = set()
        for f in fichiers:
            if f.suffix.lower() == ".pdf":
                images = self.rasteriser(f)
                pages += images
                temporaires.add(images[0].parent)
            else:
                pages.append(f)
        fichiers = pages[:SEUIL_ASYNCHRONE * 2]
        corps, type_contenu = self._multipart(fichiers)
        suffixe = "?include_raw=true" if brut else ""

        poids = sum(f.stat().st_size for f in fichiers)
        try:
            if len(fichiers) <= SEUIL_ASYNCHRONE and poids <= 4_000_000:
                try:
                    return self._appel("/v1/ocr" + suffixe, corps=corps,
                                       type_contenu=type_contenu, methode="POST")
                except ErreurApi as e:
                    # Tous ces codes veulent dire la même chose : « pas en
                    # synchrone ». On bascule sur les tâches au lieu de les
                    # remonter comme des échecs.
                    #
                    #   413 le service refuse la charge d'avance ;
                    #   504 le service a lui-même expiré et renvoie vers /v1/jobs ;
                    #   524 / 522 / 502 un intermédiaire a coupé AVANT le
                    #       service. Cloudflare, qui protège la plupart des
                    #       hébergeurs GPU, tranche à 100 secondes — or une
                    #       page de facture réelle et dense a demandé 119
                    #       secondes de modèle sur une L4. Sans ce repli, tout
                    #       document sérieux échouait derrière un proxy, et le
                    #       message ne disait rien : « HTTP 524 » sans corps.
                    if e.statut not in (413, 502, 504, 522, 524):
                        raise
            return self.tache(corps, type_contenu)
        finally:
            for d in temporaires:
                shutil.rmtree(d, ignore_errors=True)

    def tache(self, corps: bytes, type_contenu: str, *, pas: float = 2.0) -> dict:
        """Soumet une tâche et attend son résultat, sans interroger trop vite.

        L'intervalle croît : sur un document long, interroger toutes les demi-
        secondes consomme un quota de requêtes sans rien accélérer.
        """
        depart = self._appel("/v1/jobs", corps=corps,
                             type_contenu=type_contenu, methode="POST")
        identifiant = depart["job_id"]
        attente, debut = pas, time.time()
        while True:
            etat = self._appel(f"/v1/jobs/{identifiant}")
            if etat.get("status") in ("succeeded", "failed"):
                if etat["status"] == "failed":
                    raise ErreurApi(200, {"code": "job_failed",
                                          "message": etat.get("error", "")})
                return etat["result"]
            if time.time() - debut > self.delai:
                raise TimeoutError(
                    f"Tâche {identifiant} toujours en cours après "
                    f"{self.delai}s. Elle continue côté serveur : on peut la "
                    f"relire plus tard avec GET /v1/jobs/{identifiant}.")
            time.sleep(attente)
            attente = min(attente * 1.5, 15.0)

    # ------------------------------------------------------------- utilitaires
    def sante(self) -> dict:
        return self._appel("/v1/health")

    def modele(self) -> dict:
        return self._appel("/v1/model")

    def usage(self) -> dict:
        return self._appel("/v1/usage")


def en_pixels(boite: dict, largeur: int, hauteur: int) -> tuple[int, int, int, int]:
    """Coordonnées normalisées → pixels.

    Le modèle rend des entiers de 0 à 999 **inclus**, donc l'échelle est
    x/999, pas x/1000. Sur une page A4 à 300 ppp l'écart atteint deux pixels
    et demi : invisible à l'œil, suffisant pour qu'une découpe de tableau
    tombe à côté d'une ligne.
    """
    x1, y1, x2, y2 = boite["normalised"]
    return (round(x1 / 999 * largeur), round(y1 / 999 * hauteur),
            round(x2 / 999 * largeur), round(y2 / 999 * hauteur))
