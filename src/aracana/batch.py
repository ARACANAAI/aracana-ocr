"""Traitement d'un lot : d'un dossier de factures à un grand livre et une file.

CE QUE FAIT CE MODULE, ET POURQUOI IL EST LE PRODUIT
  Les modules précédents savent chacun une chose : trier, lire, contrôler,
  décider, écrire. Celui-ci les met bout à bout sur un lot réel et produit
  **trois** sorties, jamais une seule :

    1. le FEC des factures automatisables ;
    2. la file de revue, une ligne par facture arrêtée, avec la raison ;
    3. le journal d'audit, une ligne par document, quoi qu'il lui soit arrivé.

  Un système qui ne rendrait que le FEC laisserait croire que le reste n'existe
  pas. Or c'est le reste qui décide si un cabinet nous garde : une facture
  bloquée sans explication coûte plus de temps qu'une saisie manuelle.

  Le journal d'audit couvre l'intégralité du lot, y compris les rejets. C'est
  lui qui permet de répondre « qu'est devenue la facture X ? » sans rejouer le
  traitement — et c'est la question que pose un client mécontent.

L'ORDRE DE TRAITEMENT N'EST PAS ANODIN
  Les documents sont traités dans l'ordre reçu, et la détection de doublon est
  cumulative dans le lot. Deux exemplaires de la même facture dans un même
  dossier — cas courant quand un fournisseur relance par courriel — donnent une
  écriture et une revue, jamais deux écritures.
"""
from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .decision import Bilan, Decision, Issue, decider
from .ledger import (ExportImpossible, Ligne, PlanComptable, ecrire_fec,
                     ecriture_achat, ecritures_csv, numeroter)

COLONNES_REVUE = (
    "document", "issue", "confidence", "route", "country", "reason",
    "all_reasons", "invoice_number", "invoice_date", "total_incl_vat",
    "seller", "fingerprint", "model_called", "seconds",
)


@dataclass
class Entree:
    """Un document et ce qu'il est devenu. L'unité du journal d'audit."""

    nom: str
    decision: Decision
    lignes: list[Ligne] = field(default_factory=list)
    erreur_ecriture: str | None = None

    @property
    def comptabilise(self) -> bool:
        return bool(self.lignes)


@dataclass
class Lot:
    entrees: list[Entree] = field(default_factory=list)
    bilan: Bilan = field(default_factory=Bilan)

    # ------------------------------------------------------------ sorties
    @property
    def lignes(self) -> list[Ligne]:
        return [l for e in self.entrees for l in e.lignes]

    @property
    def a_revoir(self) -> list[Entree]:
        return [e for e in self.entrees if not e.comptabilise]

    def fec(self, flux: io.TextIOBase) -> int:
        lignes = self.lignes
        if not lignes:
            raise ExportImpossible(
                "Aucune facture n'a franchi les contrôles : il n'y a rien à "
                "comptabiliser. Voir la file de revue.")
        return ecrire_fec(lignes, flux)

    def csv_ecritures(self, flux: io.TextIOBase) -> int:
        return ecritures_csv(self.lignes, flux)

    def file_de_revue(self, flux: io.TextIOBase) -> int:
        """Une ligne par facture arrêtée, exploitable telle quelle par un
        comptable : la raison d'abord, les champs lus ensuite pour qu'il n'ait
        pas à rouvrir le PDF pour comprendre."""
        w = csv.DictWriter(flux, fieldnames=list(COLONNES_REVUE),
                           delimiter=";", lineterminator="\r\n")
        w.writeheader()
        n = 0
        for e in self.a_revoir:
            w.writerow(_ligne_revue(e))
            n += 1
        return n

    def audit(self) -> list[dict[str, Any]]:
        """Le lot entier, y compris ce qui est passé. Sérialisable en JSON."""
        return [_ligne_audit(e) for e in self.entrees]

    def resume(self) -> str:
        parts = [self.bilan.resume()]
        ecrites = len(self.lignes)
        factures = sum(1 for e in self.entrees if e.comptabilise)
        # La file de revue contient aussi les rejets : quelqu'un doit confirmer
        # qu'un document illisible n'était pas une facture. Le détail est
        # explicité, sinon la ligne semble contredire le bilan des décisions.
        rejetes = sum(1 for e in self.entrees
                      if e.decision.issue is Issue.REJET)
        attente = len(self.a_revoir)
        parts.append(
            f"{factures} facture(s) comptabilisée(s) en {ecrites} ligne(s) ; "
            f"{attente} document(s) en attente d'un humain "
            f"({attente - rejetes} en revue, {rejetes} rejeté(s) à confirmer).")
        refus = [e for e in self.entrees if e.erreur_ecriture]
        if refus:
            parts.append(
                f"{len(refus)} facture(s) validée(s) par les contrôles mais "
                f"refusée(s) à l'écriture — anomalie à examiner : "
                + "; ".join(f"{e.nom} ({e.erreur_ecriture})" for e in refus[:3]))
        return "\n".join(parts)


def _champ(d: Decision, cle: str) -> Any:
    c = d.champs.get(cle)
    if isinstance(c, dict):
        return c.get("value") if c.get("found") else None
    return c


