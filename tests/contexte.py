# -*- coding: utf-8 -*-
"""Contexte des suites, version dépôt autonome.

Le framework est publié dans son propre dépôt (`ARACANAAI/aracana-ocr`) et
vit aussi dans le monorepo de développement. Les deux emplacements doivent
exécuter les mêmes suites sans modification : un test qui ne passe que d'un
côté ne garantit rien du paquet publié.

D'où la résolution depuis l'emplacement de CE fichier, jamais depuis un chemin
en dur ni depuis le répertoire courant.
"""
from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent      # racine du dépôt
SRC = RACINE / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def sortie_utf8() -> None:
    """La console Windows est en cp1252 ; nos messages ne le sont pas."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


class Compteur:
    """Rapporteur minimal, sans pytest.

    Délibéré : les suites doivent tourner sur une machine nue, dans une action
    d'intégration continue, avec `python tests/…`, sans rien installer. Le
    paquet n'a aucune dépendance obligatoire ; ses tests non plus.
    """

    def __init__(self, titre: str = "") -> None:
        self.titre = titre
        self.ok = 0
        self.echecs: list[str] = []

    def check(self, nom: str, condition, detail: object = "") -> bool:
        if condition:
            self.ok += 1
            print(f"  PASS  {nom}")
        else:
            self.echecs.append(nom)
            print(f"  FAIL  {nom}  {detail}")
        return bool(condition)

    def bilan(self) -> int:
        total = self.ok + len(self.echecs)
        barre = "=" * 58
        print(f"\n{barre}\n{self.ok}/{total} PASS"
              + (f" · ÉCHECS : {', '.join(self.echecs)}" if self.echecs else "")
              + f"\n{barre}")
        return 1 if self.echecs else 0


def charger_serveur_mcp():
    """Le serveur MCP, sans exiger le SDK `mcp`.

    Il n'en utilise que le décorateur `@app.tool()`. Installer la dépendance
    complète pour éprouver des expressions régulières serait un coût inutile,
    et surtout une raison de ne pas lancer les tests.
    """
    import importlib
    import types

    if "mcp.server.fastmcp" not in sys.modules:
        faux = types.ModuleType("mcp.server.fastmcp")

        class _FastMCP:
            def __init__(self, *a, **k):
                pass

            def tool(self, *a, **k):
                def deco(f):
                    return f
                return deco

        faux.FastMCP = _FastMCP
        faux.Context = object
        sys.modules["mcp"] = types.ModuleType("mcp")
        sys.modules["mcp.server"] = types.ModuleType("mcp.server")
        sys.modules["mcp.server.fastmcp"] = faux
    sys.modules.setdefault("httpx", types.ModuleType("httpx"))
    return importlib.import_module("aracana.mcp_server")
