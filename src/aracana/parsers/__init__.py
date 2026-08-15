"""Adaptateurs d'analyseurs — ce qui rend l'affirmation C3 falsifiable.

POURQUOI CE SOUS-PAQUET EXISTE
  RECHERCHE.md §3.7 affirme (C3) que **le plancher de risque est fixé par les
  vérificateurs, pas par l'analyseur**. Tant qu'un seul analyseur est branché,
  c'est une conviction. Avec quatre, c'est une expérience : on fige les
  vérificateurs, on remplace l'analyseur, on relance la même mesure. Si le
  risque à couverture fixée varie fortement, C3 est fausse — et il faudra le
  publier.

  C'est la seule raison d'être de ce dossier. Il ne sert pas à « supporter
  beaucoup de formats » : il sert à pouvoir se tromper publiquement.

RÈGLES COMMUNES À TOUS LES ADAPTATEURS
  1. **Import paresseux.** La bibliothèque tierce n'est importée que dans
     `analyser()`. Docling, Marker et MinerU pèsent chacun plusieurs
     gigaoctets ; les importer au chargement du paquet rendrait
     `import aracana` inutilisable.
  2. **Message d'absence utile.** Quand la dépendance manque, on dit quoi
     installer, pas « ModuleNotFoundError ».
  3. **Aucune invention.** Un analyseur qui ne rend pas de boîtes rend
     `boite=None`. Fabriquer une position plausible tromperait la boucle de
     réparation, qui recadre sur ces coordonnées.
  4. **Traçabilité.** Chaque bloc porte `source` — quel analyseur l'a produit.
     Dans une mesure comparative, ne pas savoir d'où vient un bloc rend le
     résultat inexploitable.

BRANCHER UN ADAPTATEUR
    from aracana import plugins
    from aracana.parsers import DoclingParser
    plugins.parser(DoclingParser())

ou, pour tous ceux dont la dépendance est présente :
    from aracana.parsers import enregistrer_disponibles
    enregistrer_disponibles()
"""
from __future__ import annotations

from typing import Any

from ..document import Bloc, Boite, Document, Page, TypeBloc

__all__ = [
    "ApiParser", "DoclingParser", "MarkerParser", "MineruParser",
    "TesseractParser", "PdfTexteParser", "AnalyseurIndisponible", "disponibles",
    "enregistrer_disponibles", "ADAPTATEURS",
]


class AnalyseurIndisponible(ImportError):
    """La bibliothèque de l'analyseur n'est pas installée."""


def _exiger(module: str, extra: str, quoi: str):
    """Importe, ou explique. Jamais un ModuleNotFoundError nu."""
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise AnalyseurIndisponible(
            f"{quoi} n'est pas installé ({module}).\n"
            f"    pip install \"aracana-ocr[{extra}]\"\n"
            f"  Les autres analyseurs et tout le moteur de vérification "
            f"restent utilisables sans lui."
        ) from e


def _bloc(ordre: int, type_brut: str, texte: str, boite: Boite | None,
          page: int, source: str, confiance: float | None = None,
          brut: dict | None = None) -> Bloc:
    return Bloc(ordre=ordre, type=TypeBloc.depuis(type_brut), texte=texte or "",
                boite=boite, page=page, confiance=confiance, source=source,
                brut=brut or {})


# ═══════════════════════════════════════════════════════ service ARACANA

