"""Triage : décider ce qu'un document EST avant de décider quoi en faire.

LE RAISONNEMENT QUI FONDE CE MODULE
  À partir du 1ᵉʳ septembre 2026, la majorité des factures françaises arrivent
  structurées. Une facture structurée n'a pas besoin d'OCR — envoyer un GPU
  dessus, c'est brûler de l'argent pour un résultat moins bon que la lecture
  directe du XML.

  Mais la réforme crée un travail que personne ne fait : **vérifier que le XML
  embarqué dit la même chose que la page**. Un Factur-X est un PDF/A-3 qui
  contient les deux. Rien n'oblige qu'ils concordent. Le XML peut porter
  1 500,00 € pendant que l'œil humain — et le juge, et l'auditeur — lit
  1 600,00 € sur l'image. C'est un vecteur d'erreur fournisseur et de fraude, et
  le détecter exige précisément un OCR.

  Autrement dit : la réforme retire de la valeur à l'OCR aveugle et en donne à
  l'OCR de contrôle. Ce module est la bascule entre les deux.

QUATRE ROUTES, ET UNE SEULE COÛTE UN GPU
    STRUCTURE   XML seul (UBL, CII) ................ 0 appel modèle
    HYBRIDE     Factur-X / ZUGFeRD ................. 1 appel, pour RÉCONCILIER
    NATIF       PDF avec couche texte fiable ....... 0 appel modèle
    IMAGE       scan, photo, PDF sans texte ........ 1 appel, pour LIRE
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Signatures de fichiers. On ne fait jamais confiance a l'extension : un ".pdf"
# renomme depuis un JPEG est courant dans les flux fournisseurs.
SIGNATURES = {
    b"%PDF-": "pdf",
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
    b"RIFF": "webp",
    b"II*\x00": "tiff",
    b"MM\x00*": "tiff",
    b"PK\x03\x04": "zip",
}

# Noms normalises de la piece jointe XML dans un PDF/A-3.
NOMS_FACTURX = {
    "factur-x.xml",          # Factur-X (FR/DE)
    "zugferd-invoice.xml",   # ZUGFeRD 2.x
    "ZUGFeRD-invoice.xml",   # ZUGFeRD 1.0
    "xrechnung.xml",         # XRechnung (DE, secteur public)
    "order-x.xml",
}

RACINES_XML = {
    "Invoice": "ubl",                       # UBL 2.1
    "CreditNote": "ubl",
    "CrossIndustryInvoice": "cii",          # UN/CEFACT CII — base de Factur-X
    "SCRDMCCBDACIOMessageStructure": "cii",
}


class Route(str, Enum):
    STRUCTURE = "structured"
    HYBRIDE = "hybrid"
    NATIF = "native_text"
    IMAGE = "image"
    INCONNU = "unknown"


@dataclass
class Diagnostic:
    route: Route
    format: str                       # pdf, png, xml, ...
    profil: str | None = None         # factur-x basic/en16931/extended, ubl, cii
    xml: bytes | None = None
    xml_nom: str | None = None
    pages: int | None = None
    texte_natif: str | None = None
    couverture_texte: float | None = None   # 0..1, densite de la couche texte
    besoin_modele: bool = True
    raison: str = ""
    avertissements: list[str] = field(default_factory=list)

    @property
    def economise_gpu(self) -> bool:
        return not self.besoin_modele


def _signature(octets: bytes) -> str:
    for sig, nom in SIGNATURES.items():
        if octets.startswith(sig):
            return nom
    tete = octets[:512].lstrip()
    if tete.startswith(b"<?xml") or tete.startswith(b"<"):
        return "xml"
    return "inconnu"


def _racine_xml(octets: bytes) -> tuple[str | None, str | None]:
    """Nom de la racine et famille, sans parser le document entier."""
    try:
        tete = octets[:4096].decode("utf-8", "ignore")
    except Exception:
        return None, None
    m = re.search(r"<(?:\w+:)?([A-Za-z][\w.-]*)[\s>]", tete.replace("<?xml", " "))
    if not m:
        return None, None
    nom = m.group(1)
    return nom, RACINES_XML.get(nom)


def _profil_facturx(xml: bytes) -> str | None:
    """Le profil conditionne les champs obligatoires ; le connaître évite de
    réclamer un champ que le profil n'impose pas."""
    t = xml[:20000].decode("utf-8", "ignore")
    m = re.search(r"urn:(?:cen\.eu|factur-x\.eu)[^<\"']*?:(\w[\w.]*)", t)
    if m:
        return m.group(1).lower()
    for cle in ("extended", "en16931", "basicwl", "basic", "minimum"):
        if cle in t.lower():
            return cle
    return None


