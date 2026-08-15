"""Export FEC — Fichier des Écritures Comptables (article A47 A-1 du LPF).

POURQUOI LE FEC ET PAS UN CONNECTEUR
  Un cabinet français tourne sur Cegid, Sage, ACD, Quadra, Pennylane, Fulll ou
  Agiris. Écrire un connecteur pour chacun, c'est sept intégrations, sept
  contrats et sept dettes de maintenance — et l'accès aux API est souvent
  réservé aux partenaires.

  Le FEC contourne tout cela. Il est **imposé par la loi** : toute entreprise
  tenant sa comptabilité de façon informatisée doit pouvoir le produire, et
  tous ces logiciels savent l'importer. Un fichier FEC correct s'intègre donc
  partout, sans autorisation de personne. C'est la voie la moins chère vers la
  compatibilité universelle, et personne ne peut nous la fermer.

CE QUE CE MODULE REFUSE DE FAIRE
  Il n'écrit **que** les décisions AUTO. Une facture partie en revue humaine
  n'entre pas dans le fichier : elle va dans la file de revue, avec sa raison.
  Un export qui « comblerait » les cas douteux pour faire du volume produirait
  un grand livre faux — et c'est l'entreprise, pas nous, qui répondrait devant
  l'administration.

  Il refuse aussi toute écriture déséquilibrée. Un FEC dont un lot ne balance
  pas est rejeté au contrôle fiscal ; mieux vaut échouer ici, bruyamment.

PORTÉE
  Le FEC est français. La Suisse n'impose aucun format d'écritures : le même
  jeu de lignes y est exporté en CSV, via `ecritures_csv()`. Ne jamais laisser
  croire qu'un FEC vaut pour la Suisse.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Sequence

# Les dix-huit colonnes de l'article A47 A-1, dans l'ordre imposé. L'ordre
# n'est pas une convention : un fichier dont les colonnes sont permutées est
# rejeté par le contrôle.
COLONNES_FEC = (
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
    "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
    "PieceRef", "PieceDate", "EcritureLib", "Debit", "Credit",
    "EcritureLet", "DateLet", "ValidDate", "Montantdevise", "Idevise",
)

# Colonnes qui ne peuvent jamais être vides. Les auxiliaires et le lettrage le
# peuvent : une écriture non lettrée est normale.
COLONNES_OBLIGATOIRES = (
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
    "CompteNum", "CompteLib", "PieceRef", "PieceDate", "EcritureLib",
)

CENT = Decimal("0.01")


@dataclass
class PlanComptable:
    """Comptes utilisés. Injectable, parce qu'aucun plan n'est universel.

    Les valeurs par défaut suivent le PCG et conviennent à un achat de
    prestation de services standard. Un cabinet remplace `charge` par le compte
    de sa nomenclature analytique, ou fournit `charge_par_fournisseur`.
    """

    journal_code: str = "AC"
    journal_lib: str = "Achats"
    charge: str = "606100"
    charge_lib: str = "Achats non stockés"
    tva_deductible: str = "445660"
    tva_lib: str = "TVA déductible sur autres biens et services"
    fournisseur: str = "401000"
    fournisseur_lib: str = "Fournisseurs"
    # Surcharge ciblée : {numéro de TVA ou SIREN: compte de charge}
    charge_par_fournisseur: dict[str, str] = field(default_factory=dict)

    def compte_charge(self, emetteur: str | None) -> str:
        return self.charge_par_fournisseur.get(emetteur or "", self.charge)


@dataclass
class Ligne:
    """Une ligne du grand livre. Débit et crédit sont exclusifs."""

    journal_code: str
    journal_lib: str
    ecriture_num: str
    ecriture_date: str          # AAAAMMJJ
    compte_num: str
    compte_lib: str
    piece_ref: str
    piece_date: str             # AAAAMMJJ
    ecriture_lib: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    comp_aux_num: str = ""
    comp_aux_lib: str = ""
    valid_date: str = ""
    montant_devise: str = ""
    idevise: str = ""

    def cellules(self) -> dict[str, str]:
        return {
            "JournalCode": self.journal_code,
            "JournalLib": self.journal_lib,
            "EcritureNum": self.ecriture_num,
            "EcritureDate": self.ecriture_date,
            "CompteNum": self.compte_num,
            "CompteLib": self.compte_lib,
            "CompAuxNum": self.comp_aux_num,
            "CompAuxLib": self.comp_aux_lib,
            "PieceRef": self.piece_ref,
            "PieceDate": self.piece_date,
            "EcritureLib": self.ecriture_lib,
            "Debit": _montant(self.debit),
            "Credit": _montant(self.credit),
            "EcritureLet": "",
            "DateLet": "",
            "ValidDate": self.valid_date,
            "Montantdevise": self.montant_devise,
            "Idevise": self.idevise,
        }


class ExportImpossible(RuntimeError):
    """Levée plutôt que d'écrire un fichier faux."""