class ApiParser:
    """Le service ARACANA OCR par HTTP. Le seul adaptateur déjà éprouvé en réel.

    Il n'a aucune dépendance : le SDK `aracana.client` n'utilise que la
    bibliothèque standard. C'est donc le point de comparaison par défaut.
    """

    nom = "aracana-api"

    def __init__(self, url: str, cle: str | None = None, delai: int = 900):
        from ..client import Client
        self._c = Client(url, cle, delai=delai)

    def analyser(self, source: bytes | str, **options: Any) -> Document:
        import tempfile
        import time
        from pathlib import Path

        t0 = time.time()
        if isinstance(source, (str, Path)):
            chemin = Path(source)
            temporaire = None
        else:
            suffixe = ".pdf" if source[:5] == b"%PDF-" else ".png"
            with tempfile.NamedTemporaryFile(suffix=suffixe, delete=False) as f:
                f.write(source)
                chemin = temporaire = Path(f.name)
        try:
            d = self._c.lire(chemin, brut=options.get("brut", False))
        finally:
            if temporaire is not None:
                temporaire.unlink(missing_ok=True)

        pages = []
        for p in d.get("pages", []):
            blocs = []
            for b in p.get("blocks", []):
                n = (b.get("box") or {}).get("normalised")
                blocs.append(_bloc(
                    b.get("order", len(blocs) + 1), b.get("type", "text"),
                    b.get("text", ""),
                    Boite(*n) if n and len(n) == 4 else None,
                    p.get("page", 1), self.nom))
            pages.append(Page(p.get("page", 1), p.get("width"),
                              p.get("height"), blocs))
        return Document(pages, self.nom, round(time.time() - t0, 2),
                        d.get("raw"), {"usage": d.get("usage")})


# ══════════════════════════════════════════════════════════════ Docling

class DoclingParser:
    """IBM Docling. Rend un `DoclingDocument` riche, dont on projette le
    strict nécessaire.

    Docling exprime les positions en `BoundingBox` sur un repère dont
    l'origine peut être en BAS de page (`CoordOrigin.BOTTOMLEFT`). Convertir
    sans vérifier l'origine retourne toutes les boîtes verticalement — une
    erreur invisible sur un texte plein page, fatale au recadrage de la boucle
    de réparation.
    """

    nom = "docling"

    def __init__(self, **options: Any):
        self._options = options
        self._conv = None

    def analyser(self, source: bytes | str, **options: Any) -> Document:
        import time
        _exiger("docling.document_converter", "docling", "Docling")
        from docling.document_converter import DocumentConverter

        if self._conv is None:
            self._conv = DocumentConverter(**self._options)

        chemin, temporaire = _materialiser(source, ".pdf")
        t0 = time.time()
        try:
            res = self._conv.convert(str(chemin))
        finally:
            if temporaire:
                chemin.unlink(missing_ok=True)

        doc = res.document
        tailles: dict[int, tuple[float, float]] = {}
        for no, p in (getattr(doc, "pages", {}) or {}).items():
            taille = getattr(p, "size", None)
            if taille is not None:
                tailles[int(no)] = (float(taille.width), float(taille.height))

        par_page: dict[int, list[Bloc]] = {}
        for i, (item, _niveau) in enumerate(doc.iterate_items(), start=1):
            texte = (getattr(item, "text", "") or "").strip()
            etiquette = str(getattr(item, "label", "") or "text")
            prov = (getattr(item, "prov", None) or [None])[0]
            no_page = int(getattr(prov, "page_no", 1) or 1) if prov else 1
            boite = None
            if prov is not None and getattr(prov, "bbox", None) is not None:
                l, h = tailles.get(no_page, (0, 0))
                boite = _boite_docling(prov.bbox, l, h)
            if not texte and TypeBloc.depuis(etiquette) not in (
                    TypeBloc.IMAGE, TypeBloc.TABLEAU):
                continue
            par_page.setdefault(no_page, []).append(
                _bloc(len(par_page.get(no_page, [])) + 1, etiquette, texte,
                      boite, no_page, self.nom))

        pages = [Page(n, int(tailles.get(n, (0, 0))[0]) or None,
                      int(tailles.get(n, (0, 0))[1]) or None, b)
                 for n, b in sorted(par_page.items())]
        return Document(pages or [Page(1, None, None, [])], self.nom,
                        round(time.time() - t0, 2))


def _boite_docling(bbox, largeur: float, hauteur: float) -> Boite | None:
    """Convertit une `BoundingBox` Docling, en respectant l'origine.

    `CoordOrigin.BOTTOMLEFT` place l'origine en bas ; il faut alors inverser
    l'axe vertical. Ne pas le faire produit des boîtes retournées, ce qui ne
    se voit pas sur une page dense et casse tout recadrage.
    """
    if not largeur or not hauteur:
        return None
    try:
        origine = str(getattr(bbox, "coord_origin", "TOPLEFT")).upper()
        x1, x2 = float(bbox.l), float(bbox.r)
        y1, y2 = float(bbox.t), float(bbox.b)
        if "BOTTOM" in origine:
            y1, y2 = hauteur - float(bbox.t), hauteur - float(bbox.b)
        haut, bas = min(y1, y2), max(y1, y2)
        return Boite.depuis_pixels((x1, haut, x2, bas), largeur, hauteur)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════ Marker

