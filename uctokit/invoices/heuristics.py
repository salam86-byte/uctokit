"""Regexové heuristiky nad textem faktury.

Levná záloha (běží vždy, zdarma) a zároveň křížová kontrola LLM výsledku. Není
to hlavní tahoun – od toho je LLM –, ale u jednoduchých faktur si poradí sama a
u složitých dá aspoň částku/VS/IČO k porovnání.
"""

from __future__ import annotations

import re
import unicodedata

from . import validators as V
from .types import Field, ExtractedInvoice, SOURCE_HEURISTIC

BASE_CONFIDENCE = 0.5

_ICO_RE = re.compile(r"I[ČC](?:O)?[:\s]*?(\d{8})", re.IGNORECASE)
_VS_RE = re.compile(r"(?:variabiln\w*\s*symbol|\bVS)\D{0,4}(\d{1,10})", re.IGNORECASE)
_IBAN_RE = re.compile(r"\bCZ\d{2}(?:\s?[0-9]){20}\b")
_ACCOUNT_RE = re.compile(r"\b(?:\d{1,6}-)?\d{3,10}/\d{4}\b")
_SEPARATE_ACCOUNT_RE = re.compile(
    r"číslo\s+účtu\D{0,30}(\d{1,10})[^\n]*\n"
    r"[^\n]*(?:identifikace\s+banky|kód\s+banky)\D{0,30}(\d{4})",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"(\d[\d  ]*[.,]\d{2})")
_AMOUNT_TOKEN = r"((?:\d{1,3}(?:[ .]\d{3})+|\d+)(?:[.,]\d{2})?)"
_AMOUNT_KEYED_RE = re.compile(
    r"(?:celkem\s+k\s+úhrad\w*|k\s+úhrad\w*|celkem)\D{0,30}" + _AMOUNT_TOKEN,
    re.IGNORECASE,
)
_DATE_VALUE = r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4})"
_ISSUE_DATE_RE = re.compile(
    r"(?:datum\s+vystavení|datum\s+vystavení\s+faktury|\bdatum\s*:)\D{0,12}" + _DATE_VALUE,
    re.IGNORECASE,
)
_DUE_DATE_RE = re.compile(
    r"(?:datum\s+splatnosti|splatnost(?:\s+faktury)?)\D{0,12}" + _DATE_VALUE,
    re.IGNORECASE,
)
def strip_diacritics(text: str) -> str:
    """„plnění" → „plneni". Část faktur jede bez diakritiky (reca, Ahoj, …)."""
    norm = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in norm if not unicodedata.combining(c))


# DUZP. Na fakturách stojí pod hromadou různých názvů, zkracuje se kde kdo
# kde chce a část dodavatelů píše bez diakritiky. Ověřeno na skutečných
# fakturách, každá varianta se opravdu vyskytla:
#
#     Datum zdanit. plnění                  — zkrácený přívlastek
#     Datum uskutečnění plnění              — bez „zdanitelného"
#     Datum uskutecneni zdanitelneho plneni — bez diakritiky
#     DUZP / Dat. usk. zdan. plnění
#
# Proto se hledá nad textem BEZ diakritiky a „zdanitelného" je nepovinné.
# Samotné „plnění" bez uvozujícího „datum" se nechytá — to je i ve větách
# typu „plnění dle smlouvy".
_TAXABLE_DATE_RE = re.compile(
    r"(?:dat(?:um)?\.?\s*(?:usk(?:utecneni)?\.?\s*)?(?:zdan\w*\.?\s*)?plneni"
    r"|\bDUZP\b|\bDUPZ\b)"
    r"\D{0,12}" + _DATE_VALUE,
    re.IGNORECASE,
)
_INVOICE_NUMBER_RE = re.compile(
    r"(?:č\.?\s*faktury|(?:číslo|doklad)\s+faktury|daňový\s+doklad\s+číslo)"
    r"[ \t]*:?[ \t]*([A-Z0-9][A-Z0-9./_-]{0,59})",
    re.IGNORECASE,
)
_LEADING_INVOICE_NUMBER_RE = re.compile(
    r"^\s*faktura\s+(?:č(?:íslo|\.)?\s*)?([A-Z0-9][A-Z0-9./_-]{0,59})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_XLSX_HEADER_SUPPLIER_RE = re.compile(
    r"^\s*([^\t\n]{2,200})\t+FAKTURA(?:\t|$)", re.IGNORECASE | re.MULTILINE,
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

    ico = _supplier_ico_match(text)
    if ico:
        inv.supplier_ico = _field(V.normalize_ico(ico.group(1)), raw=ico.group(1))

    vs = _VS_RE.search(text)
    if vs:
        inv.variable_symbol = _field(V.normalize_vs(vs.group(1)), raw=vs.group(1))

    iban = _IBAN_RE.search(text)
    if iban:
        inv.supplier_iban = _field(V.normalize_iban(iban.group(0)), raw=iban.group(0))

    separate_account = _SEPARATE_ACCOUNT_RE.search(text)
    if separate_account:
        raw_account = f"{separate_account.group(1)}/{separate_account.group(2)}"
        inv.supplier_account = _field(V.normalize_account(raw_account), raw=raw_account)
    else:
        account = _ACCOUNT_RE.search(text)
        if account:
            inv.supplier_account = _field(V.normalize_account(account.group(0)), raw=account.group(0))

    issue = _ISSUE_DATE_RE.search(text)
    issue_raw = issue.group(1) if issue else text
    issue_date = V.normalize_date(issue_raw)
    if issue_date:
        inv.issue_date = _field(issue_date, raw=issue.group(1) if issue else None)

    due = _DUE_DATE_RE.search(text)
    if due:
        due_date = V.normalize_date(due.group(1))
        if due_date:
            inv.due_date = _field(due_date, raw=due.group(1))

    # Nad textem bez diakritiky — datum samo diakritiku nemá, takže se dá
    # vzít rovnou z shody.
    taxable = _TAXABLE_DATE_RE.search(strip_diacritics(text))
    if taxable:
        taxable_date = V.normalize_date(taxable.group(1))
        if taxable_date:
            inv.taxable_date = _field(taxable_date, raw=taxable.group(1))

    number = _LEADING_INVOICE_NUMBER_RE.search(text) or _INVOICE_NUMBER_RE.search(text)
    if number:
        inv.invoice_number = _field(number.group(1).strip(), raw=number.group(0))

    supplier = _XLSX_HEADER_SUPPLIER_RE.search(text)
    if supplier:
        inv.supplier_name = _field(supplier.group(1).strip(), raw=supplier.group(1))

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


def _supplier_ico_match(text: str):
    """Vybere IČO dodavatele, i když PDF promíchá dva sloupce.

    U layoutu s oběma nadpisy ``Dodavatel``/``Odběratel`` bývá IČO
    dodavatele blíž jeho bankovnímu účtu. Mimo tento konkrétní layout
    zachováváme bezpečné původní chování a vezmeme první IČO.
    """
    matches = list(_ICO_RE.finditer(text))
    if len(matches) < 2:
        return matches[0] if matches else None
    lowered = text.lower()
    if "dodavatel" not in lowered or "odběratel" not in lowered:
        return matches[0]
    anchor = _IBAN_RE.search(text) or _ACCOUNT_RE.search(text)
    if anchor is None:
        return matches[0]
    return min(matches, key=lambda m: abs(m.start() - anchor.start()))
