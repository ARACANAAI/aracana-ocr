"""Extraction de champs depuis le texte d'une facture.

OÙ CE CODE VIVAIT, ET POURQUOI IL A DÉMÉNAGÉ
  Il était dans le serveur MCP. Trois appelants en ont besoin : le serveur MCP,
  la route `/v1/invoices` de l'API, et le traitement par lot. Or le serveur MCP
  importe le SDK `mcp` dès sa première ligne, et ce SDK n'est pas — n'a aucune
  raison d'être — dans l'image de l'API.

  Conséquence concrète, découverte en testant la route : `/v1/invoices`
  répondait 501 « pipeline indisponible » dans le conteneur livré, tout en
  fonctionnant en développement où le SDK est installé. Le pire type de panne :
  invisible chez soi, systématique chez le client.

  L'extracteur appartient donc au pipeline, qui ne dépend que de la
  bibliothèque standard. Le serveur MCP l'importe ; il n'en est plus le
  propriétaire.

CE QUE CE MODULE PROMET
  Chaque champ dit s'il a été trouvé. Aucune valeur n'est devinée : un champ
  absent ressort `found=False`, jamais rempli d'une approximation plausible.
  C'est ce qui permet aux contrôles en aval de distinguer « pas d'information »
  de « information fausse ».
"""
from __future__ import annotations

import re
from typing import Any

from .countries import LONGUEUR_IBAN, Pays

# ------------------------------------------------------------- extraction

MOIS = {"janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
        "décembre": 12}

# Separateurs de milliers rencontres en zone de lancement : espace, espace
# insecable, espace fine, point — et l'apostrophe suisse, droite ou typo-
# graphique (« 1'081.00 », « 1’081.00 »). Sans elle, tout montant suisse a
# quatre chiffres est lu a un millieme de sa valeur.
MILLIERS = r"[  . '’]"
RE_MONTANT = rf"(\d{{1,3}}(?:{MILLIERS}\d{{3}})*(?:[.,]\d{{2}})?|\d+[.,]\d{{2}})"
# Variante EXIGEANT deux decimales. Necessaire partout ou un taux peut se
# trouver entre le libelle et le montant : sur « TVA 20 %   250,00 € », un motif
# permissif capture « 20 » comme montant. Exiger les centimes leve l'ambiguite.
# Contrepartie assumee : une facture libellee « Total TTC 1500 € » sans centimes
# sera rapportee found=false plutot que devinee — c'est le bon sens de l'echec.
RE_MONTANT_STRICT = rf"(\d{{1,3}}(?:{MILLIERS}\d{{3}})*[.,]\d{{2}}|\d+[.,]\d{{2}})"

# La TVA change de nom a chaque frontiere, et la Suisse en emploie trois d'un
# coup : MWST en allemand, TVA en francais, IVA en italien. Un pack pays qui
# controlerait le taux legal suisse sans savoir lire « MWST » ne servirait a
# rien.
# Les gardes ne sont pas decoratives : « iva » vit dans « arrivano », « ust »
# dans « August ». Sans elles, un mot de corps de facture devient une etiquette
# de TVA et le montant capture derriere est faux — l'erreur la plus couteuse du
# lot, parce qu'elle est plausible.
ETIQ_TVA = (r"(?<![A-Za-z])(?:t\.?v\.?a\.?|mw\.?st\.?|mehrwertsteuer|iva|vat"
            r"|ust\.?)(?![A-Za-z])")


def _nombre(s: str) -> float | None:
    """Three conventions coexist in the launch zone and must not collide:
    French '1 234,56', English '1,234.56' and Swiss "1'234.56"."""
    if not s:
        return None
    t = re.sub(r"[\s'’]", "", s)
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") \
            else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


# --------------------------------------------------------- numéro de facture
#
# Un numéro de facture n'a aucune forme normalisée en Europe : « FA-2026-0114 »,
# « 2026/0114 », « INV0114 ». On ne peut donc le reconnaître que par son
# contexte — et le contexte est multilingue. La Suisse, à elle seule, facture en
# français, en allemand et en italien.
#
# Trois écueils, dans l'ordre où ils nous ont mordus :
#   1. le « ° » de « n° » ressort de l'OCR en « º » (ordinal masculin), « ˚ »
#      (rond en chef), « o », « . », ou disparaît ;
#   2. sans marqueur, « Facture du 12 mars 2026 » ferait passer une date pour un
#      numéro — d'où l'exigence d'un chiffre et le rejet explicite des dates ;
#   3. « Facture nominative » ne doit rien produire : le « no » de « nominative »
#      n'est pas un marqueur, d'où le séparateur obligatoire après celui-ci.
ETIQ_FACTURE = r"(?:facture|invoice|rechnung|fattura|factura)"
MARQUE_NUM = r"(?:n[°ºo˚]|n\s*\.|nr\s*\.?|num(?:[ée]ro)?|number|#)"
# Après le marqueur : un vrai séparateur, ou le début d'un nombre, ou un « # »
# collé. Sans cette contrainte, « no » avale n'importe quel mot.
SEP_NUM = r"(?:\s*[:.]\s*|\s+|(?<=#)|(?=\d))"
RE_NUM_DOC = r"([A-Za-z0-9][A-Za-z0-9\-/_.]{2,})"