# ------------------------------------------------------------------- format

def _montant(d: Decimal) -> str:
    """Deux décimales, virgule décimale. Le FEC est un format français ; un
    point décimal y est lu comme un séparateur de milliers par certains
    importeurs, ce qui multiplie les montants par cent."""
    return f"{Decimal(d).quantize(CENT, rounding=ROUND_HALF_UP):.2f}".replace(".", ",")


def _date_fec(v: Any) -> str:
    """AAAAMMJJ. Accepte ce que produisent l'extraction (JJ/MM/AAAA) et les
    XML (AAAA-MM-JJ), parce que les deux arrivent dans le même flux."""
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y%m%d")
    s = str(v or "").strip()
    for motif, ordre in ((r"^(\d{2})/(\d{2})/(\d{4})$", (3, 2, 1)),
                         (r"^(\d{4})-(\d{2})-(\d{2})", (1, 2, 3)),
                         (r"^(\d{2})\.(\d{2})\.(\d{4})$", (3, 2, 1)),
                         (r"^(\d{8})$", None)):
        if m := re.match(motif, s):
            if ordre is None:
                return m.group(1)
            return "".join(m.group(i) for i in ordre)
    raise ExportImpossible(f"Date illisible pour un FEC : {v!r}")


# Le séparateur du FEC est la tabulation. Une valeur qui en contient une
# décale toutes les colonnes suivantes — et le fichier reste syntaxiquement
# plausible, donc l'erreur passe le premier regard. Un nom de fournisseur
# collé depuis un PDF contient souvent une tabulation ou un retour ligne.
_INTERDITS = re.compile(r"[\t\r\n]+")


def _propre(v: Any, taille: int = 100) -> str:
    return _INTERDITS.sub(" ", str(v or "")).strip()[:taille]


def code_auxiliaire(emetteur: str | None, nom: str) -> str:
    """Code de compte auxiliaire fournisseur, déterministe.

    On préfère toujours un identifiant fiscal — numéro de TVA, SIREN, IDE : il
    est unique et vérifié par une clé. À défaut, on dérive un code du nom, ce
    que fait tout cabinet à la main (« SAS Exemple » → « FEXEMPLE »). Laisser
    la colonne vide obligerait le comptable à rattacher chaque ligne à un
    fournisseur après import, c'est-à-dire à refaire le travail.

    Ce code est une clé de regroupement interne, pas une identité légale : il
    n'est jamais présenté comme telle.
    """
    if emetteur:
        return re.sub(r"[^A-Z0-9]", "", str(emetteur).upper())[:20]
    lettres = re.sub(r"[^A-Z0-9]", "", (nom or "").upper())
    return ("F" + lettres[:16]) if lettres else "FDIVERS"


def _dec(v: Any) -> Decimal:
    if v is None:
        raise ExportImpossible("Montant manquant.")
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- écritures

def _valeur(champs: dict, cle: str) -> Any:
    c = champs.get(cle)
    if isinstance(c, dict):
        return c.get("value") if c.get("found") else None
    return c


