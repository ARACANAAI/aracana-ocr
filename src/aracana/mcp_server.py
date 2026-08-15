"""ARACANA OCR — MCP server.

WHY THIS EXISTS RATHER THAN FOUR SEPARATE PLUGINS
  n8n consumes MCP natively through its `MCP Client Tool` node. LangChain and
  LangGraph have official MCP adapters. Claude Desktop, Claude Code, Cursor and
  Windsurf all speak MCP. Writing a native n8n node, a Zapier app, a LangChain
  tool and an editor extension separately would be four codebases to keep in
  step. This is one.

WHAT IT DELIBERATELY DOES NOT DO
  It does not run the model. It calls the ARACANA OCR HTTP API, which owns the
  GPU, the queue and the quotas. An MCP server that loaded 7 GB of weights per
  client would be unusable the moment two people opened Claude Desktop.

THE VERIFICATION TOOLS DO NOT USE A MODEL
  `check_invoice` is arithmetic and checksum work: line sums, VAT rates, the
  Luhn key of a SIREN. Deterministic by construction, so it cannot hallucinate
  a total that balances. The model reads; this code verifies. That separation
  is what makes an autonomous pipeline defensible.
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

API = os.environ.get("ARACANA_API_URL", "http://localhost:8000").rstrip("/")
KEY = os.environ.get("ARACANA_API_KEY", "")
TIMEOUT = float(os.environ.get("ARACANA_TIMEOUT_S", "180"))

mcp = FastMCP(
    "aracana-ocr",
    instructions=(
        "Document parsing for regulated European workloads. Returns typed, "
        "located blocks in reading order.\n\n"
        "Coordinates are integers 0-999, decoded as x / 999 * width — not "
        "0-1000.\n\n"
        "Several pages in one call are parsed in a single forward pass; do not "
        "loop page by page.\n\n"
        "`parse_document` is the general tool. `extract_invoice` adds field "
        "extraction, and `check_invoice` validates those fields arithmetically "
        "— use it before writing anything to an accounting system."
    ),
)


# ---------------------------------------------------------------- transport

def _entetes() -> dict[str, str]:
    return {"Authorization": f"Bearer {KEY}"} if KEY else {}


async def _appeler(chemins: list[tuple[str, bytes, str]], brut: bool) -> dict:
    fichiers = [("files", (n, o, t)) for n, o, t in chemins]
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{API}/v1/ocr", params={"include_raw": brut},
                         files=fichiers, headers=_entetes())
    if r.status_code == 401:
        raise RuntimeError(
            "Rejected by the OCR API: set ARACANA_API_KEY in the MCP server "
            "environment.")
    if r.status_code == 429:
        d = r.json()
        raise RuntimeError(
            f"Quota: {d.get('message')} Retry in "
            f"{d.get('retry_after_seconds')}s.")
    if r.status_code >= 400:
        raise RuntimeError(f"OCR API {r.status_code}: {r.text[:300]}")
    return r.json()


def _lire(chemin: str) -> tuple[str, bytes, str]:
    if not os.path.exists(chemin):
        raise FileNotFoundError(f"{chemin} does not exist")
    ext = os.path.splitext(chemin)[1].lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "tif": "image/tiff",
            "tiff": "image/tiff"}.get(ext)
    if not mime:
        raise ValueError(
            f"{chemin}: unsupported extension. PNG, JPEG, WebP or TIFF. "
            f"PDFs must be rasterised to page images first.")
    return os.path.basename(chemin), open(chemin, "rb").read(), mime


# ---------------------------------------------------------------- outils

@mcp.tool()
async def parse_document(
    image_paths: list[str],
    include_raw: bool = False,
) -> dict[str, Any]:
    """Parse one or more page images into typed, located blocks.

    Pass every page of a document in ONE call: they are parsed in a single
    forward pass and accuracy does not degrade with length. Calling once per
    page is slower and loses cross-page reading order.

    Returns pages, each with blocks carrying `order`, `type`, `box` (0-999 and
    pixels) and `text`, plus the concatenated text and usage counters.
    """
    fichiers = [_lire(p) for p in image_paths]
    d = await _appeler(fichiers, include_raw)
    return {
        "pages": d["pages"],
        "text": d["text"],
        "raw": d.get("raw"),
        "usage": d["usage"],
        "note": "Coordinates are 0-999; pixels = x / 999 * width.",
    }


@mcp.tool()
async def extract_invoice(image_paths: list[str]) -> dict[str, Any]:
    """Parse an invoice and extract its accounting fields.

    Extraction is pattern-based over the parsed text, so every field carries
    `found: true|false`. A missing field is reported as missing rather than
    guessed — an invoice pipeline that invents a VAT number is worse than one
    that asks a human.

    Always follow with `check_invoice` before posting anything.
    """
    fichiers = [_lire(p) for p in image_paths]
    d = await _appeler(fichiers, brut=False)
    champs = extraire_champs(d["text"])
    return {
        "fields": champs,
        "text": d["text"],
        "pages": len(d["pages"]),
        "blocks": d["usage"]["blocks"],
        "confidence_note": (
            "Fields are pattern-extracted, not model-predicted. Anything with "
            "found=false was not located and must not be inferred."
        ),
    }


@mcp.tool()
def check_invoice(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate extracted invoice fields arithmetically. No model involved.

    Runs deterministic checks: the VAT amount against the stated rate, the
    total against net + VAT, the SIREN/SIRET Luhn key, and the VAT number
    format. Returns a verdict and the list of failures.

    `verdict: "review"` means a human must look at it. Route those rather than
    posting them — the model breaks down on a measurable share of pages, and a
    pipeline that hides that fact will post wrong entries.
    """
    return verifier_facture(fields)


