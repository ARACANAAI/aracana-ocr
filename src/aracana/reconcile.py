"""Réconciliation Factur-X : le XML dit-il ce que la page montre ?

LE PROBLÈME QUE PERSONNE NE TRAITE
  Un Factur-X est un PDF/A-3 qui contient deux représentations de la même
  facture : un XML lisible par machine et une page lisible par un humain. Rien,
  dans la norme, ne garantit qu'elles concordent.

  Or les deux n'ont pas le même statut. Le XML est ce que la comptabilité
  intègre automatiquement. La page est ce que voit le comptable, le dirigeant
  qui valide, l'auditeur, et le juge en cas de litige. Un écart entre les deux
  n'est pas un détail technique : c'est soit une erreur d'émetteur qui va
  contaminer un grand livre, soit une divergence délibérée.

  À partir du 1ᵉʳ septembre 2026, tout le monde reçoit des Factur-X. Presque
  personne ne vérifie cette concordance, parce que la vérifier demande de LIRE
  L'IMAGE — c'est-à-dire un OCR. La réforme ne détruit donc pas la valeur de
  l'OCR : elle la déplace de la saisie vers le contrôle.

CE QUE CE MODULE NE FAIT PAS
  Il ne décide pas qui a raison. Un écart est signalé, jamais arbitré
  silencieusement. Le XML reste la source pour l'écriture comptable — il est
  normatif — mais un écart bloque l'automatisation et part en revue humaine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------- extraction

# CII (Factur-X, ZUGFeRD) et UBL nomment les mêmes notions différemment. On
# cherche par nom local pour ne pas dépendre des préfixes d'espaces de noms,
# qui varient d'un émetteur à l'autre.
CHEMINS_CII = {
    "invoice_number": ["ExchangedDocument/ID"],
    "invoice_date": ["ExchangedDocument/IssueDateTime/DateTimeString"],
    "currency": ["ApplicableHeaderTradeSettlement/InvoiceCurrencyCode"],
    "total_excl_vat": [
        "SpecifiedTradeSettlementHeaderMonetarySummation/TaxBasisTotalAmount"],
    "vat_amount": [
        "SpecifiedTradeSettlementHeaderMonetarySummation/TaxTotalAmount"],
    "total_incl_vat": [
        "SpecifiedTradeSettlementHeaderMonetarySummation/GrandTotalAmount"],
    "amount_due": [
        "SpecifiedTradeSettlementHeaderMonetarySummation/DuePayableAmount"],
    "seller_name": ["SellerTradeParty/Name"],
    "buyer_name": ["BuyerTradeParty/Name"],
}
CHEMINS_UBL = {
    "invoice_number": ["ID"],
    "invoice_date": ["IssueDate"],
    "currency": ["DocumentCurrencyCode"],
    "total_excl_vat": ["LegalMonetaryTotal/TaxExclusiveAmount"],
    "vat_amount": ["TaxTotal/TaxAmount"],
    "total_incl_vat": ["LegalMonetaryTotal/TaxInclusiveAmount"],
    "amount_due": ["LegalMonetaryTotal/PayableAmount"],
    "seller_name": ["AccountingSupplierParty/Party/PartyName/Name"],
    "buyer_name": ["AccountingCustomerParty/Party/PartyName/Name"],
}


def _local(balise: str) -> str:
    return balise.rsplit("}", 1)[-1]


def _descendre(noeud: ET.Element, chemin: str) -> str | None:
    """Suit un chemin par noms locaux, en profondeur. Le premier segment peut
    apparaître à n'importe quel niveau : les schémas CII imbriquent
    différemment selon le profil, et coder les préfixes exacts casserait à la
    première facture d'un autre émetteur."""
    segments = chemin.split("/")

    def chercher(n: ET.Element, i: int) -> str | None:
        for e in n.iter():
            if _local(e.tag) != segments[i]:
                continue
            if i == len(segments) - 1:
                if e.text and e.text.strip():
                    return e.text.strip()
                continue
            r = chercher(e, i + 1)
            if r is not None:
                return r
        return None

    return chercher(noeud, 0)


def _decimal(v: str | None) -> Decimal | None:
    if not v:
        return None
    t = v.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(t).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _date_iso(v: str | None) -> str | None:
    """CII écrit 20260312, UBL 2026-03-12. On rend le format français, celui
    que l'on comparera au texte lu sur la page."""
    if not v:
        return None
    v = v.strip()
    if re.fullmatch(r"\d{8}", v):
        return f"{v[6:8]}/{v[4:6]}/{v[0:4]}"
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2}).*", v)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return v


def lire_xml(xml: bytes) -> dict[str, Any]:
    """Champs normalisés depuis un XML CII ou UBL."""
    racine = ET.fromstring(xml)
    nom = _local(racine.tag)
    chemins = CHEMINS_UBL if nom in ("Invoice", "CreditNote") else CHEMINS_CII
    famille = "ubl" if chemins is CHEMINS_UBL else "cii"

    sortie: dict[str, Any] = {"_family": famille}
    for champ, liste in chemins.items():
        brut = next((r for c in liste if (r := _descendre(racine, c))), None)
        if champ in ("total_excl_vat", "vat_amount", "total_incl_vat", "amount_due"):
            sortie[champ] = _decimal(brut)
        elif champ == "invoice_date":
            sortie[champ] = _date_iso(brut)
        else:
            sortie[champ] = brut
    return sortie


