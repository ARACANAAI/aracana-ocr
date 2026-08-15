# -*- coding: utf-8 -*-
"""Export FEC : le fichier est relu et contrôlé comme le ferait un tiers.

Le FEC est la seule sortie du système qui entre dans le grand livre d'un
client et se retrouve, telle quelle, devant l'administration fiscale. Les
tests y sont donc écrits à l'envers des autres : on ne vérifie pas d'abord
que le bon cas marche, on vérifie d'abord que les mauvais cas ne sortent
RIEN.
"""
import io
from datetime import date
from decimal import Decimal

from contexte import Compteur, sortie_utf8

sortie_utf8()

from aracana.ledger import (  # noqa: E402
    COLONNES_FEC, ExportImpossible, Ligne, PlanComptable, ecrire_fec,
    ecriture_achat, ecritures_csv, incoherences, nom_fichier, numeroter, relire)

_C = Compteur()
check = _C.check


def facture(**surcharge):
    base = {"invoice_number": "FA-2026-0114", "invoice_date": "12/03/2026",
            "total_excl_vat": 1250.00, "vat_amount": 250.00,
            "total_incl_vat": 1500.00, "seller_name": "SAS Exemple",
            "vat_number": "FR63441639465", "currency": "EUR"}
    base.update(surcharge)
    return {k: {"value": v, "found": v is not None} for k, v in base.items()}


def refuse(nom, appel, fragment=""):
    try:
        appel()
    except ExportImpossible as e:
        ok = fragment.lower() in str(e).lower()
        check(nom, ok, f"message inattendu : {e}")
    else:
        check(nom, False, "AUCUNE exception : le fichier aurait été écrit")


# ================================================== ce qui doit être refusé
print("=== Refus : rien ne sort plutôt qu'un grand livre faux ===")
refuse("écriture qui ne balance pas",
       lambda: ecriture_achat(facture(total_incl_vat=1600.00), numero="A1"),
       "ne balance pas")
refuse("facture sans numéro",
       lambda: ecriture_achat(facture(invoice_number=None), numero="A1"),
       "numéro de facture")
refuse("montant total nul",
       lambda: ecriture_achat(facture(total_excl_vat=0, vat_amount=0,
                                      total_incl_vat=0), numero="A1"),
       "non exploitable")
refuse("date illisible",
       lambda: ecriture_achat(facture(invoice_date="le 12 du mois"),
                              numero="A1"),
       "date illisible")
refuse("montant manquant",
       lambda: ecriture_achat(facture(vat_amount=None), numero="A1"),
       "manquant")
refuse("SIREN invalide dans le nom de fichier",
       lambda: nom_fichier("4416394", date(2026, 12, 31)),
       "9 chiffres")

# ====================================================== écriture nominale
print("\n=== Écriture d'achat : trois lignes en partie double ===")
lignes = ecriture_achat(facture(), numero=numeroter("AC", 1))
check("trois lignes", len(lignes) == 3, len(lignes))
check("numérotation sur six chiffres", lignes[0].ecriture_num == "AC000001",
      lignes[0].ecriture_num)
check("charge au débit du 606100",
      lignes[0].compte_num == "606100" and lignes[0].debit == Decimal("1250.00"))
check("TVA déductible au 445660",
      lignes[1].compte_num == "445660" and lignes[1].debit == Decimal("250.00"))
check("fournisseur au crédit du 401000",
      lignes[2].compte_num == "401000" and lignes[2].credit == Decimal("1500.00"))
check("compte auxiliaire = identifiant fournisseur",
      lignes[2].comp_aux_num == "FR63441639465", lignes[2].comp_aux_num)
check("date convertie en AAAAMMJJ", lignes[0].ecriture_date == "20260312",
      lignes[0].ecriture_date)
check("débit = crédit",
      sum(l.debit for l in lignes) == sum(l.credit for l in lignes))
check("aucune incohérence détectée", incoherences(lignes) == [],
      incoherences(lignes))

print("\n--- facture sans TVA (autoliquidation, franchise) : deux lignes ---")
sansTVA = ecriture_achat(facture(vat_amount=0, total_excl_vat=1500.00),
                         numero="AC000002")
check("deux lignes seulement", len(sansTVA) == 2, len(sansTVA))
check("toujours équilibrée", incoherences(sansTVA) == [], incoherences(sansTVA))

print("\n--- plan comptable surchargé par fournisseur ---")
plan = PlanComptable(charge_par_fournisseur={"FR63441639465": "622600"},
                     journal_code="ACH", journal_lib="Journal des achats")
sur = ecriture_achat(facture(), numero="ACH000001", plan=plan)
check("compte de charge dédié utilisé", sur[0].compte_num == "622600",
      sur[0].compte_num)
check("journal repris", sur[0].journal_code == "ACH")