@mcp.tool()
async def model_info() -> dict[str, Any]:
    """Model identity, frozen generation settings, supported block types and
    languages, as reported by the API itself."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API}/v1/model", headers=_entetes())
    r.raise_for_status()
    return r.json()


@mcp.tool()
async def service_health() -> dict[str, Any]:
    """Whether the OCR service is up, the model loaded, and how deep the queue
    is. Check this before submitting a batch."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API}/v1/health")
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------- extraction
#
# L'extraction vit dans `aracana_pipeline.extract`, pas ici. Trois appelants en
# dépendent — ce serveur, la route `/v1/invoices` de l'API et le traitement par
# lot — et seul ce serveur a besoin du SDK MCP. Tant que le code habitait ce
# fichier, l'API devait importer un module qui exige `mcp` : la route
# répondait 501 dans le conteneur livré tout en marchant en développement.
#
# Les noms sont réexportés tels quels : ils font partie de la surface publique
# de ce module et des tests qui la vérifient.
from .extract import (          # noqa: E402,F401
    ETIQ_FACTURE, ETIQ_TVA, MARQUE_NUM, MILLIERS, MOIS, MOTS_NON_NUMERO,
    QUALIF_APRES, QUALIF_AVANT, RE_DATE_SEULE, RE_MONTANT, RE_MONTANT_STRICT,
    RE_NUM_DOC, SEP_NUM, _chercher, _iban, _iban_valide, _montant_tva,
    _nombre, _numero_facture, extraire_champs,
)
from .countries import LONGUEUR_IBAN   # noqa: E402,F401



# ----------------------------------------------------------- verification

def _luhn(n: str) -> bool:
    """SIREN and SIRET carry a Luhn checksum. A typo or a misread digit fails
    it, which catches OCR errors the model itself cannot know it made."""
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


TAUX_FR = {20.0, 10.0, 5.5, 2.1, 0.0}


def verifier_facture(champs: dict[str, Any], tolerance: float = 0.02) -> dict:
    """Deterministic checks. `tolerance` absorbs rounding, not errors."""
    def val(k):
        c = champs.get(k) or {}
        return c.get("value") if c.get("found") else None

    echecs, avertissements, controles = [], [], []
    ht, tva, ttc = val("total_excl_vat"), val("vat_amount"), val("total_incl_vat")
    taux = val("vat_rate")

    if ht is not None and tva is not None and ttc is not None:
        ecart = round(abs((ht + tva) - ttc), 2)
        ok = ecart <= tolerance
        controles.append({"check": "total_excl_vat + vat = total_incl_vat",
                          "passed": ok, "gap": ecart})
        if not ok:
            echecs.append(f"Totals do not balance: {ht} + {tva} ≠ {ttc} "
                          f"(off by {ecart}).")
    else:
        avertissements.append("Totals incomplete — cannot verify the sum.")

    if ht is not None and tva is not None and taux is not None:
        attendu = round(ht * taux / 100, 2)
        ecart = round(abs(attendu - tva), 2)
        ok = ecart <= max(tolerance, 0.01 * max(1.0, attendu))
        controles.append({"check": f"VAT at {taux}% of net", "passed": ok,
                          "expected": attendu, "stated": tva, "gap": ecart})
        if not ok:
            echecs.append(f"VAT inconsistent: {taux}% of {ht} is {attendu}, "
                          f"invoice states {tva}.")

    if taux is not None:
        ok = float(taux) in TAUX_FR
        controles.append({"check": "VAT rate is a French statutory rate",
                          "passed": ok, "value": taux})
        if not ok:
            avertissements.append(
                f"{taux}% is not a French statutory rate "
                f"({', '.join(str(x) for x in sorted(TAUX_FR))}) — plausible for "
                f"a foreign supplier, otherwise a misread.")

    for cle, longueur in (("siren", 9), ("siret", 14)):
        v = val(cle)
        if v:
            ok = len(v) == longueur and _luhn(v)
            controles.append({"check": f"{cle.upper()} Luhn checksum",
                              "passed": ok, "value": v})
            if not ok:
                echecs.append(f"{cle.upper()} {v} fails its checksum — likely a "
                              f"misread digit.")

    v = val("vat_number")
    if v:
        ok = bool(re.fullmatch(r"[A-Z]{2}[0-9A-Z]{8,12}", v))
        controles.append({"check": "VAT number format", "passed": ok, "value": v})
        if not ok:
            echecs.append(f"Malformed VAT number: {v}")
        elif v.startswith("FR") and len(v) == 13 and _luhn(v[4:]):
            controles.append({"check": "FR VAT embeds a valid SIREN",
                              "passed": True, "value": v[4:]})

    manquants = [k for k in ("invoice_number", "invoice_date", "total_incl_vat")
                 if not (champs.get(k) or {}).get("found")]
    if manquants:
        echecs.append(f"Mandatory fields missing: {', '.join(manquants)}.")

    verdict = "ok" if not echecs and not avertissements else (
        "review" if echecs else "ok_with_warnings")
    return {
        "verdict": verdict,
        "failures": echecs,
        "warnings": avertissements,
        "checks": controles,
        "auto_postable": verdict == "ok",
        "note": ("Deterministic checks only — no model was consulted. "
                 "verdict='review' means a human must see it before posting."),
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
