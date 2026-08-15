"""Frontière entre l'ouvert et le commercial.

CE QUE CE MODULE FAIT, ET CE QU'IL NE FAIT PAS
  Il déclare une frontière et sait dire de quel côté on se trouve. Il ne
  contient aucun verrou : le code ouvert n'est pas bridé, il est complet. Les
  extensions Pro sont un paquet SÉPARÉ (`aracana-ocr-pro`) qui s'enregistre ici
  quand il est installé.

  C'est une décision de conception, pas une naïveté. Un verrou dans du code
  ouvert se contourne en trois lignes ; le prétendre protecteur induit en
  erreur. Ce qui se vend, ce sont des fonctions que nous seuls écrivons et
  maintenons, et l'engagement qui va avec — pas l'accès à un `if`.

CE QUI EST OUVERT, ET POURQUOI
  Tout le traitement. Triage, extraction en quatre langues, packs pays avec
  leurs clés de contrôle, réconciliation Factur-X, décision, export FEC,
  traitement par lot, client HTTP, serveur MCP, ligne de commande.

  Un développeur doit pouvoir aller de `pip install` à une facture
  correctement contrôlée sans jamais nous parler. Une chaîne bridée au premier
  essai ne se diffuse pas, et notre valeur ne tient pas au fait de retenir un
  bout de code : elle tient à ce que ce code est juste, mesuré, et que ses
  erreurs sont documentées.

CE QUI EST PAYANT, ET POURQUOI C'EST DÉFENDABLE
  Ce qu'une organisation exige et qu'un développeur seul n'utilise pas :

    journal_scelle    chaînage par hachage + ancrage horodaté. Le journal
                      ouvert prouve l'intégrité à qui garde l'empreinte au
                      dehors ; le scellé la prouve à un tiers.
    dossier_ai_act    génération de la documentation technique et du registre
                      de journalisation exigés par le règlement européen sur
                      l'IA pour un système à haut risque.
    rgpd              purge sur durée de conservation, réponse aux demandes
                      d'accès et d'effacement, minimisation à l'entrée.
    derive            détection de dérive sur VOS documents : un jeu de
                      référence maison, rejoué à chaque mise à jour, avec
                      alerte au premier écart significatif.
    connecteurs       Pennylane, Sage, Cegid, SharePoint, S3, IMAP à l'échelle.
    multi_locataire   cloisonnement, rôles, quotas par entité.

  Aucune de ces fonctions n'est nécessaire pour lire une facture. Toutes sont
  nécessaires pour en répondre devant un régulateur.

USAGE
    from aracana import pro

    if pro.disponible("journal_scelle"):
        journal = pro.charger("journal_scelle")(chemin)
    else:
        journal = JournalAudit(chemin)      # la version ouverte, complète

    print(pro.etat())
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

# ---------------------------------------------------------------- catalogue

@dataclass(frozen=True)
class Fonction:
    cle: str
    titre: str
    pourquoi: str
    palier: str          # "pro" | "entreprise"


CATALOGUE: dict[str, Fonction] = {
    f.cle: f for f in (
        Fonction("journal_scelle", "Journal d'audit scellé",
                 "Chaînage par hachage et ancrage horodaté : l'intégrité de la "
                 "trace se prouve à un tiers, pas seulement à soi.", "pro"),
        Fonction("dossier_ai_act", "Dossier de conformité AI Act",
                 "Documentation technique et registre de journalisation exigés "
                 "pour un système à haut risque, générés depuis vos traces "
                 "réelles plutôt que rédigés à la main.", "entreprise"),
        Fonction("rgpd", "Outillage RGPD",
                 "Purge sur durée de conservation, réponse aux demandes d'accès "
                 "et d'effacement, minimisation à l'entrée.", "pro"),
        Fonction("derive", "Détection de dérive",
                 "Votre propre jeu de référence, rejoué à chaque mise à jour du "
                 "modèle ou des règles. Une régression se voit avant vos "
                 "clients.", "pro"),
        Fonction("connecteurs", "Connecteurs métier",
                 "Pennylane, Sage, Cegid, SharePoint, S3, IMAP à l'échelle, "
                 "avec reprise sur incident.", "entreprise"),
        Fonction("multi_locataire", "Multi-locataire et rôles",
                 "Cloisonnement des données, rôles, quotas et facturation par "
                 "entité.", "entreprise"),
    )
}

_REGISTRE: dict[str, Callable[..., Any]] = {}


def enregistrer(cle: str, fabrique: Callable[..., Any]) -> None:
    """Appelé par `aracana-ocr-pro` à son import. Inconnu = refusé, pour qu'un
    paquet tiers ne puisse pas se déclarer sous un nom du catalogue."""
    if cle not in CATALOGUE:
        raise KeyError(
            f"{cle!r} n'est pas au catalogue ARACANA Pro. "
            f"Connus : {', '.join(sorted(CATALOGUE))}.")
    _REGISTRE[cle] = fabrique


def _tenter_import() -> None:
    try:
        import aracana_ocr_pro  # noqa: F401  (s'enregistre à l'import)
    except ImportError:
        pass


def disponible(cle: str) -> bool:
    if cle not in _REGISTRE:
        _tenter_import()
    return cle in _REGISTRE


def charger(cle: str) -> Callable[..., Any]:
    """Rend la fabrique, ou lève une erreur qui EXPLIQUE — sans vendre.

    Le message dit ce que fait la fonction et où l'obtenir. Il ne prétend pas
    que la version ouverte est incomplète : elle ne l'est pas.
    """
    if disponible(cle):
        return _REGISTRE[cle]
    f = CATALOGUE.get(cle)
    if f is None:
        raise KeyError(f"Fonction inconnue : {cle!r}.")
    raise ProIndisponible(
        f"« {f.titre} » fait partie d'ARACANA {f.palier.capitalize()}, un paquet "
        f"distinct.\n\n  {f.pourquoi}\n\n"
        f"  Installation : pip install aracana-ocr-pro\n"
        f"  Détails      : https://aracana.ai/framework#pro\n\n"
        f"  Le traitement complet — triage, lecture, contrôles, réconciliation, "
        f"décision, export FEC — fonctionne sans cela."
    )


class ProIndisponible(RuntimeError):
    """Levée quand une fonction Pro est demandée sans être installée."""


def etat() -> str:
    """Ce qui est actif dans cette installation. Utile en support."""
    _tenter_import()
    lignes = ["ARACANA — état des extensions", ""]
    for f in CATALOGUE.values():
        marque = "installée" if f.cle in _REGISTRE else "absente  "
        lignes.append(f"  [{marque}] {f.cle:16s} {f.titre}  ({f.palier})")
    lignes += [
        "",
        "  Le traitement documentaire complet est dans le paquet ouvert et ne",
        "  dépend d'aucune de ces extensions.",
    ]
    if os.environ.get("ARACANA_LICENCE"):
        lignes.append("  Une licence est présente dans l'environnement.")
    return "\n".join(lignes)
