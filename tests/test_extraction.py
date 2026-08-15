# -*- coding: utf-8 -*-
"""Extraction multilingue : la zone de lancement, pas seulement la France.

POURQUOI CETTE SUITE EXISTE
  Elle est née d'un bug. Le test « Suisse » de l'orchestrateur passait au vert
  parce qu'il était écrit en français. Réécrit en allemand — la langue de deux
  tiers des factures suisses — il a révélé trois défauts d'un coup :

    1. « Total exkl. MWST 1'000.00 » était lu comme un MONTANT de TVA de 1000,
       parce que l'étiquette « MWST » y figure. L'équilibre HT + TVA = TTC
       tombait, et toute facture allemande partait en revue humaine.
    2. « 1'000.00 » était lu 1,00 : l'apostrophe suisse de milliers n'était pas
       reconnue. Une facture de dix mille francs devenait une facture de dix.
    3. « Rechnung Nr. » ne donnait aucun numéro de facture, donc aucune
       détection de doublon.

  Aucun de ces trois défauts n'était visible en français. Chacun aurait été
  découvert par un client suisse, en production, sur ses propres factures.

  Cette suite fige les trois cas et tous leurs voisins. Elle est le contrat de
  ce que l'extraction sait lire hors de France.
"""
from contexte import Compteur, charger_serveur_mcp, sortie_utf8

sortie_utf8()
S = charger_serveur_mcp()

from aracana import extract as EXTRAIT  # noqa: E402
from aracana.countries import LONGUEUR_IBAN, Pays  # noqa: E402

_C = Compteur()
check = _C.check


def champ(texte, nom):
    c = S.extraire_champs(texte)[nom]
    return c["value"] if c["found"] else None


# ============================================================ formats de nombre
print("=== Formats de nombres : trois conventions coexistent dans la zone ===")
for brut, attendu, ou in [
    ("1 234,56", 1234.56, "France, espace"),
    ("1 234,56", 1234.56, "France, espace insécable"),
    ("1 234,56", 1234.56, "France, espace fine"),
    ("1,234.56", 1234.56, "anglais"),
    ("1.234,56", 1234.56, "allemand"),
    ("1'234.56", 1234.56, "Suisse, apostrophe droite"),
    ("1’234.56", 1234.56, "Suisse, apostrophe typographique"),
    ("12'345'678.90", 12345678.90, "Suisse, deux séparateurs"),
    ("81.00", 81.0, "sans séparateur"),
]:
    check(f"{brut!r} -> {attendu} ({ou})", S._nombre(brut) == attendu,
          S._nombre(brut))

# ====================================================== numéro de facture
print("\n=== Numéro de facture : le « n° » survit à l'OCR et aux frontières ===")
for texte, attendu in [
    ("Facture n° FA-2026-0114", "FA-2026-0114"),
    ("Facture nº FA-2026-0114", "FA-2026-0114"),          # ordinal masculin
    ("Facture n˚ FA-2026-0114", "FA-2026-0114"),          # rond en chef
    ("Facture no FA-2026-0114", "FA-2026-0114"),
    ("Facture n. FA-2026-0114", "FA-2026-0114"),
    ("Facture N°2026-0114", "2026-0114"),                 # collé au chiffre
    ("FACTURE NUMÉRO : FA-2026-0114", "FA-2026-0114"),
    ("N° de facture : FA-2026-0114", "FA-2026-0114"),
    ("Rechnung Nr. R-2026-88", "R-2026-88"),              # Suisse alémanique
    ("Rechnung Nr R-2026-88", "R-2026-88"),
    ("Fattura n. 2026/114", "2026/114"),                  # Tessin, Italie
    ("Factura nº 2026-114", "2026-114"),                  # Espagne
    ("Invoice #INV-99012", "INV-99012"),
    ("Invoice number: INV-99012", "INV-99012"),
    ("Facture FA-2026-0114", "FA-2026-0114"),             # sans marqueur
]:
    check(f"{texte!r} -> {attendu}", champ(texte, "invoice_number") == attendu,
          champ(texte, "invoice_number"))

print("\n--- et ne fabrique rien quand il n'y a rien ---")
for texte in [
    "Facture nominative établie ce jour",     # « no » de « nominative »
    "Facture du 12/03/2026",                  # une date n'est pas un numéro
    "Facture acquittée",
    "Facture datée du 12 mars 2026",
]:
    v = champ(texte, "invoice_number")
    check(f"{texte!r} -> rien", v is None, f"a produit {v!r}")

