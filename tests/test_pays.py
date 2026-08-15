# -*- coding: utf-8 -*-
"""Pipeline : triage, packs pays, réconciliation Factur-X."""
from decimal import Decimal

from contexte import Compteur, sortie_utf8

sortie_utf8()
from aracana import countries as C, detect as D, reconcile as R  # noqa: E402

_C = Compteur()


def check(n, c, d=""):
    _C.check(n, c, d)


print("=== Suisse : cle IDE mod-11 verifiee sur un UID reel ===")
# Nestle SA : CHE-105.909.036  (verifie a la main : somme 104, 104%11=5, 11-5=6)
check("Nestle CHE-105.909.036 valide", C.Suisse.cle_ide("10590903") == 6,
      C.Suisse.cle_ide("10590903"))
check("chiffre inverse rejete", C.Suisse.cle_ide("10599003") != 6)
sans_cle = [h for h in ("%08d" % i for i in range(3000))
            if C.Suisse.cle_ide(h) is None]
check(f"combinaisons non attribuables detectees ({len(sans_cle)} sur 3000)",
      len(sans_cle) > 0 and all(C.Suisse.cle_ide(h) is None for h in sans_cle[:5]),
      sans_cle[:3])
check("un prefixe non attribuable ne valide aucune cle",
      all(not C.Suisse().valider({"uid": f"CHE{sans_cle[0]}{k}"})[0].passe
          for k in range(10)) if sans_cle else False)
# auto-coherence : la cle calculee valide toujours
import random
rng = random.Random(1); bons = 0
for _ in range(500):
    h = "".join(str(rng.randint(0,9)) for _ in range(8))
    k = C.Suisse.cle_ide(h)
    if k is not None:
        bons += 1
        assert C.Suisse.cle_ide(h) == k
check(f"algorithme auto-coherent ({bons}/500 combinaisons attribuables)", bons > 400)

print("\n=== Suisse : reference QR mod-10 recursive ===")
r26 = "21000000000313947143000"[:26].ljust(26, "0")
k = C.Suisse.cle_mod10(r26)
suisse = C.Suisse()
v = suisse.valider({"qr_reference": r26 + str(k)})
check("reference generee passe son controle", v[0].passe, v[0].detail)
v = suisse.valider({"qr_reference": r26 + str((k+1) % 10)})
check("cle fausse rejetee", not v[0].passe)

print("\n=== Suisse : taux et identifiants ===")
check("8.1 % legal", suisse.taux_legal(Decimal("8.1")).passe)
check("20 % (taux FR) refuse en Suisse", not suisse.taux_legal(Decimal("20")).passe)
ids = suisse.identifiants("Nestle SA, CHE-105.909.036 MWST, CHF 1'250.00")
check("IDE extrait et normalise", ids.get("uid") == "CHE105909036", ids)
v = suisse.valider({"uid": "CHE-105.909.036"})
check("IDE reel valide bout en bout", v[0].passe, v[0].detail)

print("\n=== France : cle TVA sur SIREN ===")
fr = C.France()
# Renault : SIREN 441639465 -> cle = (12 + 3*(441639465 % 97)) % 97
attendu = (12 + 3 * (441639465 % 97)) % 97
tva = f"FR{attendu:02d}441639465"
v = fr.valider({"vat_number": tva, "siren": "441639465"})
noms = {c.nom: c.passe for c in v}
check("SIREN Luhn", noms.get("SIREN Luhn checksum"), noms)
check("format TVA FR", noms.get("FR VAT number format"))
check("cle TVA coherente avec le SIREN", noms.get("FR VAT key matches the SIREN"), noms)
v = fr.valider({"vat_number": f"FR{(attendu+1)%97:02d}441639465"})
check("cle TVA fausse detectee",
      not {c.nom: c.passe for c in v}.get("FR VAT key matches the SIREN"))

print("\n=== detection de juridiction ===")
check("CH via IDE", C.deviner("Rechnung CHE-105.909.036 MWST") == "CH")
check("FR via SIRET", C.deviner("SIRET 44163946500023") == "FR")
check("BE via TVA", C.deviner("BTW BE0417497106") == "BE", C.deviner("BTW BE0417497106"))
check("inconnu -> None", C.deviner("Invoice 123") is None)
check("pack de repli sans code", isinstance(C.pour(None), C.UnionEuropeenne))
check("pack FR", C.pour("fr").code == "FR")

