"""Odvození míry jistoty z kontrol (validátorů).

Jeden centrální bod, kde se z „hrubé" jistoty podle zdroje (ISDOC/LLM/heuristika)
udělá finální jistota pole na základě toho, jestli hodnota projde kontrolou
(IČO/IBAN checksum, částka dohledatelná v textu, sanita dat…). Vrací i seznam
varování pro účetního.
"""

from __future__ import annotations

from decimal import Decimal

from . import validators as V
from .types import ExtractedInvoice

_MIN, _MAX = 0.05, 0.99


def _clamp(x: float) -> float:
    return max(_MIN, min(_MAX, x))


def rescore(inv: ExtractedInvoice, raw_text: str | None) -> list[str]:
    """Upraví ``confidence`` polí podle validátorů a vrátí varování."""
    warnings: list[str] = []

    for name, f in inv.items():
        if not f.is_present:
            f.confidence = 0.0
            continue
        base = f.confidence

        if name == "supplier_ico":
            ok = V.valid_ico(str(f.value))
            f.confidence = _clamp(base + (0.25 if ok else -0.35))
            if not ok:
                warnings.append(f"IČO '{f.value}' neprošlo kontrolní číslicí.")

        elif name == "supplier_iban":
            ok = V.valid_iban(str(f.value))
            f.confidence = _clamp(base + (0.25 if ok else -0.35))
            if not ok:
                warnings.append(f"IBAN '{f.value}' neprošel kontrolou (checksum).")

        elif name == "total_amount":
            amount = f.value if isinstance(f.value, Decimal) else V.normalize_amount(f.value)
            if amount is None or amount <= 0:
                f.confidence = _clamp(base - 0.35)
                warnings.append("Částka je nulová nebo nečitelná – zkontrolujte.")
            elif V.amount_in_text(amount, raw_text):
                f.confidence = _clamp(base + 0.2)
            else:
                f.confidence = _clamp(base)

        elif name == "variable_symbol":
            in_text = V.token_in_text(str(f.value), raw_text)
            f.confidence = _clamp(base + (0.15 if in_text else 0.0))

        elif name in ("issue_date", "due_date"):
            ok = V.date_sane(f.value)
            f.confidence = _clamp(base + (0.15 if ok else -0.3))
            if not ok:
                warnings.append(f"Datum v poli {name} je mimo očekávaný rozsah.")
        else:
            f.confidence = _clamp(base)

    # Křížová kontrola: splatnost nesmí být před vystavením.
    issue, due = inv.issue_date, inv.due_date
    if issue.is_present and due.is_present and due.value < issue.value:
        warnings.append("Splatnost je před datem vystavení.")
        due.confidence = _clamp(due.confidence - 0.2)

    return warnings


def overall_confidence(inv: ExtractedInvoice) -> float:
    """Průměr jistot klíčových polí, které rozhodují o zaplatitelnosti faktury."""
    key_fields = ("supplier_name", "supplier_ico", "total_amount", "variable_symbol", "due_date")
    scores = [getattr(inv, name).confidence for name in key_fields]
    present = [s for s in scores if s > 0]
    if not present:
        return 0.0
    # Chybějící klíčové pole táhne průměr dolů (počítá se jako 0).
    return round(sum(present) / len(key_fields), 3)