def _pieces_jointes_pdf(octets: bytes) -> dict[str, bytes]:
    """Extrait les pièces jointes d'un PDF/A-3 sans dépendance lourde.

    pypdf est utilisé s'il est présent — c'est le chemin propre. Le repli
    balaye les flux `EmbeddedFile` à la main : moins élégant, mais il évite
    qu'un client sans pypdf perde silencieusement la détection Factur-X, ce qui
    ferait basculer des factures structurées vers un OCR inutile.
    """
    try:
        import io

        from pypdf import PdfReader

        lecteur = PdfReader(io.BytesIO(octets))
        att = getattr(lecteur, "attachments", None)
        if att:
            return {n: (v[0] if isinstance(v, list) else v) for n, v in att.items()}
    except ImportError:
        # `pypdf` absent. Le repli manuel ci-dessous fonctionne sur beaucoup de
        # fichiers, mais pas sur tous — et l'échec est SILENCIEUX : le
        # Factur-X est alors classé IMAGE, on paie un GPU pour le lire, et la
        # réconciliation XML contre page — la fonction pour laquelle ce
        # framework existe — ne s'exécute jamais.
        #
        # Découvert en installant le paquet nu dans un environnement vierge.
        # C'est exactement le mode de panne que ce projet combat ailleurs :
        # une dégradation qui ne se signale pas. On la signale donc.
        global _AVERTI_PYPDF
        if not _AVERTI_PYPDF:
            _AVERTI_PYPDF = True
            import warnings
            warnings.warn(
                "pypdf n'est pas installé : la détection des pièces jointes "
                "Factur-X / ZUGFeRD repose sur un repli approximatif. Des "
                "factures hybrides peuvent être classées comme de simples "
                "images, ce qui coûte un appel modèle inutile ET supprime la "
                "réconciliation XML contre page. Installez `pip install "
                "aracana[pdf]`.", RuntimeWarning, stacklevel=3)
    except Exception:
        pass

    trouves: dict[str, bytes] = {}
    try:
        import zlib
        for m in re.finditer(rb"/F\s*\(([^)]{3,120}\.xml)\)", octets, re.I):
            nom = m.group(1).decode("latin-1")
            fenetre = octets[m.end():m.end() + 200_000]
            fm = re.search(rb"stream\r?\n", fenetre)
            if not fm:
                continue
            fin = fenetre.find(b"endstream", fm.end())
            if fin < 0:
                continue
            donnees = fenetre[fm.end():fin]
            try:
                donnees = zlib.decompress(donnees)
            except zlib.error:
                pass
            if b"<" in donnees[:200]:
                trouves[nom] = donnees
    except Exception:
        pass
    return trouves


def _texte_natif_pdf(octets: bytes) -> tuple[str | None, int | None]:
    """Couche texte et nombre de pages, si pypdf est disponible."""
    try:
        import io

        from pypdf import PdfReader

        lecteur = PdfReader(io.BytesIO(octets))
        pages = len(lecteur.pages)
        morceaux = []
        for p in lecteur.pages[:20]:
            try:
                morceaux.append(p.extract_text() or "")
            except Exception:
                morceaux.append("")
        return "\n".join(morceaux), pages
    except Exception:
        return None, None


def _couverture(texte: str, pages: int) -> float:
    """Densité de la couche texte, ramenée à une page.

    Un PDF scanné passé dans un OCR bureautique médiocre porte souvent quelques
    dizaines de caractères de bruit. Une vraie couche texte de facture en fait
    plusieurs centaines. Le seuil n'a pas à être fin : il sépare « il y a du
    texte exploitable » de « il n'y en a pas ».
    """
    if not texte or pages <= 0:
        return 0.0
    utiles = len(re.sub(r"\s", "", texte))
    return min(1.0, utiles / (pages * 900.0))


SEUIL_COUVERTURE = 0.35