MOTS_NON_NUMERO = {
    "originale", "acquittee", "acquittée", "proforma", "rectificative",
    "definitive", "définitive", "date", "dated", "total", "client",
}
RE_DATE_SEULE = re.compile(r"^\d{1,2}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?$")


def _numero_facture(t: str) -> tuple[str | None, bool]:
    """Le numéro de facture, ou rien — jamais une approximation.

    Un faux numéro est pire qu'un numéro absent : il fait échouer la détection
    de doublon et le rapprochement fournisseur, silencieusement.
    """
    for motif, exige_chiffre in (
        (rf"{ETIQ_FACTURE}\s*{MARQUE_NUM}{SEP_NUM}{RE_NUM_DOC}", False),
        (rf"{MARQUE_NUM}\s*(?:de\s+|der\s+)?{ETIQ_FACTURE}\s*:?\s*{RE_NUM_DOC}", False),
        # Dernier recours, sans marqueur : « Facture FA-2026-0114 ».
        (rf"{ETIQ_FACTURE}\s*:?\s+{RE_NUM_DOC}", True),
    ):
        for r in re.finditer(motif, t, re.I | re.M):
            v = r.group(1).strip(" .:,;-")
            if len(v) < 3 or v.lower() in MOTS_NON_NUMERO or RE_DATE_SEULE.match(v):
                continue
            if exige_chiffre:
                if not any(c.isdigit() for c in v):
                    continue
                # Un millésime nu n'est pas un numéro de facture. Sur une page
                # allemande, le repli sans marqueur avait pris « 2010 » là où
                # le XML annonçait « 471102 » : le mot « Rechnung » y était
                # suivi d'une année, pas d'une référence. Un vrai numéro
                # comporte presque toujours une lettre, un tiret, une barre —
                # ou davantage de chiffres.
                if re.fullmatch(r"(?:19|20)\d{2}", v):
                    continue
            return v, True
    return None, False


# ------------------------------------------------------------ montant de TVA
#
# Une etiquette de TVA precedee — ou suivie — d'un qualificatif d'inclusion ou
# d'exclusion ne NOMME pas un montant de TVA : elle QUALIFIE un total.
#   « Total exkl. MWST  1'000.00 »  est un HT, pas une TVA.
#   « Total inkl. MWST  1'081.00 »  est un TTC, pas une TVA.
#   « Total TVA comprise 1 500,00 » est un TTC, pas une TVA.
# Le motif naif capturait le total comme montant de TVA. L'equilibre
# HT + TVA = TTC tombait alors en echec, et toute facture allemande partait en
# revue humaine — c'est-a-dire la majorite du marche suisse, pays de lancement.
# Le francais nous avait epargne le bug : « Total HT » ne contient pas « TVA ».
QUALIF_AVANT = re.compile(
    r"(?:incl|inkl|inklusive|excl|exkl|exklusive|ohne|zzgl|hors|sans|plus)"
    r"\s*\.?\s*$", re.I)
QUALIF_APRES = re.compile(
    r"^\s*(?:comprises?|inclus\w*|inclusa|esclusa|included|excluded|incluse)",
    re.I)


# ---------------------------------------------------------------------- IBAN
#
# L'ancien motif « groupes de quatre » tronquait silencieusement tout IBAN dont
# la longueur n'est pas un multiple de quatre : « CH93 0076 2011 6238 5295 7 »
# ressortait amputé de son dernier caractère, et rapporté found=True. Un numéro
# de compte faux mais confiant est le pire résultat possible — pire qu'un champ
# vide, parce qu'il ne déclenche aucune alerte.
#
# La longueur par pays et la clé mod-97 vivent dans `countries.Pays` et sont
# utilisées ici telles quelles. Elles y étaient dupliquées tant que ce code
# habitait le serveur MCP, avec un test pour vérifier que les deux copies
# concordaient — un test qu'il valait mieux rendre inutile que faire passer.
_iban_valide = Pays.iban_valide