class MarkerParser:
    """Datalab Marker. Meilleure précision publiée sur les tableaux (96,7 %).

    Marker rend ses positions en pixels de la page rendue, dans `bbox`, et
    expose un JSON hiérarchique. On aplatit en gardant l'ordre de parcours,
    qui EST l'ordre de lecture qu'il a déterminé.
    """

    nom = "marker"

    def __init__(self, **options: Any):
        self._options = options
        self._conv = None

    def analyser(self, source: bytes | str, **options: Any) -> Document:
        import time
        _exiger("marker.converters.pdf", "marker", "Marker")
        from marker.config.parser import ConfigParser
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import output_from_document

        if self._conv is None:
            cfg = ConfigParser({"output_format": "json", **self._options})
            self._conv = PdfConverter(artifact_dict=create_model_dict(),
                                      config=cfg.generate_config_dict())

        chemin, temporaire = _materialiser(source, ".pdf")
        t0 = time.time()
        try:
            rendu = self._conv(str(chemin))
        finally:
            if temporaire:
                chemin.unlink(missing_ok=True)

        sortie = output_from_document(rendu)
        pages: list[Page] = []
        for i, page in enumerate(getattr(sortie, "children", []) or [], start=1):
            l, h = _dimensions_marker(page)
            blocs: list[Bloc] = []
            _aplatir_marker(page, blocs, i, l, h, self.nom)
            pages.append(Page(i, l or None, h or None, blocs))
        return Document(pages or [Page(1, None, None, [])], self.nom,
                        round(time.time() - t0, 2))


def _dimensions_marker(page) -> tuple[int, int]:
    bbox = getattr(page, "bbox", None) or getattr(page, "polygon", None)
    try:
        return int(bbox[2]), int(bbox[3])
    except Exception:
        return 0, 0


def _aplatir_marker(noeud, sortie: list[Bloc], no_page: int,
                    largeur: int, hauteur: int, source: str) -> None:
    for enfant in (getattr(noeud, "children", None) or []):
        texte = (getattr(enfant, "html", None)
                 or getattr(enfant, "text", "") or "")
        texte = _sans_balises(texte).strip()
        bbox = getattr(enfant, "bbox", None)
        boite = None
        if bbox and largeur and hauteur:
            try:
                boite = Boite.depuis_pixels(
                    (bbox[0], bbox[1], bbox[2], bbox[3]), largeur, hauteur)
            except Exception:
                boite = None
        if texte:
            sortie.append(_bloc(len(sortie) + 1,
                                str(getattr(enfant, "block_type", "text")),
                                texte, boite, no_page, source))
        _aplatir_marker(enfant, sortie, no_page, largeur, hauteur, source)