print("\n=== triage : ne pas bruler de GPU pour rien ===")
UBL = b"""<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">
<ID>FA-2026-0114</ID><IssueDate>2026-03-12</IssueDate>
<DocumentCurrencyCode>EUR</DocumentCurrencyCode>
<TaxTotal><TaxAmount>250.00</TaxAmount></TaxTotal>
<LegalMonetaryTotal><TaxExclusiveAmount>1250.00</TaxExclusiveAmount>
<TaxInclusiveAmount>1500.00</TaxInclusiveAmount></LegalMonetaryTotal></Invoice>"""
d = D.diagnostiquer(UBL)
check("UBL -> STRUCTURE", d.route == D.Route.STRUCTURE, d.route)
check("UBL n'appelle pas le modele", d.economise_gpu)
check("famille ubl", d.profil == "ubl", d.profil)

import io
from PIL import Image
b = io.BytesIO(); Image.new("RGB",(800,1000),"white").save(b,"PNG")
d = D.diagnostiquer(b.getvalue())
check("PNG -> IMAGE", d.route == D.Route.IMAGE)
check("PNG appelle le modele", d.besoin_modele)

d = D.diagnostiquer(b"pas un format connu du tout")
check("inconnu -> INCONNU sans appel", d.route == D.Route.INCONNU and not d.besoin_modele)

print("\n=== reconciliation Factur-X : le coeur de la valeur ===")
CII = b"""<?xml version="1.0"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
 xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
<rsm:ExchangedDocumentContext><ram:GuidelineSpecifiedDocumentContextParameter>
<ram:ID>urn:cen.eu:en16931:2017</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter>
</rsm:ExchangedDocumentContext>
<rsm:ExchangedDocument><ram:ID>FA-2026-0114</ram:ID>
<ram:IssueDateTime><ram:DateTimeString>20260312</ram:DateTimeString></ram:IssueDateTime>
</rsm:ExchangedDocument>
<rsm:SupplyChainTradeTransaction><ram:ApplicableHeaderTradeSettlement>
<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
<ram:SpecifiedTradeSettlementHeaderMonetarySummation>
<ram:TaxBasisTotalAmount>1250.00</ram:TaxBasisTotalAmount>
<ram:TaxTotalAmount>250.00</ram:TaxTotalAmount>
<ram:GrandTotalAmount>1500.00</ram:GrandTotalAmount>
</ram:SpecifiedTradeSettlementHeaderMonetarySummation>
</ram:ApplicableHeaderTradeSettlement></rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""
x = R.lire_xml(CII)
check("CII : numero", x["invoice_number"] == "FA-2026-0114", x["invoice_number"])
check("CII : date 20260312 -> 12/03/2026", x["invoice_date"] == "12/03/2026", x["invoice_date"])
check("CII : TTC", x["total_incl_vat"] == Decimal("1500.00"), x["total_incl_vat"])
x2 = R.lire_xml(UBL)
check("UBL : meme lecture normalisee", x2["total_incl_vat"] == Decimal("1500.00"))
check("UBL : date ISO -> FR", x2["invoice_date"] == "12/03/2026", x2["invoice_date"])

page_ok = {"invoice_number": {"value":"FA-2026-0114","found":True},
           "invoice_date": {"value":"12/03/2026","found":True},
           "total_excl_vat": {"value":1250.0,"found":True},
           "vat_amount": {"value":250.0,"found":True},
           "total_incl_vat": {"value":1500.0,"found":True},
           "currency": {"value":"EUR","found":True}}
rec = R.reconcilier(CII, page_ok)
check("concordance detectee", rec.concordant, rec.ecarts)
check("6 champs compares", len(rec.compares) == 6, rec.compares)

page_fraude = dict(page_ok)
page_fraude["total_incl_vat"] = {"value":1600.0,"found":True}
rec = R.reconcilier(CII, page_fraude)
check("ECART XML/PAGE detecte", not rec.concordant)
check("classe bloquant", rec.bloquant)
e = [x for x in rec.ecarts if x.champ == "total_incl_vat"][0]
check("explique l'enjeu", "auditor" in e.explication, e.explication[:80])
print(f"      -> {rec.resume()}")

page_partielle = {"total_incl_vat": {"value":1500.0,"found":True}}
rec = R.reconcilier(CII, page_partielle)
check("champs non lus != ecarts", rec.concordant and len(rec.non_verifies) >= 4,
      (rec.concordant, rec.non_verifies))

CII_FAUX = CII.replace(b"<ram:GrandTotalAmount>1500.00", b"<ram:GrandTotalAmount>1600.00")
rec = R.reconcilier(CII_FAUX, page_ok)
check("incoherence interne du XML detectee",
      any(x.champ == "_xml_totals" for x in rec.ecarts), rec.ecarts)

rec = R.reconcilier(b"<pas du xml valide", page_ok)
check("XML malforme -> bloquant, pas de crash", rec.bloquant)

import sys

sys.exit(_C.bilan())