def _iban(t: str) -> tuple[str | None, bool]:
    n = Pays.lire_iban(t)
    return (n, True) if n else (None, False)


def _montant_tva(t: str) -> tuple[float | None, bool]:
    """Le montant de la TVA, en refusant les totaux qui portent son nom."""
    motifs = [
        rf"(?P<tag>{ETIQ_TVA})\s*(?:\d{{1,2}}(?:[.,]\d)?\s*%)?"
        rf"[^\d\n]{{0,25}}{RE_MONTANT_STRICT}",
        rf"(?P<tag>(?:vat|tax)\s+amount)[^\d\n]{{0,25}}{RE_MONTANT_STRICT}",
    ]
    for motif in motifs:
        for r in re.finditer(motif, t, re.I | re.M):
            debut, fin = r.start("tag"), r.end("tag")
            if QUALIF_AVANT.search(t[max(0, debut - 16):debut]):
                continue
            if QUALIF_APRES.match(t[fin:fin + 16]):
                continue
            return _nombre(r.groups()[-1]), True
    return None, False


# ------------------------------------------------------------ date de facture
#
# Une vraie facture porte PLUSIEURS dates : date de facture, date d'échéance,
# date de livraison, parfois date de commande. Prendre la première rencontrée
# revient à tirer au sort.
#
# Constaté sur un exemple officiel du FNFE : la page annonçait 03/11/2017 en
# date de facture, et nous rendions 11/03/2017 — une autre date de la page. La
# réconciliation a signalé l'écart contre le XML, ce qui a évité une écriture
# fausse, mais le champ était bel et bien mauvais.
#
# On cherche donc par ÉTIQUETTE d'abord, on écarte ensuite les dates
# explicitement rattachées à une échéance ou une livraison, et on ne retombe
# sur « la première date » qu'en dernier recours.
ETIQ_DATE_FACTURE = (
    r"(?:date\s+(?:de\s+)?(?:la\s+)?factur\w*|date\s+d['’]\s?émission"
    r"|émise?\s+le|factur[ée]e?\s+(?:le|du)|factur\w*\s+du"
    r"|rechnungsdatum|belegdatum|ausstellungsdatum"
    r"|invoice\s+date|date\s+of\s+issue|issued?\s+on"
    r"|data\s+fattura|fecha\s+(?:de\s+)?factura|datum)"
)
# Ces étiquettes désignent une AUTRE date. Une date qu'elles précèdent n'est
# jamais la date de facture.
ETIQ_AUTRE_DATE = (
    r"(?:échéance|echeance|à\s+payer\s+avant|payable\s+(?:le|avant)|due\s+date"
    r"|fällig\w*|faellig\w*|zahlbar\s+bis|livraison|delivery|leistungsdatum"
    r"|lieferdatum|commande|order\s+date|período|periode|vencimiento)"
)
# L'alternative ISO vient EN PREMIER, sinon `\d{1,2}` mord au milieu :
# « 2018-03-05 » ressortait « 18-03-05 », soit une date fausse et plausible —
# le pire des résultats.
RE_DATE_NUM = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})"


def _date_litterale(fragment: str) -> str | None:
    r = re.search(rf"\b(\d{{1,2}})\s+({'|'.join(MOIS)})\s+(\d{{4}})\b",
                  fragment, re.I)
    if not r:
        return None
    return f"{int(r.group(1)):02d}/{MOIS[r.group(2).lower()]:02d}/{r.group(3)}"


def _date_facture(t: str) -> tuple[str | None, bool]:
    # 1. une date immédiatement rattachée à une étiquette de date de facture
    for motif in (rf"{ETIQ_DATE_FACTURE}[^\d\n]{{0,20}}{RE_DATE_NUM}",
                  rf"{RE_DATE_NUM}[^\d\n]{{0,12}}{ETIQ_DATE_FACTURE}"):
        if m := re.search(motif, t, re.I):
            return m.group(1), True
    if m := re.search(rf"{ETIQ_DATE_FACTURE}[^\d\n]{{0,20}}"
                      rf"(\d{{1,2}}\s+(?:{'|'.join(MOIS)})\s+\d{{4}})", t, re.I):
        if (d := _date_litterale(m.group(1))):
            return d, True

    # 2. à défaut, la première date qui n'est PAS annoncée comme une échéance
    #    ou une livraison
    for m in re.finditer(RE_DATE_NUM, t):
        # Le contexte s'arrête au début de LIGNE. Une fenêtre de quarante
        # caractères remontait par-dessus le retour à la ligne et voyait
        # l'étiquette de la ligne précédente : sur « Livraison le 01/02\n
        # Facturé le 15/02 », les deux dates étaient écartées comme des
        # livraisons, et le repli final rendait la première — exactement celle
        # qu'il fallait éviter.
        debut_ligne = t.rfind("\n", 0, m.start()) + 1
        avant = t[max(debut_ligne, m.start() - 40):m.start()]
        if not re.search(ETIQ_AUTRE_DATE, avant, re.I):
            return m.group(1), True

    # 3. en dernier recours, une date littérale, puis n'importe laquelle
    if (d := _date_litterale(t)):
        return d, True
    if m := re.search(RE_DATE_NUM, t):
        return m.group(1), True
    return None, False