# =========================================================== montant de TVA
print("\n=== Montant de TVA : l'étiquette dans un libellé de TOTAL n'en est pas une ===")
PIEGES = [
    ("Total exkl. MWST 1'000.00", None, "allemand, HT"),
    ("Total inkl. MWST 1'081.00", None, "allemand, TTC"),
    ("Total TVA comprise 1 500,00 €", None, "français, TTC"),
    ("Totale IVA inclusa 1.220,00", None, "italien, TTC"),
    ("Total excl. VAT 1,000.00", None, "anglais, HT"),
    ("Montant hors TVA 1 000,00", None, "français, HT"),
]
for texte, attendu, ou in PIEGES:
    v = champ(texte, "vat_amount")
    check(f"{ou} : {texte!r} n'est pas un montant de TVA", v == attendu,
          f"a produit {v}")

print("\n--- mais un vrai montant de TVA est bien lu, dans les quatre langues ---")
for texte, attendu, ou in [
    ("TVA 20 % 250,00 €", 250.0, "français avec taux intercalé"),
    ("Montant TVA : 250,00", 250.0, "français"),
    ("MWST 8.1 % 81.00", 81.0, "allemand"),
    ("MwSt. 19 % 190,00", 190.0, "Allemagne"),
    ("IVA 22% 220,00", 220.0, "italien"),
    ("VAT amount 250.00", 250.0, "anglais"),
]:
    v = champ(texte, "vat_amount")
    check(f"{ou} : {texte!r} -> {attendu}", v == attendu, v)

print("\n--- les étiquettes de TVA sont bornées : pas de faux positif dans un mot ---")
for texte, ou in [
    ("I bicchieri arrivano 20 % rotti  Totale 12,00", "« iva » dans « arrivano »"),
    ("Facture du 3 August 2026  Total 20,00", "« ust » dans « August »"),
]:
    v = champ(texte, "vat_rate")
    check(f"{ou} -> aucun taux", v is None, f"a produit {v}")

# ====================================================================== IBAN
print("\n=== IBAN : longueur ISO 13616 + clé mod-97, ou rien ===")
IBANS_VALIDES = [
    ("CH93 0076 2011 6238 5295 7", "CH9300762011623852957", "Suisse, 21 car."),
    ("CH9300762011623852957", "CH9300762011623852957", "Suisse, sans espaces"),
    ("FR14 2004 1010 0505 0001 3M02 606", "FR1420041010050500013M02606", "France, 27"),
    ("DE89 3704 0044 0532 0130 00", "DE89370400440532013000", "Allemagne, 22"),
    ("IT60 X054 2811 1010 0000 0123 456", "IT60X0542811101000000123456", "Italie, 27"),
    ("BE68 5390 0754 7034", "BE68539007547034", "Belgique, 16"),
]
for brut, attendu, ou in IBANS_VALIDES:
    v = champ(f"Coordonnées bancaires — IBAN {brut}", "iban")
    check(f"{ou} : lu en entier", v == attendu, v)
    check(f"{ou} : clé validée par le pipeline", Pays.iban_valide(brut))
    check(f"{ou} : longueur conforme à la table",
          len(attendu) == LONGUEUR_IBAN[attendu[:2]])

print("\n--- un IBAN abîmé est REFUSÉ, jamais rendu approximatif ---")
for brut, ou in [
    ("CH93 0076 2011 6238 5295", "suisse tronqué d'un caractère"),
    ("CH93 0076 2011 6238 5295 8", "dernier chiffre substitué"),
    ("CH39 0076 2011 6238 5295 7", "clé de contrôle transposée"),
    ("DE89 3704 0044 0532 0130", "allemand trop court"),
]:
    v = champ(f"IBAN {brut}", "iban")
    check(f"{ou} -> refusé", v is None, f"a produit {v!r}")
    check(f"{ou} -> refusé aussi par le pipeline", not Pays.iban_valide(brut))

print("\n--- un IBAN entouré d'autre chose reste lisible ---")
v = champ("IBAN CH93 0076 2011 6238 5295 7 BIC POFICHBEXXX", "iban")
check("IBAN + BIC collés", v == "CH9300762011623852957", v)

# Le cas qui a coûté le plus cher : un numéro de TVA a la même forme AA99
# qu'un début d'IBAN, et il figure AVANT les coordonnées bancaires sur
# pratiquement toute facture. Un lecteur gourmand partait de « FR63… »,
# absorbait l'IBAN, échouait, et reprenait après lui.
AVEC_TVA = """SAS EXEMPLE  SIREN : 441 639 465  TVA : FR63441639465
IBAN CH93 0076 2011 6238 5295 7
Facture n° FA-2026-0114"""
check("un numéro de TVA placé avant ne masque plus l'IBAN",
      champ(AVEC_TVA, "iban") == "CH9300762011623852957",
      champ(AVEC_TVA, "iban"))