def ecriture_achat(champs: dict, *, numero: str,
                   plan: PlanComptable | None = None,
                   date_validation: str = "") -> list[Ligne]:
    """Les trois lignes d'une facture d'achat : charge, TVA, fournisseur.

        Débit  606100  Achats                      HT
        Débit  445660  TVA déductible              TVA
        Crédit 401000  Fournisseurs                TTC

    L'écriture est refusée si elle ne balance pas au centime. C'est le seul
    endroit du système où l'on écrit dans le grand livre d'un client ; s'y
    montrer permissif reviendrait à déplacer le problème dans sa liasse.
    """
    plan = plan or PlanComptable()
    ht = _dec(_valeur(champs, "total_excl_vat"))
    tva = _dec(_valeur(champs, "vat_amount"))
    ttc = _dec(_valeur(champs, "total_incl_vat"))

    if ht + tva != ttc:
        raise ExportImpossible(
            f"L'écriture ne balance pas : {ht} + {tva} = {ht + tva}, "
            f"or la facture annonce {ttc}. Aucune ligne n'est produite.")
    if ttc <= 0:
        raise ExportImpossible(f"Montant total non exploitable : {ttc}.")

    emetteur = (_valeur(champs, "vat_number") or _valeur(champs, "siren")
                or _valeur(champs, "uid") or "")
    nom = _propre(_valeur(champs, "seller_name") or emetteur or "Fournisseur")
    piece = _propre(_valeur(champs, "invoice_number"), 40)
    if not piece:
        raise ExportImpossible("Pas de numéro de facture : la pièce serait "
                               "introuvable lors d'un contrôle.")
    jour = _date_fec(_valeur(champs, "invoice_date"))
    libelle = _propre(f"{nom} - facture {piece}", 200)
    devise = _propre(_valeur(champs, "currency") or "", 3).upper()
    # Le FEC exprime les montants en euros. Une facture en devise porte en plus
    # les colonnes Montantdevise/Idevise ; les laisser vides sur une facture en
    # CHF serait une perte d'information au moment du contrôle.
    en_devise = devise not in ("", "EUR")

    commun = dict(journal_code=plan.journal_code, journal_lib=plan.journal_lib,
                  ecriture_num=numero, ecriture_date=jour, piece_ref=piece,
                  piece_date=jour, ecriture_lib=libelle,
                  valid_date=date_validation)

    lignes = [
        Ligne(compte_num=plan.compte_charge(emetteur),
              compte_lib=plan.charge_lib, debit=ht, **commun),
    ]
    if tva > 0:
        lignes.append(Ligne(compte_num=plan.tva_deductible,
                            compte_lib=plan.tva_lib, debit=tva, **commun))
    lignes.append(Ligne(
        compte_num=plan.fournisseur, compte_lib=plan.fournisseur_lib,
        credit=ttc, comp_aux_num=code_auxiliaire(_propre(emetteur, 20), nom),
        comp_aux_lib=nom,
        montant_devise=_montant(ttc) if en_devise else "",
        idevise=devise if en_devise else "", **commun))
    return lignes


def numeroter(prefixe: str, rang: int) -> str:
    """Numéro d'écriture stable et croissant. Le FEC exige qu'il soit unique
    dans l'exercice et attribué de façon chronologique et continue."""
    return f"{prefixe}{rang:06d}"


# ------------------------------------------------------------------ fichier

def ecrire_fec(lignes: Sequence[Ligne], flux: io.TextIOBase) -> int:
    """Écrit les lignes et rend leur nombre. Vérifie avant d'écrire."""
    probleme = incoherences(lignes)
    if probleme:
        raise ExportImpossible("FEC non conforme, rien n'a été écrit :\n  - "
                               + "\n  - ".join(probleme))
    w = csv.DictWriter(flux, fieldnames=list(COLONNES_FEC), delimiter="\t",
                       lineterminator="\r\n", quoting=csv.QUOTE_NONE,
                       escapechar=None)
    w.writeheader()
    for l in lignes:
        w.writerow(l.cellules())
    return len(lignes)


def nom_fichier(siren: str, cloture: date | str) -> str:
    """SIRENFECAAAAMMJJ.txt — nommage imposé, AAAAMMJJ étant la clôture."""
    s = re.sub(r"\D", "", str(siren))
    if len(s) != 9:
        raise ExportImpossible(f"SIREN attendu sur 9 chiffres, reçu {siren!r}.")
    return f"{s}FEC{_date_fec(cloture)}.txt"


def incoherences(lignes: Iterable[Ligne]) -> list[str]:
    """Tout ce qui ferait rejeter le fichier. Vide = conforme.

    Cette fonction est le contrôle que l'administration exercera. La passer
    ici, chez nous, coûte quelques millisecondes ; la rater chez le client
    coûte une procédure.
    """
    lignes = list(lignes)
    if not lignes:
        return ["Aucune ligne."]
    ennuis: list[str] = []
    par_ecriture: dict[str, list[Ligne]] = {}
    for l in lignes:
        par_ecriture.setdefault(l.ecriture_num, []).append(l)

    for num, groupe in par_ecriture.items():
        debit = sum((x.debit for x in groupe), Decimal("0"))
        credit = sum((x.credit for x in groupe), Decimal("0"))
        if debit != credit:
            ennuis.append(f"Écriture {num} déséquilibrée : débit {debit}, "
                          f"crédit {credit}.")
        if len(groupe) < 2:
            ennuis.append(f"Écriture {num} : une seule ligne, une écriture en "
                          f"partie double en compte au moins deux.")

    for l in lignes:
        cellules = l.cellules()
        for col in COLONNES_OBLIGATOIRES:
            if not cellules[col]:
                ennuis.append(f"Écriture {l.ecriture_num} : {col} est vide.")
        for col, valeur in cellules.items():
            if _INTERDITS.search(valeur):
                ennuis.append(f"Écriture {l.ecriture_num} : {col} contient une "
                              f"tabulation ou un retour ligne.")
        if l.debit and l.credit:
            ennuis.append(f"Écriture {l.ecriture_num} : une ligne porte à la "
                          f"fois un débit et un crédit.")
        if not l.debit and not l.credit:
            ennuis.append(f"Écriture {l.ecriture_num} : ligne à zéro.")
        if not re.fullmatch(r"\d{8}", l.ecriture_date):
            ennuis.append(f"Écriture {l.ecriture_num} : date {l.ecriture_date} "
                          f"n'est pas au format AAAAMMJJ.")
    return ennuis


