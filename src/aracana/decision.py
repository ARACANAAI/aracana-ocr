"""Orchestrateur : router, contrôler, décider — et pouvoir le justifier.

CE QUE CE MODULE GARANTIT, ET QUI MANQUE AUX OUTILS DU MARCHÉ

  1. **Aucune écriture n'est produite sans trace.** Chaque décision porte la
     liste des contrôles passés et échoués. Un auditeur qui demande « pourquoi
     cette facture est-elle partie en automatique ? » obtient une réponse
     exacte, pas une probabilité.

  2. **Le taux d'automatisation est mesuré, pas promis.** Nos propres mesures
     disent que le modèle décroche sur une part non nulle des pages. Un système
     qui annoncerait 100 % d'automatisation mentirait. Celui-ci compte ce qu'il
     automatise réellement et l'expose.

  3. **Idempotence.** À partir de septembre 2026, la même facture arrive
     couramment deux fois — par courriel et par la plateforme. Une empreinte
     stable sur les champs métier, et non sur les octets, permet de le voir :
     deux PDF différents portant la même facture ont la même empreinte.

  4. **Le GPU n'est appelé que s'il apporte quelque chose.** Le triage écarte
     les flux structurés et les PDF à couche texte fiable. On compte les appels
     évités : c'est la marge brute du service.

LA RÈGLE QUI GOUVERNE TOUT
  En cas de doute, l'humain. Un faux positif en écriture comptable coûte plus
  cher que dix revues manuelles — il faut le détecter, l'extourner, et refaire
  la déclaration. Le seuil est donc délibérément conservateur.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Callable

from . import countries as pays_mod
from .detect import Diagnostic, Route, diagnostiquer
from .reconcile import Reconciliation, lire_xml, reconcilier


class Issue(str, Enum):
    AUTO = "auto_post"          # écriture proposée sans intervention
    REVUE = "human_review"      # un humain doit trancher
    REJET = "rejected"          # rien d'exploitable


@dataclass
class Decision:
    issue: Issue
    confiance: str                        # "high" | "medium" | "low"
    route: Route
    pays: str | None
    champs: dict[str, Any] = field(default_factory=dict)
    controles: list[dict] = field(default_factory=list)
    bloquants: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    reconciliation: Reconciliation | None = None
    empreinte: str | None = None
    appel_modele: bool = False
    secondes: float = 0.0
    source_champs: str = ""               # "xml" | "ocr" | "xml+ocr" | "native"

    def justification(self) -> str:
        """Une phrase que l'on peut montrer à un comptable ou à un auditeur."""
        if self.issue is Issue.AUTO:
            return (f"Auto-posted: {len(self.controles)} checks passed, no "
                    f"discrepancy. Source: {self.source_champs}.")
        if self.issue is Issue.REJET:
            return f"Rejected: {self.bloquants[0] if self.bloquants else 'unreadable'}."
        raisons = self.bloquants or self.avertissements
        return ("Sent to human review: "
                + (raisons[0] if raisons else "insufficient confidence.")
                + (f" (+{len(raisons)-1} more)" if len(raisons) > 1 else ""))


# --------------------------------------------------------------- empreinte

def empreinte_facture(champs: dict[str, Any]) -> str | None:
    """Empreinte métier, stable à travers les représentations.

    Volontairement calculée sur (émetteur, numéro, date, TTC) et non sur les
    octets : le même document reçu en PDF puis en Factur-X, ou re-généré par le
    fournisseur, produit des octets différents et la même facture. Hacher le
    fichier ne détecterait aucun de ces doublons.
    """
    def v(k):
        c = champs.get(k)
        if isinstance(c, dict):
            return c.get("value") if c.get("found") else None
        return c

    numero, ttc = v("invoice_number"), v("total_incl_vat")
    if not numero or ttc is None:
        return None
    emetteur = (v("vat_number") or v("siren") or v("uid") or v("seller_name") or "")
    graine = "|".join([
        re.sub(r"[^A-Z0-9]", "", str(emetteur).upper()),
        re.sub(r"[^A-Z0-9]", "", str(numero).upper()),
        str(v("invoice_date") or ""),
        f"{Decimal(str(ttc)):.2f}",
    ])
    return hashlib.sha256(graine.encode()).hexdigest()[:32]


