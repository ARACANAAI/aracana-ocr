"""Journal d'audit — la trace de ce que le système a fait, et pourquoi.

POURQUOI CE MODULE EST DANS LA PARTIE OUVERTE
  Un système qui décide d'écritures comptables sans laisser de trace n'est pas
  déployable, quelle que soit sa précision. La traçabilité n'est pas une option
  d'entreprise : c'est la condition d'usage. Elle est donc gratuite, complète,
  et utilisable hors ligne.

  Ce que la version Pro ajoute n'est pas la trace — c'est la PREUVE que la
  trace n'a pas été modifiée après coup. Voir `aracana.pro` et le module
  `aracana_pro.audit`.

CE QU'UN AUDITEUR DEMANDE, ET QUE CE JOURNAL RÉPOND
  — qu'est devenu ce document ? (`retrouver`)
  — sur quelle base a-t-il été automatisé ? (les contrôles, un par un)
  — quel modèle, quelle version du code, quel jour ?
  — qu'a-t-on lu exactement, et d'où venait l'information ?

CE QU'IL NE FAIT PAS
  Il ne conserve aucun document. Un journal qui recopierait les factures
  deviendrait lui-même une base de données à caractère personnel, avec les
  obligations qui vont avec. Il enregistre des empreintes et des décisions ; le
  document reste chez son propriétaire.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator


@dataclass
class Evenement:
    """Une décision, figée. Sérialisable, comparable, rejouable."""

    horodatage: str
    document: str
    empreinte_document: str        # sha256 du fichier, pas son contenu
    empreinte_metier: str | None   # émetteur|numéro|date|TTC
    decision: str
    justification: str
    route: str
    pays: str | None
    source_champs: str
    modele_appele: bool
    secondes: float
    controles: list[dict] = field(default_factory=list)
    bloquants: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    champs: dict[str, Any] = field(default_factory=dict)
    contexte: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict:
        return asdict(self)


def _contexte_execution(modele: str | None) -> dict[str, Any]:
    """Ce qu'il faut pour reproduire un résultat un an plus tard.

    Sans la version du code et celle du modèle, une trace dit ce qui a été
    décidé mais pas par quoi — et devient inutilisable dès la mise à jour
    suivante.
    """
    from . import __version__
    return {
        "aracana": __version__,
        "modele": modele,
        "python": platform.python_version(),
        "hote": socket.gethostname(),
    }


class JournalAudit:
    """Journal append-only en JSON Lines.

    Le format est délibérément banal : une ligne, un objet JSON. Il se lit avec
    `grep`, s'importe dans n'importe quel outil, et survit à la disparition de
    cette bibliothèque. Un format propriétaire rendrait l'auditabilité
    dépendante de nous, ce qui la vide de son sens.
    """

    def __init__(self, chemin: str | Path, *, modele: str | None = None):
        self.chemin = Path(chemin)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self._contexte = _contexte_execution(modele)

    # ------------------------------------------------------------ écriture
    def enregistrer(self, decision, *, document: str,
                    octets: bytes | None = None,
                    contexte: dict | None = None) -> Evenement:
        """Consigne une `Decision`. Rend l'événement écrit."""
        e = Evenement(
            horodatage=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            document=document,
            empreinte_document=(hashlib.sha256(octets).hexdigest()
                                if octets is not None else ""),
            empreinte_metier=getattr(decision, "empreinte", None),
            decision=decision.issue.value,
            justification=decision.justification(),
            route=decision.route.value,
            pays=decision.pays,
            source_champs=decision.source_champs,
            modele_appele=decision.appel_modele,
            secondes=decision.secondes,
            controles=list(decision.controles),
            bloquants=list(decision.bloquants),
            avertissements=list(decision.avertissements),
            champs={k: (v.get("value") if isinstance(v, dict) else v)
                    for k, v in decision.champs.items()},
            contexte={**self._contexte, **(contexte or {})},
        )
        self._ajouter(e.dict())
        return e

    def _ajouter(self, objet: dict) -> None:
        # `default=str` : les montants issus des XML sont des Decimal. Perdre
        # une trace parce qu'un type ne se sérialise pas serait absurde.
        ligne = json.dumps(objet, ensure_ascii=False, default=str)
        with open(self.chemin, "a", encoding="utf-8", newline="\n") as f:
            f.write(ligne + "\n")
            # Une trace d'audit qui vit dans un tampon système n'existe pas
            # tant que la machine n'a pas rendu la main.
            f.flush()
            os.fsync(f.fileno())

    # ------------------------------------------------------------- lecture
    def evenements(self) -> Iterator[dict]:
        if not self.chemin.exists():
            return
        with open(self.chemin, encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.strip()
                if ligne:
                    yield json.loads(ligne)

    def retrouver(self, *, document: str | None = None,
                  empreinte: str | None = None) -> list[dict]:
        """La question qu'un client pose : « qu'est devenue la facture X ? »"""
        return [e for e in self.evenements()
                if (document is None or e["document"] == document)
                and (empreinte is None
                     or empreinte in (e.get("empreinte_metier"),
                                      e.get("empreinte_document")))]

    def statistiques(self) -> dict[str, Any]:
        """Ce que le service a réellement fait, à montrer tel quel."""
        n = auto = revue = rejet = modele = 0
        for e in self.evenements():
            n += 1
            modele += bool(e.get("modele_appele"))
            d = e.get("decision")
            auto += d == "auto_post"
            revue += d == "human_review"
            rejet += d == "rejected"
        return {
            "documents": n,
            "automatises": auto,
            "en_revue": revue,
            "rejetes": rejet,
            "appels_modele": modele,
            "appels_evites": n - modele,
            "taux_automatisation": round(auto / n, 4) if n else 0.0,
        }

    # --------------------------------------------------------- intégrité
    def empreinte(self) -> str:
        """Empreinte du journal entier, à noter ailleurs.

        C'est le degré d'intégrité que la version ouverte peut offrir
        honnêtement : si vous conservez cette empreinte hors du système — dans
        un courriel, un coffre, un registre — vous pouvez prouver plus tard que
        le journal n'a pas bougé.

        Ce qu'elle NE fait pas : empêcher quelqu'un qui a accès au fichier de
        le réécrire ET de recalculer l'empreinte. Pour cela il faut un
        chaînage par bloc et une ancre externe — c'est ce qu'apporte
        `aracana_pro.audit.JournalScelle`.
        """
        h = hashlib.sha256()
        if self.chemin.exists():
            with open(self.chemin, "rb") as f:
                for bloc in iter(lambda: f.read(65536), b""):
                    h.update(bloc)
        return h.hexdigest()


def resume_pour_auditeur(evenements: Iterable[dict]) -> str:
    """Un paragraphe lisible par quelqu'un qui n'ouvrira pas le JSON."""
    evs = list(evenements)
    if not evs:
        return "Aucun document traité."
    auto = [e for e in evs if e["decision"] == "auto_post"]
    controles = sum(len(e.get("controles", [])) for e in evs)
    echecs = sum(1 for e in evs for c in e.get("controles", [])
                 if not c.get("passed"))
    return (
        f"{len(evs)} document(s) traités entre {evs[0]['horodatage']} et "
        f"{evs[-1]['horodatage']}. {len(auto)} automatisés, "
        f"{len(evs) - len(auto)} arrêtés pour examen humain. "
        f"{controles} contrôles déterministes exécutés, dont {echecs} en échec — "
        f"chaque échec a bloqué ou signalé le document concerné, aucun n'a été "
        f"ignoré. Versions : "
        + ", ".join(f"{k}={v}" for k, v in (evs[-1].get("contexte") or {}).items())
        + "."
    )
