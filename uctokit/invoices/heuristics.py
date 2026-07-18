"""Regexové heuristiky nad textem faktury.

Levná záloha (běží vždy, zdarma) a zároveň křížová kontrola LLM výsledku. Není
to hlavní tahoun – od toho je LLM –, ale u jednoduchých faktur si poradí sama a
u složitých dá aspoň částku/VS/IČO k porovnání.
"""

from __future__ import annotations

import re

from . import validators as V
from .types import Field, ExtractedInvoice, SOURCE_HEURISTIC

BASE_CONFIDENCE = 0.5

_ICO_RE = re.compile(r"I[ČC]O[:\s]*?(\d{8})", re.IGNORECASE)
_VS_RE = re.compile(r"(?:variabiln\w*\s*symbol|\bVS)\D{0,4}(\d{1,10})", re.IGNORECASE)
_IBAN_RE = re.compile(r"\bCZ\d{2}(?:\s?[0-9]){20}\b")
_ACCOUNT_RE = re.compile(r"\b(?:\d{1,6}-)?\d{2,10}/\d{4}\b")
_AMOUNT_RE = re.compile(r"(\d[\d  ]*[.,]\d{2})")
_AMOUNT_KEYED_RE = re.compile(
    r"(?:k\s+úhrad\w*|celkem\s+k\s+úhrad\w*|celkem)\D{0,20}(\d[\d  ]*[.,]\d{2})",
    re.IGNORECASE,
)


def _field(value, raw=None) -> Field:
    if value in (None, ""):
        return Field()
    return Field(value=value, confidence=BASE_CONFIDENCE, source=SOURCE_HEURISTIC, raw=raw)


def extract_from_text(text: str, source: str = SOURCE_HEURISTIC) -> ExtractedInvoice:
    """Vytěží co jde regexy. ``source`` umožní přeznačit původ (např. OCR)."""
    inv = ExtractedInvoice()
    if not text:
        return inv

    ico = _ICO_RE.search(text)
    if ico:
        inv.supplier_ico = _field(V.normalize_ico(ico.group(1)), raw=ico.group(1))

    vs = _VS_RE.search(text)
    if vs:
        inv.variable_symbol = _field(V.normalize_vs(vs.group(1)), raw=vs.group(1))

    iban = _IBAN_RE.search(text)
    if iban:
        inv.supplier_iban = _field(V.normalize_iban(iban.group(0)), raw=iban.group(0))

    account = _ACCOUNT_RE.search(text)
    if account:
        inv.supplier_account = _field(V.normalize_account(account.group(0)), raw=account.group(0))

    d = V.normalize_date(text)
    if d:
        inv.issue_date = _field(d)

    keyed = _AMOUNT_KEYED_RE.search(text)
    if keyed:
        inv.total_amount = _field(V.normalize_amount(keyed.group(1)), raw=keyed.group(1))
    else:
        amounts = [V.normalize_amount(m) for m in _AMOUNT_RE.findall(text)]
        amounts = [a for a in amounts if a is not None]
        if amounts:
            biggest = max(amounts)
            inv.total_amount = _field(biggest, raw=str(biggest))

    if inv.total_amount.is_present and not inv.currency.is_present:
        inv.currency = _field("CZK")

    # Přeznačit původ (např. při OCR) a případně snížit důvěru.
    if source != SOURCE_HEURISTIC:
        for _, f in inv.items():
            if f.is_present:
                f.source = source
    return inv
