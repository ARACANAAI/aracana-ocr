"""Document canonique — le contrat entre n'importe quel analyseur et la suite.

POURQUOI UN FORMAT DE PLUS, ET POURQUOI CELUI-CI EST DIFFÉRENT
  Docling a `DoclingDocument`, Marker et MinerU ont leur JSON, Azure et Google
  ont les leurs. Ajouter un septième format serait indéfendable si le but était
  de représenter un document.

  Ce n'est pas le but. Ce format est le plus PETIT dénominateur sur lequel des
  vérificateurs peuvent travailler : un bloc, son type, sa position, son texte,
  son rang de lecture. Tous les analyseurs cités rendent au moins cela ; aucun
  n'impose plus. Les adaptateurs y convertissent en quelques lignes, sans
  perte pour ce qui nous concerne.

  Le format canonique est donc volontairement pauvre. Sa richesse serait un
  coût d'adaptation payé par chaque analyseur, pour une information dont les
  vérificateurs n'ont pas l'usage.

COORDONNÉES
  Entiers 0–999 inclus, décodés `x / 999 × dimension`. Ce choix vient du
  dialecte du modèle ARACANA, mais il est neutre : les adaptateurs convertissent
  depuis les pixels, les points ou les fractions. Le point à retenir est qu'il
  y a UNE convention, écrite, et que `Boite.pixels()` est le seul chemin pour
  en sortir — pour qu'aucun appelant ne redivise par 1000.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Protocol, runtime_checkable

ECHELLE = 999


class TypeBloc(str, Enum):
    """Vocabulaire de blocs, volontairement court et ouvert.

    Les analyseurs n'ont pas le même vocabulaire — Docling distingue des
    choses que MinerU fusionne. Les adaptateurs projettent sur cette liste, et
    tout ce qui ne rentre pas tombe dans `AUTRE` plutôt que d'être inventé. Un
    type faux est pire qu'un type absent : un vérificateur qui filtre sur
    `TABLE` doit pouvoir s'y fier.
    """

    TEXTE = "text"
    TITRE = "title"
    ENTETE = "header"
    PIED = "footer"
    NOTE = "footnote"
    NUMERO_PAGE = "page_number"
    LISTE = "list"
    TABLEAU = "table"
    FORMULE = "formula"
    IMAGE = "image"
    LEGENDE = "caption"
    AUTRE = "other"

    @classmethod
    def depuis(cls, brut: str) -> "TypeBloc":
        t = (brut or "").strip().lower().replace("-", "_")
        ALIAS = {
            "paragraph": cls.TEXTE, "plain_text": cls.TEXTE, "body": cls.TEXTE,
            "section_header": cls.TITRE, "heading": cls.TITRE,
            "page_header": cls.ENTETE, "page_footer": cls.PIED,
            "page_footnote": cls.NOTE, "ref_text": cls.NOTE,
            "table_caption": cls.LEGENDE, "image_caption": cls.LEGENDE,
            "equation": cls.FORMULE, "figure": cls.IMAGE, "picture": cls.IMAGE,
            "list_item": cls.LISTE,
        }
        if t in ALIAS:
            return ALIAS[t]
        try:
            return cls(t)
        except ValueError:
            return cls.AUTRE


@dataclass(frozen=True)
class Boite:
    """Boîte englobante en coordonnées normalisées 0–999 inclus."""

    x1: int
    y1: int
    x2: int
    y2: int

    def pixels(self, largeur: int, hauteur: int) -> tuple[int, int, int, int]:
        """Le SEUL chemin vers les pixels.

        Diviser par 1000 décale chaque boîte de 0,1 % vers le haut et la
        gauche : invisible à l'écran, faux dans une mesure, et suffisant pour
        qu'un recadrage tombe à côté d'une ligne de tableau. Centraliser la
        conversion est le seul moyen d'empêcher que quelqu'un la refasse mal.
        """
        return (round(self.x1 / ECHELLE * largeur),
                round(self.y1 / ECHELLE * hauteur),
                round(self.x2 / ECHELLE * largeur),
                round(self.y2 / ECHELLE * hauteur))

    @classmethod
    def depuis_pixels(cls, boite, largeur: int, hauteur: int) -> "Boite":
        x1, y1, x2, y2 = boite
        e = ECHELLE
        return cls(round(x1 / largeur * e), round(y1 / hauteur * e),
                   round(x2 / largeur * e), round(y2 / hauteur * e))

    @property
    def aire(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)

    def elargie(self, marge: int = 20) -> "Boite":
        """Utile à la boucle de réparation : recadrer un peu plus large que le
        bloc, parce qu'un caractère mal lu est souvent au bord."""
        return Boite(max(0, self.x1 - marge), max(0, self.y1 - marge),
                     min(ECHELLE, self.x2 + marge), min(ECHELLE, self.y2 + marge))