def _chercher(texte: str, motifs: list[str], nombre: bool = False):
    for m in motifs:
        r = re.search(m, texte, re.I | re.M)
        if r:
            v = r.group(1).strip()
            return (_nombre(v) if nombre else v), True
    return None, False


def extraire_champs(texte: str) -> dict[str, Any]:
    """Pattern extraction over the parsed text. Every field reports whether it
    was actually found."""
    t = re.sub(r"[ \t]+", " ", texte)
    champs: dict[str, Any] = {}

    for nom, motifs, num in [
        # Toute-taxes. L'allemand et l'italien ne sont pas un supplement d'ame :
        # la Suisse, pays de lancement, facture majoritairement en allemand.
        ("total_incl_vat",
         [rf"(?:total\s*(?:ttc|t\.t\.c\.?)|amount\s+due|total\s+incl)[^\d\-]{{0,20}}{RE_MONTANT}",
          rf"(?:net\s+[àa]\s+payer)[^\d\-]{{0,20}}{RE_MONTANT}",
          rf"(?:total\s+inkl\.?\s*{ETIQ_TVA}|rechnungsbetrag|gesamtbetrag|endbetrag"
          rf"|bruttobetrag)[^\d\-]{{0,20}}{RE_MONTANT}",
          rf"(?:totale\s+(?:documento|{ETIQ_TVA}\s+inclusa)|importo\s+totale)"
          rf"[^\d\-]{{0,20}}{RE_MONTANT}"], True),
        ("total_excl_vat",
         [rf"(?:total\s*(?:ht|h\.t\.?)|subtotal|total\s+excl)[^\d\-]{{0,20}}{RE_MONTANT}",
          rf"(?:total\s+(?:exkl\.?|ohne)\s*{ETIQ_TVA}|nettobetrag|zwischensumme)"
          rf"[^\d\-]{{0,20}}{RE_MONTANT}",
          rf"(?:totale\s+imponibile|imponibile)[^\d\-]{{0,20}}{RE_MONTANT}"], True),
        ("vat_rate", [rf"{ETIQ_TVA}[^\d]{{0,12}}(\d{{1,2}}(?:[.,]\d)?)\s*%",
                      rf"(\d{{1,2}}(?:[.,]\d)?)\s*%\s*(?:de\s+|di\s+)?{ETIQ_TVA}"], True),
        ("currency", [r"\b(EUR|USD|GBP|CHF)\b", r"(€)"], False),
        ("vat_number", [r"\b(FR\s?[0-9A-Z]{2}\s?\d{9})\b",
                        r"\b((?:BE|DE|ES|IT|NL|LU|PT|AT|PL)\s?\d{8,12})\b"], False),
        ("siret", [r"\bsiret\s*:?\s*((?:\d[  ]?){14})\b"], False),
        ("siren", [r"\bsiren\s*:?\s*((?:\d[  ]?){9})\b"], False),
        ("order_reference",
         [r"(?:bon\s+de\s+commande|purchase\s+order|réf\.?\s+commande|PO)\s*:?\s*([A-Z0-9][\w\-/]{2,})"],
         False),
    ]:
        v, ok = _chercher(t, motifs, num)
        champs[nom] = {"value": v, "found": ok}

    v, ok = _numero_facture(t)
    champs["invoice_number"] = {"value": v, "found": ok}
    v, ok = _montant_tva(t)
    champs["vat_amount"] = {"value": v, "found": ok}
    v, ok = _iban(t)
    champs["iban"] = {"value": v, "found": ok}

    d, ok = _date_facture(t)
    champs["invoice_date"] = {"value": d, "found": ok}

    if champs["currency"]["value"] == "€":
        champs["currency"]["value"] = "EUR"
    for k in ("siret", "siren", "iban", "vat_number"):
        if champs[k]["value"]:
            champs[k]["value"] = re.sub(r"[\s  ]", "", champs[k]["value"])
    return champs