#: Un seul avertissement par processus : le repeter a chaque page noierait
#: les journaux d'un traitement de lot.
_AVERTI_PYPDF = False


def diagnostiquer(source: str | Path | bytes, *,
                  forcer_ocr: bool = False) -> Diagnostic:
    """Classe un document et dit s'il faut appeler le modèle.

    `forcer_ocr` court-circuite l'économie : utile pour auditer un flux dont on
    soupçonne que la couche texte ment.
    """
    octets = Path(source).read_bytes() if not isinstance(source, bytes) else source
    fmt = _signature(octets)

    # --- XML nu : rien a lire visuellement -------------------------------
    if fmt == "xml":
        nom, famille = _racine_xml(octets)
        if famille:
            return Diagnostic(
                route=Route.STRUCTURE, format="xml", profil=famille, xml=octets,
                besoin_modele=False,
                raison=f"Structured {famille.upper()} invoice (<{nom}>). "
                       f"No OCR needed — the data is already machine-readable.")
        return Diagnostic(
            route=Route.INCONNU, format="xml", besoin_modele=False,
            raison=f"XML with unrecognised root <{nom}>.",
            avertissements=["Not a UBL or CII invoice; nothing to extract."])

    # --- images : il faut lire -------------------------------------------
    if fmt in ("png", "jpeg", "webp", "tiff"):
        return Diagnostic(
            route=Route.IMAGE, format=fmt, pages=1, besoin_modele=True,
            raison="Raster image — the model is the only way to read it.")

    # --- ZIP : un lot, pas un document -----------------------------------
    if fmt == "zip":
        try:
            with zipfile.ZipFile(__import__("io").BytesIO(octets)) as z:
                noms = z.namelist()[:50]
        except Exception:
            noms = []
        return Diagnostic(
            route=Route.INCONNU, format="zip", besoin_modele=False,
            raison="Archive, not a single document. Unpack it first.",
            avertissements=[f"{len(noms)} entries"])

    # --- PDF : le cas interessant ----------------------------------------
    if fmt == "pdf":
        pj = _pieces_jointes_pdf(octets)
        xml_nom = next(
            (n for n in pj if n.lower() in {x.lower() for x in NOMS_FACTURX}),
            None)
        if xml_nom is None:
            xml_nom = next(
                (n for n, v in pj.items()
                 if n.lower().endswith(".xml") and _racine_xml(v)[1]), None)

        texte, pages = _texte_natif_pdf(octets)
        couv = _couverture(texte or "", pages or 1)

        if xml_nom:
            xml = pj[xml_nom]
            _, famille = _racine_xml(xml)
            profil = _profil_facturx(xml) or famille
            # HYBRIDE : on appelle le modele NON PAS pour lire, mais pour
            # verifier que la page dit ce que le XML pretend. C'est le seul
            # endroit ou l'OCR gagne de la valeur grace a la reforme.
            return Diagnostic(
                route=Route.HYBRIDE, format="pdf", profil=profil, xml=xml,
                xml_nom=xml_nom, pages=pages, texte_natif=texte,
                couverture_texte=couv, besoin_modele=True,
                raison=("Factur-X / ZUGFeRD: embedded XML plus a visual layer. "
                        "The model reads the page so we can reconcile the two — "
                        "nothing guarantees they agree, and only the visual "
                        "layer is what a human or an auditor actually sees."))

        if not forcer_ocr and couv >= SEUIL_COUVERTURE:
            return Diagnostic(
                route=Route.NATIF, format="pdf", pages=pages, texte_natif=texte,
                couverture_texte=couv, besoin_modele=False,
                raison=(f"Native text layer covering {couv:.0%} of the expected "
                        f"density. Extracting it is exact and free; OCR could "
                        f"only be worse."))

        return Diagnostic(
            route=Route.IMAGE, format="pdf", pages=pages, texte_natif=texte,
            couverture_texte=couv, besoin_modele=True,
            raison=(f"Scanned PDF (text coverage {couv:.0%}). Rasterise the "
                    f"pages and read them."),
            avertissements=([] if pages else
                            ["Page count unknown — install pypdf for a reliable "
                             "rasterisation plan."]))

    return Diagnostic(
        route=Route.INCONNU, format=fmt, besoin_modele=False,
        raison=f"Unrecognised format ({fmt}).")
