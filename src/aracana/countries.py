"""Packs pays : ce qui change d'une juridiction à l'autre, isolé ici.

POURQUOI DES PACKS PLUTÔT QU'UN `if pays == "FR"` DISPERSÉ
  Le modèle est européen ; la France et la Suisse sont les deux premiers
  marchés. Tout ce qui diffère entre eux est local et vérifiable : identifiants
  d'entreprise et leurs sommes de contrôle, taux de TVA légaux, formats de
  référence de paiement. Rien de cela ne demande de réentraîner quoi que ce
  soit — c'est de l'arithmétique, et elle appartient à un module, pas au modèle.

  Ajouter la Belgique ou l'Allemagne consiste à écrire une classe de trente
  lignes, pas à toucher au pipeline.

CE QUE VALIDER UNE SOMME DE CONTRÔLE APPORTE VRAIMENT
  Un OCR ne peut pas savoir qu'il a mal lu un chiffre. Une clé de contrôle, si.
  Sur un SIREN, inverser deux chiffres casse la clé de Luhn dans neuf cas sur
  dix. C'est le seul mécanisme du pipeline qui détecte une erreur de lecture
  sans référentiel externe — et il est gratuit.

  Un échec de clé n'est jamais un rejet : c'est un routage vers l'humain.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal


# Longueurs IBAN par pays (ISO 13616). Couvre l'EEE, la Suisse et les
# voisins immédiats : au-delà, on retombe sur la fourchette générique 15–34,
# toujours associée à la clé mod-97. Une longueur absente de cette table n'est
# donc pas une faille, seulement un contrôle moins serré.
LONGUEUR_IBAN = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24,
    "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22,
    "GI": 23, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MT": 31, "NL": 18,
    "NO": 15, "PL": 28, "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24,
    "SM": 27, "VA": 22,
}


@dataclass
class Controle:
    nom: str
    passe: bool
    valeur: str | None = None
    detail: str = ""
    # Un contrôle échoué bloque-t-il l'automatisation, ou se contente-t-il
    # d'avertir ? La distinction est portée ici, par celui qui écrit le
    # contrôle et connaît sa sémantique. L'orchestrateur la lisait auparavant
    # en cherchant « checksum » ou « key » dans le nom : le jour où un contrôle
    # s'est appelé « IBAN check digits », un IBAN faux est passé en simple
    # avertissement. Un nom n'est pas un contrat.
    bloquant: bool = True


@dataclass
class Pays:
    code: str
    nom: str
    devise: str
    taux_tva: set[Decimal]
    langues: list[str]

    # ------------------------------------------------------------ outils
    @staticmethod
    def _luhn(n: str) -> bool:
        if not n.isdigit():
            return False
        s, pair = 0, False
        for c in reversed(n):
            d = int(c)
            if pair:
                d *= 2
                if d > 9:
                    d -= 9
            s += d
            pair = not pair
        return s % 10 == 0

    # ------------------------------------------------------------- IBAN
    #
    # L'IBAN est le champ le plus dangereux d'une facture : il ne fait pas que
    # documenter, il DÉCIDE où part l'argent. Un IBAN tronqué d'un caractère
    # n'échoue pas toujours bruyamment — et un IBAN substitué est le mode
    # opératoire de la fraude au faux fournisseur.
    #
    # Deux protections, toutes deux indispensables :
    #   — la longueur attendue par pays (ISO 13616), qui attrape la troncature ;
    #   — la clé mod-97 (ISO 7064), qui attrape la substitution de chiffres et
    #     la transposition, les deux erreurs de lecture les plus courantes.
    # Un IBAN qui échoue à l'une des deux n'est pas corrigé : il est refusé.
    # Mieux vaut un champ vide qu'un numéro de compte plausible et faux.
    @staticmethod
    def iban_valide(brut: str | None) -> bool:
        if not brut:
            return False
        n = re.sub(r"[\s  '’.\-]", "", str(brut)).upper()
        if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", n):
            return False
        attendue = LONGUEUR_IBAN.get(n[:2])
        if attendue is not None:
            if len(n) != attendue:
                return False
        elif not 15 <= len(n) <= 34:
            return False
        permute = n[4:] + n[:4]
        return int("".join(str(int(c, 36)) for c in permute)) % 97 == 1

    @classmethod
    def lire_iban(cls, texte: str) -> str | None:
        """Le premier IBAN du texte qui passe longueur ET clé.

        La recherche est délibérément tolérante puis filtrée strictement :
        l'OCR colle parfois le mot suivant (« …5295 7 BIC POFICHBEXXX »). On
        capture large, puis on essaie la longueur attendue du pays. Sans ce
        repli, un IBAN valide suivi d'un mot serait perdu.

        DEUX PIÈGES, PAYÉS TOUS LES DEUX
          1. Un motif gourmand consomme le texte qu'il traverse. Parti de
             « FR63… » — le numéro de TVA, présent sur toute facture française
             et de la même forme `AA99` qu'un IBAN — il avalait l'IBAN qui
             suivait, échouait à la clé, et `finditer` reprenait APRÈS. L'IBAN
             était perdu sur pratiquement chaque facture française. On énumère
             donc les débuts plausibles un par un : un candidat qui échoue ne
             masque plus celui d'après.
          2. `\\s` englobe le saut de ligne. Un IBAN ne se poursuit jamais à la
             ligne suivante ; l'autoriser revient à coller la ligne d'après au
             candidat. Seuls les séparateurs qui existent vraiment à
             l'intérieur d'un IBAN sont admis.
        """
        for m in re.finditer(r"(?<![A-Z0-9])[A-Z]{2}\d{2}", texte, re.I):
            suite = re.match(r"(?:[  '’]?[A-Z0-9]){10,32}",
                             texte[m.end():], re.I)
            if not suite:
                continue
            compact = (m.group(0)
                       + re.sub(r"[  '’]", "", suite.group(0))).upper()
            attendue = LONGUEUR_IBAN.get(compact[:2])
            tailles = [attendue] if attendue else range(34, 14, -1)
            for n in tailles:
                if n <= len(compact) and cls.iban_valide(compact[:n]):
                    return compact[:n]
        return None

    def controle_iban(self, champs: dict) -> Controle | None:
        """Contrôle indépendant du pays : la clé IBAN est la même partout."""
        brut = champs.get("iban")
        if not brut:
            return None
        n = re.sub(r"[\s  '’.\-]", "", str(brut)).upper()
        ok = self.iban_valide(n)
        attendue = LONGUEUR_IBAN.get(n[:2])
        if ok:
            detail = ""
        elif attendue and len(n) != attendue:
            detail = (f"{len(n)} characters; an {n[:2]} IBAN has {attendue}. "
                      f"Most likely truncated or over-read by the OCR.")
        else:
            detail = ("Mod-97 check digits do not match. One character was "
                      "misread, or the account number was substituted — this is "
                      "how supplier-payment fraud presents itself.")
        return Controle("IBAN check digits (ISO 7064)", ok, n, detail)

    def identifiants(self, texte: str) -> dict[str, str]:
        raise NotImplementedError

    def valider(self, champs: dict) -> list[Controle]:
        raise NotImplementedError

    def taux_legal(self, taux: Decimal | float | None) -> Controle | None:
        if taux is None:
            return None
        t = Decimal(str(taux))
        return Controle(
            f"VAT rate is statutory in {self.code}", t in self.taux_tva, str(t),
            "" if t in self.taux_tva else
            f"Statutory rates are {', '.join(str(x) for x in sorted(self.taux_tva))}. "
            f"A different rate is plausible for a cross-border supplier, and a "
            f"misread otherwise.",
            bloquant=False)


# ------------------------------------------------------------------- France

class France(Pays):
    def __init__(self) -> None:
        super().__init__(
            "FR", "France", "EUR",
            {Decimal("20"), Decimal("10"), Decimal("5.5"), Decimal("2.1"),
             Decimal("0")},
            ["fr"])

    def identifiants(self, texte: str) -> dict[str, str]:
        t = re.sub(r"[  ]", " ", texte)
        out = {}
        if m := re.search(r"\bsiret\s*:?\s*((?:\d[ .]?){14})", t, re.I):
            out["siret"] = re.sub(r"\D", "", m.group(1))
        if m := re.search(r"\bsiren\s*:?\s*((?:\d[ .]?){9})", t, re.I):
            out["siren"] = re.sub(r"\D", "", m.group(1))
        if m := re.search(r"\b(FR\s?[0-9A-Z]{2}\s?\d{9})\b", t, re.I):
            out["vat_number"] = re.sub(r"\s", "", m.group(1)).upper()
        if n := self.lire_iban(t):
            out["iban"] = n
        return out

    def valider(self, champs: dict) -> list[Controle]:
        c: list[Controle] = []
        for cle, longueur in (("siren", 9), ("siret", 14)):
            v = champs.get(cle)
            if not v:
                continue
            v = re.sub(r"\D", "", str(v))
            ok = len(v) == longueur and self._luhn(v)
            c.append(Controle(
                f"{cle.upper()} Luhn checksum", ok, v,
                "" if ok else
                f"Fails its check digit — almost always a misread or "
                f"transposed digit rather than an invalid company."))
        v = champs.get("vat_number")
        if v:
            v = re.sub(r"\s", "", str(v)).upper()
            forme = bool(re.fullmatch(r"FR[0-9A-Z]{2}\d{9}", v))
            c.append(Controle("FR VAT number format", forme, v))
            if forme and v[2:4].isdigit():
                # La cle FR se calcule sur le SIREN : (12 + 3*(SIREN mod 97)) mod 97
                siren = v[4:]
                attendu = (12 + 3 * (int(siren) % 97)) % 97
                ok = int(v[2:4]) == attendu
                c.append(Controle(
                    "FR VAT key matches the SIREN", ok, v,
                    "" if ok else
                    f"Key {v[2:4]} does not match the SIREN {siren} "
                    f"(expected {attendu:02d})."))
            if forme:
                c.append(Controle("VAT number embeds a valid SIREN",
                                  self._luhn(v[4:]), v[4:]))
        return c


# ------------------------------------------------------------------- Suisse

class Suisse(Pays):
    """La Suisse n'est pas dans la réforme française et n'a pas de SIREN.

    Elle a l'IDE/UID (CHE-xxx.xxx.xxx), des taux propres depuis 2024, et la
    QR-facture avec sa référence à clé mod-10 récursive. Un pipeline qui
    n'appliquerait que les règles françaises rejetterait des factures suisses
    parfaitement valides — ce qui est pire que ne rien contrôler.
    """

    # Table mod-10 recursive de la reference ESR/QR (norme suisse).
    TABLE = ((0, 9, 4, 6, 8, 2, 7, 1, 3, 5), (9, 4, 6, 8, 2, 7, 1, 3, 5, 0),
             (4, 6, 8, 2, 7, 1, 3, 5, 0, 9), (6, 8, 2, 7, 1, 3, 5, 0, 9, 4),
             (8, 2, 7, 1, 3, 5, 0, 9, 4, 6), (2, 7, 1, 3, 5, 0, 9, 4, 6, 8),
             (7, 1, 3, 5, 0, 9, 4, 6, 8, 2), (1, 3, 5, 0, 9, 4, 6, 8, 2, 7),
             (3, 5, 0, 9, 4, 6, 8, 2, 7, 1), (5, 0, 9, 4, 6, 8, 2, 7, 1, 3))

    def __init__(self) -> None:
        super().__init__(
            "CH", "Switzerland", "CHF",
            # Taux en vigueur depuis le 1er janvier 2024.
            {Decimal("8.1"), Decimal("2.6"), Decimal("3.8"), Decimal("0")},
            ["de", "fr", "it"])

    @classmethod
    def cle_mod10(cls, chiffres: str) -> int:
        report = 0
        for c in chiffres:
            report = cls.TABLE[report][int(c)]
        return (10 - report) % 10

    @staticmethod
    def cle_ide(huit: str) -> int | None:
        """Clé mod-11 de l'IDE, poids officiels (5,4,3,2,7,6,5,4).

        Renvoie None quand le reste vaut 10 : ces combinaisons ne sont pas
        attribuées, et prétendre le contraire ferait passer un numéro
        impossible pour valide.
        """
        poids = (5, 4, 3, 2, 7, 6, 5, 4)
        if len(huit) != 8 or not huit.isdigit():
            return None
        s = sum(int(d) * p for d, p in zip(huit, poids))
        reste = s % 11
        if reste == 0:
            return 0
        cle = 11 - reste
        return None if cle == 10 else cle

    def identifiants(self, texte: str) -> dict[str, str]:
        t = re.sub(r"[  ]", " ", texte)
        out = {}
        if m := re.search(r"\b(CHE)[\s-]?(\d{3})[.\s]?(\d{3})[.\s]?(\d{3})\b", t, re.I):
            out["uid"] = f"CHE{m.group(2)}{m.group(3)}{m.group(4)}"
        if n := self.lire_iban(t):
            out["iban"] = n
        # Reference QR : 27 chiffres, souvent groupes par 5
        if m := re.search(r"\b((?:\d[\s]?){26}\d)\b", t):
            ref = re.sub(r"\D", "", m.group(1))
            if len(ref) == 27:
                out["qr_reference"] = ref
        return out

    def valider(self, champs: dict) -> list[Controle]:
        c: list[Controle] = []
        v = champs.get("uid") or champs.get("vat_number")
        if v:
            n = re.sub(r"[^0-9A-Z]", "", str(v).upper())
            if n.startswith("CHE") and len(n) >= 12:
                chiffres = n[3:12]
                attendu = self.cle_ide(chiffres[:8])
                ok = attendu is not None and int(chiffres[8]) == attendu
                c.append(Controle(
                    "Swiss UID/IDE mod-11 checksum", ok,
                    f"CHE-{chiffres[:3]}.{chiffres[3:6]}.{chiffres[6:9]}",
                    "" if ok else
                    "Check digit does not match — likely a misread digit. "
                    "Confirm against uid.admin.ch before rejecting the supplier."))
            else:
                c.append(Controle("Swiss UID/IDE format", False, str(v),
                                  "Expected CHE-xxx.xxx.xxx."))
        ref = champs.get("qr_reference")
        if ref:
            r = re.sub(r"\D", "", str(ref))
            ok = len(r) == 27 and self.cle_mod10(r[:26]) == int(r[26])
            c.append(Controle(
                "QR-bill reference mod-10 recursive checksum", ok, r,
                "" if ok else
                "The payment reference is malformed; paying against it would "
                "fail or be misallocated by the bank."))
        iban = champs.get("iban")
        if iban and str(iban).upper().startswith("CH"):
            n = re.sub(r"\s", "", str(iban).upper())
            qr = 30000 <= int(n[4:9]) <= 31999 if n[4:9].isdigit() else False
            c.append(Controle(
                "QR-IBAN detected", True, n,
                "QR-IBAN: a structured QR reference is mandatory."
                if qr else "Standard IBAN: the reference is free-form.",
                bloquant=False))
        return c


# ------------------------------------------------------------- UE générique

class UnionEuropeenne(Pays):
    """Repli pour les autres États membres : format du numéro de TVA et
    plausibilité du taux. Pas de clé nationale — l'ajouter pays par pays est
    l'étape suivante, et chacune est une classe de trente lignes."""

    FORMATS = {
        "BE": r"BE0\d{9}", "DE": r"DE\d{9}", "ES": r"ES[0-9A-Z]\d{7}[0-9A-Z]",
        "IT": r"IT\d{11}", "NL": r"NL\d{9}B\d{2}", "LU": r"LU\d{8}",
        "PT": r"PT\d{9}", "AT": r"ATU\d{8}", "PL": r"PL\d{10}",
        "IE": r"IE\d[0-9A-Z]\d{5}[A-Z]{1,2}", "SE": r"SE\d{12}",
        "DK": r"DK\d{8}", "FI": r"FI\d{8}", "CZ": r"CZ\d{8,10}",
        "GR": r"EL\d{9}", "RO": r"RO\d{2,10}", "HU": r"HU\d{8}",
    }
    # Taux normaux 2026, pour juger de la plausibilité, pas pour liquider.
    NORMAUX = {"BE": 21, "DE": 19, "ES": 21, "IT": 22, "NL": 21, "LU": 17,
               "PT": 23, "AT": 20, "PL": 23, "IE": 23, "SE": 25, "DK": 25,
               "FI": 25.5, "CZ": 21, "GR": 24, "RO": 21, "HU": 27}

    def __init__(self, code: str = "EU") -> None:
        super().__init__(code, "European Union", "EUR",
                         {Decimal(str(v)) for v in self.NORMAUX.values()} |
                         {Decimal("0")}, [])

    @staticmethod
    def _tolerant(motif: str) -> str:
        """Autorise un espace après le préfixe pays, sans supprimer les espaces
        du texte.

        Supprimer les espaces globalement détruit les frontières de mots :
        « BTW BE0417497106 » devient « BTWBE0417497106 », et `\\bBE0…` ne matche
        plus puisque W et B sont deux caractères de mot. On garde donc le texte
        intact et on borne par des inspections négatives.
        """
        return f"(?<![A-Z0-9]){motif[:2]}\\s?{motif[2:]}(?![A-Z0-9])"

    def identifiants(self, texte: str) -> dict[str, str]:
        t = re.sub(r"[  ]", " ", texte).upper()
        out: dict[str, str] = {}
        for code, motif in self.FORMATS.items():
            if m := re.search(self._tolerant(motif), t):
                out["vat_number"] = re.sub(r"\s", "", m.group(0))
                out["vat_country"] = code
                break
        # Ne jamais sortir avant l'IBAN : un numéro de TVA trouvé n'est pas une
        # raison d'ignorer les coordonnées bancaires. La version précédente
        # sortait sur le premier `return` et perdait l'IBAN de toute facture
        # allemande ou italienne — c'est-à-dire de tout le marché hors France.
        if n := self.lire_iban(t):
            out["iban"] = n
        return out

    def valider(self, champs: dict) -> list[Controle]:
        v = champs.get("vat_number")
        if not v:
            return []
        n = re.sub(r"\s", "", str(v)).upper()
        pays = n[:2]
        motif = self.FORMATS.get(pays)
        if not motif:
            # Un préfixe hors zone n'est pas une anomalie : un fournisseur
            # britannique, norvégien ou américain facture légitimement. On le
            # signale sans bloquer.
            return [Controle("VAT number country prefix recognised", False, n,
                             f"{pays} is not an EU VAT prefix we know.",
                             bloquant=False)]
        ok = bool(re.fullmatch(motif, n))
        return [Controle(f"{pays} VAT number format", ok, n,
                         "" if ok else f"Does not match the {pays} pattern.")]


REGISTRE: dict[str, Pays] = {"FR": France(), "CH": Suisse()}


def pour(code: str | None) -> Pays:
    """Pack du pays, avec repli UE. Un code inconnu ne fait jamais échouer le
    pipeline : il réduit simplement le nombre de contrôles disponibles."""
    if not code:
        return UnionEuropeenne()
    c = code.upper()
    return REGISTRE.get(c) or UnionEuropeenne(c)


def deviner(texte: str) -> str | None:
    """Devine la juridiction à partir des identifiants présents. Ordre
    délibéré : un identifiant national est un signal plus fort qu'un mot."""
    t = texte.upper()
    if re.search(r"\bCHE[\s-]?\d{3}[.\s]?\d{3}[.\s]?\d{3}\b", t):
        return "CH"
    if re.search(r"\bSIRE[TN]\b", t) or re.search(r"\bFR\d{2}\s?\d{9}\b", t):
        return "FR"
    for code, motif in UnionEuropeenne.FORMATS.items():
        if re.search(UnionEuropeenne._tolerant(motif), t):
            return code
    if re.search(r"\bCHF\b", t):
        return "CH"
    return None