# ---------------------------------------------------------------- décision

# Seuils. Conservateurs par construction : voir l'en-tête du module.
CHAMPS_OBLIGATOIRES = ("invoice_number", "invoice_date", "total_incl_vat")


def _valeur(champs, k):
    c = champs.get(k)
    if isinstance(c, dict):
        return c.get("value") if c.get("found") else None
    return c


def _xml_vers_champs(x: dict) -> dict:
    """Aligne la sortie XML sur la forme {value, found} de l'extraction OCR,
    pour que la suite du pipeline ne sache pas d'où viennent les champs."""
    return {k: {"value": v, "found": v is not None}
            for k, v in x.items() if not k.startswith("_")}


def decider(
    source: str | bytes,
    *,
    ocr: Callable[[list[str] | bytes], dict] | None = None,
    extraire: Callable[[str], dict] | None = None,
    deja_vues: set[str] | None = None,
    pays_force: str | None = None,
    tolerance: Decimal = Decimal("0.02"),
) -> Decision:
    """Traite un document de bout en bout.

    `ocr` reçoit le document et renvoie `{"text": str, "pages": [...]}` — c'est
    l'API ARACANA OCR, injectée plutôt qu'importée pour que le pipeline reste
    testable sans GPU. `extraire` transforme un texte en champs.
    """
    t0 = time.time()
    diag: Diagnostic = diagnostiquer(source)
    d = Decision(issue=Issue.REVUE, confiance="low", route=diag.route,
                 pays=None, appel_modele=False)

    if diag.route is Route.INCONNU:
        d.issue = Issue.REJET
        d.bloquants = [diag.raison]
        d.secondes = round(time.time() - t0, 3)
        return d

    texte = ""
    champs: dict[str, Any] = {}
    rec: Reconciliation | None = None

    # --- 1. d'où viennent les champs -------------------------------------
    if diag.route is Route.STRUCTURE:
        champs = _xml_vers_champs(lire_xml(diag.xml))
        d.source_champs = "xml"
        texte = " ".join(str(v.get("value") or "") for v in champs.values())

    elif diag.route is Route.NATIF:
        texte = diag.texte_natif or ""
        champs = extraire(texte) if extraire else {}
        d.source_champs = "native"

    elif diag.route is Route.HYBRIDE:
        # Le XML est normatif pour l'écriture ; l'OCR sert à le CONTRÔLER.
        champs = _xml_vers_champs(lire_xml(diag.xml))
        d.source_champs = "xml"
        if ocr and extraire:
            r = ocr(source)
            texte = r.get("text", "")
            champs_page = extraire(texte)
            d.appel_modele = True
            rec = reconcilier(diag.xml, champs_page)
            d.reconciliation = rec
            d.source_champs = "xml+ocr"
            for e in rec.ecarts:
                (d.bloquants if e.gravite == "blocking"
                 else d.avertissements).append(e.explication)
        else:
            d.avertissements.append(
                "Embedded XML used without reading the visual layer: nothing "
                "guarantees the two agree. Enable OCR to reconcile them.")

    else:  # IMAGE
        if not (ocr and extraire):
            d.issue = Issue.REJET
            d.bloquants = ["Scanned document and no OCR available."]
            d.secondes = round(time.time() - t0, 3)
            return d
        r = ocr(source)
        texte = r.get("text", "")
        champs = extraire(texte)
        d.appel_modele = True
        d.source_champs = "ocr"

    d.champs = champs

    # --- 2. juridiction ---------------------------------------------------
    code = pays_force or pays_mod.deviner(texte) or None
    pack = pays_mod.pour(code)
    d.pays = pack.code
    ids = pack.identifiants(texte) if texte else {}
    for k, v in ids.items():
        champs.setdefault(k, {"value": v, "found": True})

    # --- 3. contrôles déterministes --------------------------------------
    plats = {k: _valeur(champs, k) for k in champs}
    controles = list(pack.valider(plats))
    t = plats.get("vat_rate")
    if (c := pack.taux_legal(Decimal(str(t)) if t is not None else None)):
        controles.append(c)
    # La clé IBAN est la même dans toute la zone : le contrôle est porté par la
    # classe de base et lancé ici, pour qu'aucun pack pays ne puisse l'oublier.
    if (c := pack.controle_iban(plats)):
        controles.append(c)

    ht, tva, ttc = (plats.get("total_excl_vat"), plats.get("vat_amount"),
                    plats.get("total_incl_vat"))
    if None not in (ht, tva, ttc):
        ecart = abs((Decimal(str(ht)) + Decimal(str(tva))) - Decimal(str(ttc)))
        ok = ecart <= tolerance
        controles.append(pays_mod.Controle(
            "Totals balance", ok, f"{ht} + {tva} = {ttc}",
            "" if ok else f"Off by {ecart}."))
        if not ok:
            d.bloquants.append(f"Totals do not balance (off by {ecart}).")
    else:
        d.avertissements.append("Totals incomplete — the sum was not verified.")

    for c in controles:
        d.controles.append({"check": c.nom, "passed": c.passe,
                            "value": c.valeur, "detail": c.detail,
                            "blocking": c.bloquant})
        if not c.passe:
            # La gravité est déclarée par le contrôle lui-même. Elle ne se
            # devine pas à partir de son intitulé : voir `Controle.bloquant`.
            (d.bloquants if c.bloquant else d.avertissements).append(
                f"{c.nom}: {c.detail or 'failed'}")

    manquants = [k for k in CHAMPS_OBLIGATOIRES if _valeur(champs, k) is None]
    if manquants:
        d.bloquants.append(f"Mandatory fields missing: {', '.join(manquants)}.")

    # --- 4. doublon -------------------------------------------------------
    d.empreinte = empreinte_facture(champs)
    if d.empreinte and deja_vues is not None and d.empreinte in deja_vues:
        d.issue = Issue.REVUE
        d.confiance = "high"
        d.bloquants.insert(0, (
            "Duplicate: an invoice with the same issuer, number, date and total "
            "has already been processed. Posting it twice would double the "
            "expense and the deductible VAT."))
        d.secondes = round(time.time() - t0, 3)
        return d

    # --- 5. verdict -------------------------------------------------------
    if d.bloquants:
        d.issue, d.confiance = Issue.REVUE, "low"
    elif d.avertissements:
        d.issue, d.confiance = Issue.REVUE, "medium"
    else:
        d.issue, d.confiance = Issue.AUTO, "high"
    d.secondes = round(time.time() - t0, 3)
    return d


