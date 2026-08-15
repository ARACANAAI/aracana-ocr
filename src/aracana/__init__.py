"""ARACANA — chaîne de traitement documentaire auditable pour l'Europe.

    pip install aracana-ocr

CE QUE CE PAQUET EST
  L'ingénierie autour d'un modèle d'OCR, pas le modèle. Il trie les documents,
  lit ce qui doit l'être, contrôle ce qui a été lu, décide ce qui peut être
  automatisé — et sait justifier chacune de ces décisions.

  Il fonctionne sans aucun modèle : un XML structuré, un PDF à couche texte,
  une réconciliation Factur-X et tous les contrôles arithmétiques ne demandent
  pas de GPU. Le modèle est une dépendance optionnelle, injectée.

CE QU'IL GARANTIT
  1. Rien n'est deviné. Un champ absent ressort `found=False`, jamais rempli
     d'une valeur plausible. Une écriture comptable fausse coûte plus cher
     qu'une case vide.
  2. Toute décision porte sa trace. Les contrôles passés et échoués sont
     attachés au résultat : « pourquoi cette facture est-elle partie en
     automatique ? » a une réponse exacte, pas une probabilité.
  3. Les contrôles sont déterministes. Clé de Luhn, clé TVA française, IDE
     suisse mod-11, IBAN mod-97, équilibre HT + TVA = TTC : de l'arithmétique.
     Un modèle peut halluciner un total qui tombe juste ; ce code ne le peut
     pas.
  4. Le modèle n'est appelé que s'il apporte quelque chose. Les appels évités
     sont comptés.

DÉMARRER
    from aracana import decider, diagnostiquer

    diag = diagnostiquer(octets)          # quelle route, faut-il un GPU ?
    d = decider(octets, ocr=mon_ocr, extraire=extraire_champs)
    print(d.issue, d.justification())

En ligne de commande :
    aracana lire facture.pdf
    aracana lot ./factures --fec sortie/

LICENCE
  Apache-2.0. Les extensions de conformité d'entreprise — journal scellé,
  dossier AI Act, outillage RGPD, détection de dérive — vivent dans
  `aracana-ocr-pro`, sous licence commerciale. Voir `aracana.pro`.
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = [
    # triage
    "Route", "Diagnostic", "diagnostiquer",
    # extraction
    "extraire_champs",
    # pays et contrôles
    "Controle", "Pays", "pour", "deviner", "LONGUEUR_IBAN",
    # réconciliation
    "Reconciliation", "Ecart", "lire_xml", "reconcilier",
    # décision
    "Issue", "Decision", "Bilan", "decider", "empreinte_facture",
    # grand livre
    "Ligne", "PlanComptable", "ExportImpossible",
    "ecriture_achat", "ecrire_fec", "relire", "nom_fichier", "incoherences",
    # lot
    "Lot", "Entree", "traiter_lot", "ecrire_sorties",
    # audit
    "JournalAudit", "Evenement",
    # SDK
    "Client", "ErreurApi", "en_pixels",
    # extensions et frontière commerciale
    "plugins", "pro",
]

from . import plugins, pro
from .audit import Evenement, JournalAudit
from .batch import Entree, Lot, ecrire_sorties, traiter_lot
from .client import Client, ErreurApi, en_pixels
from .countries import LONGUEUR_IBAN, Controle, Pays, deviner, pour
from .decision import Bilan, Decision, Issue, decider, empreinte_facture
from .detect import Diagnostic, Route, diagnostiquer
from .extract import extraire_champs
from .ledger import (ExportImpossible, Ligne, PlanComptable, ecrire_fec,
                     ecriture_achat, incoherences, nom_fichier, relire)
from .reconcile import Ecart, Reconciliation, lire_xml, reconcilier