def _ligne_revue(e: Entree) -> dict[str, Any]:
    d = e.decision
    raisons = d.bloquants + d.avertissements
    if e.erreur_ecriture:
        raisons = [e.erreur_ecriture] + raisons
    return {
        "document": e.nom,
        "issue": d.issue.value,
        "confidence": d.confiance,
        "route": d.route.value,
        "country": d.pays or "",
        "reason": raisons[0] if raisons else "",
        "all_reasons": " | ".join(raisons[1:]),
        "invoice_number": _champ(d, "invoice_number") or "",
        "invoice_date": _champ(d, "invoice_date") or "",
        "total_incl_vat": _champ(d, "total_incl_vat") or "",
        "seller": _champ(d, "seller_name") or _champ(d, "vat_number") or "",
        "fingerprint": d.empreinte or "",
        "model_called": "yes" if d.appel_modele else "no",
        "seconds": f"{d.secondes:.3f}",
    }


def _ligne_audit(e: Entree) -> dict[str, Any]:
    d = e.decision
    return {
        "document": e.nom,
        "issue": d.issue.value,
        "posted": e.comptabilise,
        "route": d.route.value,
        "country": d.pays,
        "field_source": d.source_champs,
        "model_called": d.appel_modele,
        "seconds": d.secondes,
        "fingerprint": d.empreinte,
        "checks": d.controles,
        "blocking": d.bloquants,
        "warnings": d.avertissements,
        "reconciliation": (None if d.reconciliation is None else {
            "agrees": d.reconciliation.concordant,
            "compared": d.reconciliation.compares,
            "unverified": d.reconciliation.non_verifies,
            "summary": d.reconciliation.resume(),
        }),
        "ledger_lines": len(e.lignes),
        "ledger_error": e.erreur_ecriture,
        "justification": d.justification(),
    }


# ------------------------------------------------------------------- moteur

def traiter_lot(
    documents: Iterable[tuple[str, bytes]] | Sequence[Path] | Path,
    *,
    ocr: Callable[[Any], dict] | None = None,
    extraire: Callable[[str], dict] | None = None,
    plan: PlanComptable | None = None,
    prefixe: str = "AC",
    depart: int = 1,
    pays_force: str | None = None,
) -> Lot:
    """Traite un lot et rend tout : écritures, file de revue, journal d'audit.

    `documents` accepte un dossier, une liste de chemins, ou des couples
    (nom, octets) — ce dernier cas sert à l'API, qui ne touche pas au disque.
    """
    plan = plan or PlanComptable()
    lot = Lot()
    vues: set[str] = set()
    rang = depart

    for nom, octets in _normaliser(documents):
        d = decider(octets, ocr=ocr, extraire=extraire, deja_vues=vues,
                    pays_force=pays_force)
        lot.bilan.ajouter(d)
        entree = Entree(nom=nom, decision=d)

        if d.issue is Issue.AUTO:
            try:
                entree.lignes = ecriture_achat(
                    d.champs, numero=numeroter(prefixe, rang), plan=plan,
                    date_validation=time.strftime("%Y%m%d"))
                rang += 1
            except ExportImpossible as e:
                # Les contrôles ont dit oui et l'écriture dit non : c'est une
                # contradiction interne, pas un cas métier. On ne comptabilise
                # pas, et on le signale comme tel plutôt que de le noyer dans
                # la file de revue ordinaire.
                entree.erreur_ecriture = str(e)
        if d.empreinte:
            vues.add(d.empreinte)
        lot.entrees.append(entree)

    return lot


def _normaliser(documents) -> list[tuple[str, bytes]]:
    if isinstance(documents, (str, Path)):
        chemin = Path(documents)
        if chemin.is_dir():
            fichiers = sorted(p for p in chemin.iterdir() if p.is_file())
            return [(p.name, p.read_bytes()) for p in fichiers]
        return [(chemin.name, chemin.read_bytes())]
    sortie = []
    for d in documents:
        if isinstance(d, (str, Path)):
            p = Path(d)
            sortie.append((p.name, p.read_bytes()))
        else:
            sortie.append((str(d[0]), d[1]))
    return sortie


# ------------------------------------------------------------------ écriture

def ecrire_sorties(lot: Lot, dossier: str | Path, *,
                   siren: str | None = None,
                   cloture: str | None = None) -> dict[str, str]:
    """Dépose les trois sorties dans un dossier et rend leurs chemins.

    Le FEC n'est écrit que s'il y a quelque chose à écrire ; un fichier FEC
    vide serait accepté par un importeur et donnerait l'illusion d'un
    traitement réussi.
    """
    from .ledger import nom_fichier

    d = Path(dossier)
    d.mkdir(parents=True, exist_ok=True)
    produits: dict[str, str] = {}

    if lot.lignes:
        nom = (nom_fichier(siren, cloture) if siren and cloture
               else "ecritures_fec.txt")
        chemin = d / nom
        with open(chemin, "w", encoding="utf-8", newline="") as f:
            lot.fec(f)
        produits["fec"] = str(chemin)

    if lot.a_revoir:
        chemin = d / "file_de_revue.csv"
        with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
            lot.file_de_revue(f)
        produits["revue"] = str(chemin)

    chemin = d / "journal_audit.json"
    chemin.write_text(json.dumps(lot.audit(), ensure_ascii=False, indent=2),
                      encoding="utf-8")
    produits["audit"] = str(chemin)
    return produits