check("et le numéro de TVA reste lu",
      champ(AVEC_TVA, "vat_number") == "FR63441639465",
      champ(AVEC_TVA, "vat_number"))
check("sur une seule ligne aussi",
      champ("TVA FR63441639465 - IBAN DE89 3704 0044 0532 0130 00", "iban")
      == "DE89370400440532013000",
      champ("TVA FR63441639465 - IBAN DE89 3704 0044 0532 0130 00", "iban"))
check("un IBAN ne se poursuit pas à la ligne suivante",
      champ("IBAN DE89 3704 0044 0532 0130\n00 rue de la Paix", "iban") is None,
      champ("IBAN DE89 3704 0044 0532 0130\n00 rue de la Paix", "iban"))

print("\n--- une seule implémentation, plus deux copies à synchroniser ---")
check("le serveur MCP réexporte la table du pipeline",
      S.LONGUEUR_IBAN is LONGUEUR_IBAN)
check("et la même fonction de validation, pas une jumelle",
      S._iban_valide is Pays.iban_valide)
check("l'extracteur est bien celui du pipeline",
      S.extraire_champs is EXTRAIT.extraire_champs)

# ============================================== facture suisse complète
print("\n=== La facture qui a révélé les trois défauts, en entier ===")
SUISSE = """Muster AG  CHE-105.909.036 MWST
Rechnung Nr. R-2026-88   Datum: 12.03.2026
IBAN CH93 0076 2011 6238 5295 7
Total exkl. MWST 1'000.00   MWST 8.1 % 81.00   Total inkl. MWST 1'081.00 CHF"""
c = S.extraire_champs(SUISSE)
attendus = {"invoice_number": "R-2026-88", "total_excl_vat": 1000.0,
            "vat_amount": 81.0, "total_incl_vat": 1081.0, "vat_rate": 8.1,
            "currency": "CHF", "iban": "CH9300762011623852957"}
for k, attendu in attendus.items():
    check(f"{k} = {attendu}", c[k]["found"] and c[k]["value"] == attendu,
          c[k]["value"])
check("HT + TVA = TTC",
      round(c["total_excl_vat"]["value"] + c["vat_amount"]["value"], 2)
      == c["total_incl_vat"]["value"])
check("aucun SIREN inventé sur une facture suisse",
      not c["siren"]["found"] and not c["siret"]["found"])


# ============================================== date de facture, millesime
print("\n=== Date de facture : une page en porte toujours plusieurs ===")
# Ces cas viennent de documents réels. Sur un exemple officiel du FNFE, nous
# rendions 11/03/2017 là où la page annonçait 03/11/2017 : la première date
# rencontrée n'est pas la date de facture. Une facture porte aussi une date
# d'échéance, souvent une date de livraison, parfois une date de commande.
for texte, attendu, ou in [
    ("Facture n° FA-1  Date de facture 03/11/2017  Échéance 03/12/2017",
     "03/11/2017", "étiquette, échéance après"),
    ("Date d'échéance 30/12/2017\nDate de facture 05/11/2017",
     "05/11/2017", "échéance en premier dans le texte"),
    ("Rechnungsdatum 05.03.2018   Fälligkeit 04.04.2018",
     "05.03.2018", "allemand"),
    ("Invoice date 2018-03-05", "2018-03-05", "ISO complet, pas tronqué"),
    ("Livraison le 01/02/2026\nFacturé le 15/02/2026",
     "15/02/2026", "livraison écartée, sans déborder de ligne"),
    ("Due date 30/12/2017\nIssued on 05/11/2017", "05/11/2017", "anglais"),
]:
    check(f"{ou} -> {attendu}", champ(texte, "invoice_date") == attendu,
          champ(texte, "invoice_date"))

print("\n--- un millésime nu n'est pas un numéro de facture ---")
for texte, attendu in [("Rechnung 2010 Betriebskosten", None),
                       ("Facture 2026", None),
                       ("Rechnung 471102", "471102"),
                       ("Rechnung Nr. 2010", "2010")]:
    v = champ(texte, "invoice_number")
    check(f"{texte!r} -> {attendu}", v == attendu, v)

import sys  # noqa: E402

sys.exit(_C.bilan())