# ------------------------------------------------------------- statistiques

@dataclass
class Bilan:
    """Ce que le service a réellement fait. Se montre à un client tel quel."""

    total: int = 0
    auto: int = 0
    revue: int = 0
    rejet: int = 0
    appels_modele: int = 0
    doublons: int = 0
    par_route: dict[str, int] = field(default_factory=dict)

    def ajouter(self, d: Decision) -> None:
        self.total += 1
        self.par_route[d.route.value] = self.par_route.get(d.route.value, 0) + 1
        if d.appel_modele:
            self.appels_modele += 1
        if any("Duplicate" in b for b in d.bloquants):
            self.doublons += 1
        if d.issue is Issue.AUTO:
            self.auto += 1
        elif d.issue is Issue.REJET:
            self.rejet += 1
        else:
            self.revue += 1

    @property
    def taux_automatisation(self) -> float:
        return self.auto / self.total if self.total else 0.0

    @property
    def appels_evites(self) -> int:
        """Documents traités sans GPU. C'est la marge : un appel évité est un
        coût d'inférence non engagé, sur un flux où le concurrent en paie un."""
        return self.total - self.appels_modele

    def resume(self) -> str:
        if not self.total:
            return "Nothing processed."
        return (
            f"{self.total} documents · {self.taux_automatisation:.0%} posted "
            f"automatically · {self.revue} to review · {self.rejet} rejected · "
            f"{self.appels_evites}/{self.total} handled without a GPU call "
            f"({self.appels_evites / self.total:.0%} of the flow)."
        )
