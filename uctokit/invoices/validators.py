"""Normalizace a validace vytěžených hodnot + odvození míry jistoty.

Míru jistoty (confidence) odvozujeme *deterministicky* z kontrol (kontrolní
číslice IČO, checksum IBAN, dohledatelnost částky v textu...), ne ze sebehodnocení
LLM - to je pro účetní doklad podstatně důvěryhodnější a snadno testovatelné.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

# --- Normalizace -------------------------------------------------------------

_AMOUNT_CLEAN = re.compile(r"[^\d,.\-]")


def normalize_amount(raw) -> Decimal | None:
    """Převede částku (řetězec i číslo) na ``Decimal`` na 2 desetinná místa.

    Zvládá české formáty ``1 234,50`` i ``1.234,50`` i strojové ``1234.50``.
    """
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw.quantize(Decimal("0.01"))
    if isinstance(raw, (int, float)):
        try:
            return Decimal(str(raw)).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None

    text = _AMOUNT_CLEAN.sub("", str(raw)).strip()
    if not text:
        return None
    # Rozliš desetinný oddělovač: poslední z '.' / ',' bereme jako desetinný.
    last_comma, last_dot = text.rfind(","), text.rfind(".")
    if last_comma > last_dot:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def normalize_date(raw) -> date | None:
    """Rozparsuje ISO i české datum se čtyř- nebo dvouciferným rokem.

    Dvouciferný rok se u faktur vykládá jako 2000–2099. Starší doklady
    se v importu neočekávají a tento postup brání tomu, aby ``26`` skončilo
    jako rok 1926.
    """
    if isinstance(raw, date):
        return raw
    if not raw:
        return None
    text = str(raw).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2}|\d{4})(?!\d)", text)
    if m:
        year = int(m.group(3))
        if len(m.group(3)) == 2:
            year += 2000
        return _safe_date(year, int(m.group(2)), int(m.group(1)))
    return None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def normalize_ico(raw) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits.zfill(8) if 0 < len(digits) <= 8 else digits


def normalize_vs(raw) -> str:
    return re.sub(r"\D", "", str(raw or ""))[:10]


def normalize_iban(raw) -> str:
    return re.sub(r"\s", "", str(raw or "")).upper()


def normalize_account(raw) -> str:
    """Sjednotí české číslo účtu na tvar ``[předčíslí-]číslo/kód`` bez mezer."""
    return re.sub(r"\s", "", str(raw or ""))


# --- Validace ----------------------------------------------------------------

def valid_ico(ico: str) -> bool:
    """Kontrolní číslice českého IČO (mod 11)."""
    ico = re.sub(r"\D", "", ico or "")
    if len(ico) != 8:
        return False
    weights = (8, 7, 6, 5, 4, 3, 2)
    checksum = sum(int(d) * w for d, w in zip(ico, weights))
    remainder = checksum % 11
    check = (11 - remainder) % 10
    return check == int(ico[7])


def valid_iban(iban: str) -> bool:
    """Obecná IBAN kontrola (mod 97 == 1) + délka pro CZ."""
    iban = normalize_iban(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", iban or ""):
        return False
    if iban.startswith("CZ") and len(iban) != 24:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False


def date_sane(value, *, min_year: int = 2000, max_year: int = 2100) -> bool:
    return isinstance(value, date) and min_year <= value.year <= max_year


def amount_in_text(amount, text) -> bool:
    """Je částka (bez ohledu na oddělovače) dohledatelná v syrovém textu?"""
    if amount is None or not text:
        return False
    whole = str(int(amount))
    grouped = f"{int(amount):,}".replace(",", r"[\s.]?")
    pattern = re.compile(rf"{grouped}(?:[.,]\d{{2}})?|{whole}(?:[.,]\d{{2}})?")
    return bool(pattern.search(text))


def token_in_text(token, text) -> bool:
    if not token or not text:
        return False
    return re.search(rf"\b{re.escape(token)}\b", text) is not None