print("\n--- facture en devise : colonnes Montantdevise et Idevise remplies ---")
chf = ecriture_achat(facture(currency="CHF", total_excl_vat=1000.00,
                             vat_amount=81.00, total_incl_vat=1081.00),
                     numero="AC000003")
cellules = chf[-1].cellules()
check("Idevise = CHF", cellules["Idevise"] == "CHF", cellules["Idevise"])
check("Montantdevise renseigné", cellules["Montantdevise"] == "1081,00",
      cellules["Montantdevise"])
eur = ecriture_achat(facture(), numero="AC000004")[-1].cellules()
check("aucune colonne devise en euros",
      eur["Idevise"] == "" and eur["Montantdevise"] == "")

# ============================================== hygiène des séparateurs
print("\n=== Une tabulation dans un nom décalerait toutes les colonnes ===")
sale = ecriture_achat(
    facture(seller_name="SAS\tExemple\r\nSiège social"), numero="AC000005")
cellules = sale[-1].cellules()
check("tabulation neutralisée", "\t" not in cellules["CompAuxLib"],
      repr(cellules["CompAuxLib"]))
check("retour ligne neutralisé", "\n" not in cellules["EcritureLib"])
check("le nom reste lisible", "SAS Exemple" in cellules["CompAuxLib"],
      cellules["CompAuxLib"])

# ================================================== écriture du fichier
print("\n=== Fichier écrit, puis relu depuis le texte ===")
toutes = (ecriture_achat(facture(), numero="AC000001")
          + ecriture_achat(facture(invoice_number="FA-2026-0115",
                                   total_excl_vat=800.00, vat_amount=160.00,
                                   total_incl_vat=960.00), numero="AC000002"))
flux = io.StringIO()
n = ecrire_fec(toutes, flux)
contenu = flux.getvalue()
check("six lignes écrites", n == 6, n)

premiere = contenu.split("\r\n")[0]
check("en-tête exact, dans l'ordre légal",
      premiere.split("\t") == list(COLONNES_FEC))
check("dix-huit colonnes", len(COLONNES_FEC) == 18, len(COLONNES_FEC))
check("séparateur tabulation", premiere.count("\t") == 17)
check("fin de ligne CRLF", contenu.endswith("\r\n") and "\r\n" in contenu)
check("virgule décimale, jamais de point",
      "1250,00" in contenu and "1250.00" not in contenu)

enregistrements, ennuis = relire(contenu)
check("relecture sans grief", ennuis == [], ennuis)
check("six enregistrements relus", len(enregistrements) == 6, len(enregistrements))
check("les deux écritures sont présentes",
      {e["EcritureNum"] for e in enregistrements} == {"AC000001", "AC000002"})
check("PieceRef porte le numéro de facture",
      {e["PieceRef"] for e in enregistrements} == {"FA-2026-0114", "FA-2026-0115"})

print("\n--- un déséquilibre bloque l'écriture du fichier entier ---")
casse = list(toutes)
casse[-1] = Ligne(**{**casse[-1].__dict__, "credit": Decimal("999.00")})
refuse("le fichier n'est pas écrit", lambda: ecrire_fec(casse, io.StringIO()),
       "déséquilibrée")
vide = io.StringIO()
try:
    ecrire_fec(casse, vide)
except ExportImpossible:
    pass
check("et le flux est resté vide", vide.getvalue() == "", repr(vide.getvalue()))

print("\n--- la relecture attrape ce que l'écriture aurait laissé passer ---")
falsifie = contenu.replace("1500,00", "1501,00", 1)
_, ennuis = relire(falsifie)
check("un centime déplacé après coup est vu", ennuis != [], ennuis)
_, ennuis = relire("JournalCode\tJournalLib\r\n")
check("en-tête tronqué rejeté", ennuis and "En-tête" in ennuis[0], ennuis)

# ============================================================ nom de fichier
print("\n=== Nommage imposé : SIRENFECAAAAMMJJ.txt ===")
check("clôture au 31/12/2026",
      nom_fichier("441 639 465", date(2026, 12, 31)) == "441639465FEC20261231.txt",
      nom_fichier("441 639 465", date(2026, 12, 31)))
check("clôture donnée en texte",
      nom_fichier("441639465", "31/12/2026") == "441639465FEC20261231.txt")

# =================================================================== Suisse
print("\n=== Hors de France : CSV point-virgule, mêmes écritures ===")
flux = io.StringIO()
ecritures_csv(chf, flux)
csv_contenu = flux.getvalue()
check("séparateur point-virgule", csv_contenu.split("\r\n")[0].count(";") == 17)
check("mêmes colonnes que le FEC",
      csv_contenu.split("\r\n")[0].split(";") == list(COLONNES_FEC))
check("montant en CHF conservé", "CHF" in csv_contenu)

import sys  # noqa: E402

sys.exit(_C.bilan())