def relire(contenu: str) -> tuple[list[dict[str, str]], list[str]]:
    """Relit un FEC produit et le contrôle comme le ferait un tiers.

    Écrire un fichier puis affirmer qu'il est bon n'est pas une vérification.
    On le relit donc à partir du texte, sans réutiliser les objets qui ont
    servi à l'écrire — c'est la seule façon d'attraper une erreur de sérialisa-
    tion, qui est précisément celle qu'on ne voit pas en relisant son code.
    """
    ennuis: list[str] = []
    # On découpe en lignes de façon tolérante, puis on juge séparément la fin
    # de ligne. La version stricte rendait « en-tête non conforme » quand le
    # contenu avait simplement été lu en newlines universels : un diagnostic
    # faux, sur un fichier correct — le pire des messages d'erreur.
    if contenu and "\r\n" not in contenu:
        ennuis.append("Fins de ligne non CRLF ; le FEC les impose. Si le "
                      "contenu a été lu en mode texte universel, relire en "
                      "binaire avant de conclure.")
    lignes = contenu.splitlines()
    if not lignes or not lignes[0]:
        return [], ennuis + ["Fichier vide."]
    entete = lignes[0].split("\t")
    if tuple(entete) != COLONNES_FEC:
        manquantes = [c for c in COLONNES_FEC if c not in entete]
        ennuis.append(
            f"En-tête non conforme : {len(entete)} colonnes au lieu de "
            f"{len(COLONNES_FEC)}"
            + (f", manquantes : {', '.join(manquantes)}" if manquantes else
               ", ordre incorrect"))
        return [], ennuis

    enregistrements = []
    for i, brut in enumerate(lignes[1:], start=2):
        if not brut:
            continue
        cellules = brut.split("\t")
        if len(cellules) != len(COLONNES_FEC):
            ennuis.append(f"Ligne {i} : {len(cellules)} colonnes au lieu de "
                          f"{len(COLONNES_FEC)}.")
            continue
        enregistrements.append(dict(zip(COLONNES_FEC, cellules)))

    soldes: dict[str, Decimal] = {}
    for e in enregistrements:
        for col in ("Debit", "Credit"):
            if e[col] and not re.fullmatch(r"-?\d+,\d{2}", e[col]):
                ennuis.append(f"{e['EcritureNum']} : {col}={e[col]!r} n'est pas "
                              f"un montant français à deux décimales.")
        d = Decimal((e["Debit"] or "0").replace(",", "."))
        c = Decimal((e["Credit"] or "0").replace(",", "."))
        soldes[e["EcritureNum"]] = soldes.get(e["EcritureNum"], Decimal("0")) + d - c
    for num, solde in soldes.items():
        if solde != 0:
            ennuis.append(f"Écriture {num} : solde {solde} après relecture.")
    return enregistrements, ennuis


# ------------------------------------------------------- hors de France

def ecritures_csv(lignes: Sequence[Ligne], flux: io.TextIOBase) -> int:
    """Les mêmes écritures en CSV point-virgule, pour la Suisse et le reste.

    Aucun format d'écritures n'est imposé hors de France. Les logiciels suisses
    (Abacus, Banana, Bexio) importent tous un CSV à colonnes libres ; on
    conserve donc les intitulés FEC, qui sont explicites, plutôt que d'inventer
    une nomenclature de plus.
    """
    w = csv.DictWriter(flux, fieldnames=list(COLONNES_FEC), delimiter=";",
                       lineterminator="\r\n")
    w.writeheader()
    for l in lignes:
        w.writerow(l.cellules())
    return len(lignes)