# ------------------------------------------------------------ rapprochement

@dataclass
class Ecart:
    champ: str
    xml: Any
    page: Any
    gravite: str          # "blocking" | "warning" | "info"
    explication: str


@dataclass
class Reconciliation:
    concordant: bool
    ecarts: list[Ecart] = field(default_factory=list)
    compares: list[str] = field(default_factory=list)
    non_verifies: list[str] = field(default_factory=list)
    xml: dict[str, Any] = field(default_factory=dict)

    @property
    def bloquant(self) -> bool:
        return any(e.gravite == "blocking" for e in self.ecarts)

    def resume(self) -> str:
        if not self.compares:
            return ("Nothing could be compared: the page yielded none of the "
                    "fields present in the XML.")
        if self.concordant:
            return (f"XML and visual layer agree on {len(self.compares)} "
                    f"field(s): {', '.join(self.compares)}.")
        b = sum(1 for e in self.ecarts if e.gravite == "blocking")
        return (f"{len(self.ecarts)} discrepancy(ies) between the XML and the "
                f"page, {b} blocking. The XML is what your accounting system "
                f"will ingest; the page is what a human, an auditor and a court "
                f"will read.")


def _normaliser_texte(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# Un montant lu par OCR peut différer d'un centime par arrondi d'affichage.
# Au-delà, ce n'est plus un arrondi : c'est un désaccord.
TOLERANCE = Decimal("0.01")


def reconcilier(xml: bytes, champs_page: dict[str, Any]) -> Reconciliation:
    """Compare le XML embarqué aux champs extraits de l'image.

    `champs_page` est la sortie de l'extraction OCR : {champ: {value, found}}.
    Un champ non trouvé sur la page n'est pas un écart — c'est une absence de
    preuve, et on le dit séparément.
    """
    try:
        x = lire_xml(xml)
    except ET.ParseError as e:
        return Reconciliation(
            concordant=False,
            ecarts=[Ecart("_xml", None, None, "blocking",
                          f"Embedded XML is not well-formed: {e}")])

    def page(champ):
        c = champs_page.get(champ) or {}
        return c.get("value") if c.get("found") else None

    ecarts: list[Ecart] = []
    compares: list[str] = []
    non_verifies: list[str] = []

    # --- montants : le cœur du sujet ------------------------------------
    for champ, libelle in (("total_incl_vat", "Total including VAT"),
                           ("total_excl_vat", "Total excluding VAT"),
                           ("vat_amount", "VAT amount")):
        vx, vp = x.get(champ), page(champ)
        if vx is None or vp is None:
            non_verifies.append(champ)
            continue
        compares.append(champ)
        dp = Decimal(str(vp)).quantize(Decimal("0.01"))
        ecart = abs(vx - dp)
        if ecart > TOLERANCE:
            ecarts.append(Ecart(
                champ, str(vx), str(dp), "blocking",
                f"{libelle}: the XML declares {vx} while the page shows {dp} "
                f"(difference {ecart}). Your ledger would record one number and "
                f"your auditor would read another."))

    # --- identite du document -------------------------------------------
    for champ, libelle, gravite in (("invoice_number", "Invoice number", "blocking"),
                                    ("invoice_date", "Invoice date", "warning")):
        vx, vp = x.get(champ), page(champ)
        if not vx or not vp:
            non_verifies.append(champ)
            continue
        compares.append(champ)
        if _normaliser_texte(str(vx)) != _normaliser_texte(str(vp)):
            ecarts.append(Ecart(
                champ, vx, vp, gravite,
                f"{libelle}: XML says {vx!r}, page shows {vp!r}. A mismatch here "
                f"breaks reconciliation with the supplier and duplicate "
                f"detection."))

    # --- devise ----------------------------------------------------------
    vx, vp = x.get("currency"), page("currency")
    if vx and vp:
        compares.append("currency")
        if vx.upper() != str(vp).upper():
            ecarts.append(Ecart("currency", vx, vp, "blocking",
                                f"Currency: XML {vx}, page {vp}."))
    else:
        non_verifies.append("currency")

    # --- coherence interne du XML lui-meme --------------------------------
    ht, tva, ttc = x.get("total_excl_vat"), x.get("vat_amount"), x.get("total_incl_vat")
    if None not in (ht, tva, ttc) and abs((ht + tva) - ttc) > TOLERANCE:
        ecarts.append(Ecart(
            "_xml_totals", f"{ht} + {tva}", str(ttc), "blocking",
            f"The XML does not balance on its own: {ht} + {tva} ≠ {ttc}. This is "
            f"an issuer error, independent of the visual layer."))

    return Reconciliation(
        concordant=not ecarts, ecarts=ecarts, compares=compares,
        non_verifies=sorted(set(non_verifies)), xml=x)