def _sans_balises(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", s) if "<" in s else s


# ═══════════════════════════════════════════════════════════════ MinerU

class MineruParser:
    """OpenDataLab MinerU. Le plus rapide, fort sur le CJK et le scientifique.

    MinerU rend une liste plate de blocs avec `bbox` en pixels et un `type`
    proche du nôtre — c'est l'adaptateur le plus direct des trois.
    """

    nom = "mineru"

    def __init__(self, **options: Any):
        self._options = options

    def analyser(self, source: bytes | str, **options: Any) -> Document:
        import time
        _exiger("magic_pdf.data.dataset", "mineru", "MinerU")
        from magic_pdf.data.dataset import PymuDocDataset
        from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze

        octets = source if isinstance(source, bytes) else open(source, "rb").read()
        t0 = time.time()
        jeu = PymuDocDataset(octets)
        resultat = doc_analyze(jeu, ocr=self._options.get("ocr", True))
        milieu = resultat.pipe_ocr_mode(None).get_middle_json()

        pages: list[Page] = []
        for i, pj in enumerate(milieu.get("pdf_info", []), start=1):
            l, h = (pj.get("page_size") or [0, 0])[:2]
            blocs: list[Bloc] = []
            for b in pj.get("preproc_blocks", []) or pj.get("para_blocks", []) or []:
                texte = _texte_mineru(b).strip()
                bbox = b.get("bbox")
                boite = None
                if bbox and l and h:
                    try:
                        boite = Boite.depuis_pixels(tuple(bbox[:4]), int(l), int(h))
                    except Exception:
                        boite = None
                if texte:
                    blocs.append(_bloc(len(blocs) + 1, str(b.get("type", "text")),
                                       texte, boite, i, self.nom))
            pages.append(Page(i, int(l) or None, int(h) or None, blocs))
        return Document(pages or [Page(1, None, None, [])], self.nom,
                        round(time.time() - t0, 2))


def _texte_mineru(bloc: dict) -> str:
    """MinerU imbrique le texte sous `lines` → `spans` → `content`."""
    if "text" in bloc and isinstance(bloc["text"], str):
        return bloc["text"]
    morceaux = []
    for ligne in bloc.get("lines", []) or []:
        for span in ligne.get("spans", []) or []:
            morceaux.append(span.get("content") or span.get("text") or "")
    return " ".join(m for m in morceaux if m)


# ════════════════════════════════════════════════════════════ Tesseract

class TesseractParser:
    """Tesseract. Le point bas de la comparaison, et il a sa raison d'être.

    Aucun modèle de mise en page, aucune sémantique : il rend des mots et des
    positions. C'est précisément l'intérêt pour C3 — si le plancher de risque
    tient même avec l'analyseur le plus faible, l'affirmation est forte. S'il
    ne tient qu'avec les meilleurs, elle est faible, et il faut le dire.
    """

    nom = "tesseract"

    def __init__(self, langues: str = "fra+deu+ita+eng", **options: Any):
        self._langues = langues
        self._options = options

    def analyser(self, source: bytes | str, **options: Any) -> Document:
        import io
        import time
        _exiger("pytesseract", "tesseract", "Tesseract")
        import pytesseract
        from PIL import Image

        octets = source if isinstance(source, bytes) else open(source, "rb").read()
        if octets[:5] == b"%PDF-":
            raise AnalyseurIndisponible(
                "Tesseract lit des images, pas des PDF. Rastérisez d'abord "
                "(pip install \"aracana-ocr[pdf]\") ou fournissez des PNG.")

        t0 = time.time()
        image = Image.open(io.BytesIO(octets))
        largeur, hauteur = image.size
        donnees = pytesseract.image_to_data(
            image, lang=self._langues, output_type=pytesseract.Output.DICT)

        # On regroupe les mots par bloc Tesseract : un bloc par mot rendrait
        # l'extraction inexploitable, et ne refléterait pas ce qu'il a compris.
        groupes: dict[tuple, list[int]] = {}
        for i, conf in enumerate(donnees["conf"]):
            try:
                if float(conf) < 0 or not donnees["text"][i].strip():
                    continue
            except (TypeError, ValueError):
                continue
            cle = (donnees["block_num"][i], donnees["par_num"][i])
            groupes.setdefault(cle, []).append(i)

        blocs: list[Bloc] = []
        for _cle, idx in sorted(groupes.items()):
            mots = [donnees["text"][i] for i in idx]
            x1 = min(donnees["left"][i] for i in idx)
            y1 = min(donnees["top"][i] for i in idx)
            x2 = max(donnees["left"][i] + donnees["width"][i] for i in idx)
            y2 = max(donnees["top"][i] + donnees["height"][i] for i in idx)
            confs = [float(donnees["conf"][i]) for i in idx]
            blocs.append(_bloc(
                len(blocs) + 1, "text", " ".join(mots),
                Boite.depuis_pixels((x1, y1, x2, y2), largeur, hauteur),
                1, self.nom, confiance=sum(confs) / len(confs) / 100))
        return Document([Page(1, largeur, hauteur, blocs)], self.nom,
                        round(time.time() - t0, 2))


# ══════════════════════════════════════════════════ couche texte native

class PdfTexteParser:
    """La couche texte du PDF, telle quelle. Aucun modèle, aucun GPU.

    Ce n'est pas un analyseur au rabais : c'est ce qu'emploient réellement
    beaucoup de chaînes en production, parce que sur un PDF généré — et une
    facture électronique en est un — le texte est déjà là, exact, gratuit.
    Lancer un modèle de vision dessus coûte plus cher pour un résultat moins
    bon.

    Pour l'expérience C3, c'est le point de comparaison le plus utile qui
    soit : il n'a rien d'un modèle, il n'hallucine pas, et ses erreurs sont
    d'une tout autre nature — ordre de lecture chahuté, colonnes fusionnées,
    rien sur un scan. Si le plancher de risque tient entre lui et un modèle
    de vision, l'affirmation C3 est forte.

    Limite assumée, et elle est structurelle : sur un PDF sans couche texte —
    un scan — il ne rend rien. Le document apparaît alors avec zéro champ et
    part en revue. C'est le comportement correct, pas un échec à masquer.
    """

    nom = "pdf-texte"

    def analyser(self, source: bytes | str, **options: Any) -> Document:
        import io
        import time
        _exiger("pypdf", "pdf", "pypdf")
        from pypdf import PdfReader

        octets = source if isinstance(source, bytes) else open(source, "rb").read()
        t0 = time.time()
        lecteur = PdfReader(io.BytesIO(octets))

        pages = []
        for i, page in enumerate(lecteur.pages, start=1):
            try:
                texte = page.extract_text() or ""
            except Exception:
                texte = ""
            boite_page = getattr(page, "mediabox", None)
            largeur = int(float(boite_page.width)) if boite_page else None
            hauteur = int(float(boite_page.height)) if boite_page else None

            # Un paragraphe par ligne non vide. La couche texte ne porte ni
            # type ni position exploitable sans reconstruction ; inventer des
            # boîtes à partir de l'ordre du flux produirait des coordonnées
            # fausses, et la boucle de réparation recadrerait à côté.
            blocs = [
                _bloc(n, "text", ligne.strip(), None, i, self.nom)
                for n, ligne in enumerate(
                    (l for l in texte.split("\n") if l.strip()), start=1)
            ]
            pages.append(Page(i, largeur, hauteur, blocs))

        return Document(pages or [Page(1, None, None, [])], self.nom,
                        round(time.time() - t0, 3))


# ═════════════════════════════════════════════════════════════ utilitaires

def _materialiser(source: bytes | str, suffixe: str):
    """Rend (chemin, faut-il le supprimer). Les bibliothèques tierces veulent
    presque toutes un fichier ; nous travaillons en octets."""
    import tempfile
    from pathlib import Path
    if isinstance(source, (str, Path)):
        return Path(source), False
    with tempfile.NamedTemporaryFile(suffix=suffixe, delete=False) as f:
        f.write(source)
        return Path(f.name), True


#: Les adaptateurs sans configuration obligatoire. `ApiParser` en est absent :
#: il exige une URL, et l'enregistrer sans elle produirait un analyseur qui
#: échoue au premier appel.
ADAPTATEURS = {
    "pdf-texte": PdfTexteParser,
    "docling": DoclingParser,
    "marker": MarkerParser,
    "mineru": MineruParser,
    "tesseract": TesseractParser,
}


def disponibles() -> dict[str, bool]:
    """Quels adaptateurs peuvent réellement tourner ici.

    On teste l'import de la dépendance sans instancier : construire un
    convertisseur Docling charge des modèles et prend des secondes.
    """
    import importlib.util
    modules = {"pdf-texte": "pypdf", "docling": "docling",
               "marker": "marker", "mineru": "magic_pdf",
               "tesseract": "pytesseract"}
    etat = {}
    for nom, module in modules.items():
        try:
            etat[nom] = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            etat[nom] = False
    return etat


def enregistrer_disponibles() -> list[str]:
    """Branche tous les adaptateurs dont la dépendance est présente."""
    from .. import plugins
    branches = []
    for nom, present in disponibles().items():
        if present:
            plugins.parser(ADAPTATEURS[nom](), nom)
            branches.append(nom)
    return branches