@dataclass
class Bloc:
    ordre: int
    type: TypeBloc
    texte: str
    boite: Boite | None = None
    page: int = 1
    confiance: float | None = None      # si l'analyseur en fournit une
    source: str = ""                    # quel analyseur, pour l'audit
    brut: dict[str, Any] = field(default_factory=dict)


@dataclass
class Page:
    numero: int
    largeur: int | None = None
    hauteur: int | None = None
    blocs: list[Bloc] = field(default_factory=list)

    @property
    def texte(self) -> str:
        return "\n".join(b.texte for b in self.blocs if b.texte)


@dataclass
class Document:
    """Ce qu'un analyseur rend, et ce que la suite consomme."""

    pages: list[Page] = field(default_factory=list)
    analyseur: str = ""
    secondes: float = 0.0
    brut: str | None = None             # sortie non retraitée, pour l'audit
    metadonnees: dict[str, Any] = field(default_factory=dict)

    @property
    def texte(self) -> str:
        return "\n\n".join(p.texte for p in self.pages)

    def blocs(self) -> Iterator[Bloc]:
        for p in self.pages:
            yield from p.blocs

    def blocs_de(self, *types: TypeBloc) -> list[Bloc]:
        return [b for b in self.blocs() if b.type in types]

    def localiser(self, aiguille: str) -> Bloc | None:
        """Le bloc qui contient un texte donné.

        C'est ce dont la boucle de réparation a besoin : un vérificateur dit
        « ce SIREN est faux », l'agent doit savoir OÙ il se trouve pour
        recadrer dessus.
        """
        cible = "".join(ch for ch in aiguille.lower() if ch.isalnum())
        if not cible:
            return None
        for b in self.blocs():
            if cible in "".join(ch for ch in b.texte.lower() if ch.isalnum()):
                return b
        return None

    def dict(self) -> dict[str, Any]:
        return {
            "analyseur": self.analyseur,
            "secondes": self.secondes,
            "pages": [
                {"page": p.numero, "largeur": p.largeur, "hauteur": p.hauteur,
                 "blocs": [
                     {"ordre": b.ordre, "type": b.type.value, "texte": b.texte,
                      "boite": None if b.boite is None else
                      [b.boite.x1, b.boite.y1, b.boite.x2, b.boite.y2],
                      "confiance": b.confiance}
                     for b in p.blocs]}
                for p in self.pages],
        }


@runtime_checkable
class Parser(Protocol):
    """Tout analyseur de documents. Quinze lignes suffisent à en adapter un.

    C'est délibérément un `Protocol` et non une classe de base : un adaptateur
    ne doit rien hériter, rien importer de nous, et rester une fonction si
    c'est plus clair. Cette contrainte est ce qui rend l'affirmation C3 du
    document de recherche testable — on remplace l'analyseur et l'on relance
    la même mesure.

        class MonAnalyseur:
            nom = "le mien"
            def analyser(self, source, **kw) -> Document: ...
    """

    nom: str

    def analyser(self, source: bytes | str, **options: Any) -> Document:
        ...
