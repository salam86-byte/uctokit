"""Odvození míry jistoty z kontrol (validátorů).

Jeden centrální bod, kde se z „hrubé" jistoty podle zdroje (ISDOC/LLM/heuristika)
udělá finální jistota pole na základě toho, jestli hodnota projde kontrolou
(IČO/IBAN checksum, částka dohledatelná v textu, sanita dat…). Vrací i seznam
varování pro účetního.
"""

from __future__ import annotations

from decimal import Decimal

from . import validators as V
from .heuristics import strip_diacritics
from .types import SOURCE_LLM, SOURCE_VISION, ExtractedInvoice

_MIN, _MAX = 0.05, 0.99

# Slova, kterými faktura mluví o datu plnění. Hledá se v textu bez diakritiky
# A BEZ MEZER — část PDF má písmena proložená („d a tu m u s ku te č n ěn í
# pl ně n í“), takže na mezery se spolehnout nedá.
_TAX_POINT_MARKERS = ("plneni", "duzp", "dupz", "deliverydate", "taxpointdate",
                      "dateofsupply")


def mentions_tax_point(raw_text: str) -> bool:
    """Zmiňuje doklad vůbec datum plnění?

    Schválně velkoryse: je to podlaha proti vymýšlení, ne důkaz. Když
    v dokladu není o plnění ani slovo, nemohl z něj model žádné DUZP
    přečíst — ať už napíše cokoli.
    """
    norm = strip_diacritics(raw_text or "").lower()
    return any(marker in "".join(norm.split()) for marker in _TAX_POINT_MARKERS)


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
            # Záporná částka je legitimní dobropis (opravný doklad), ne chyba –
            # jen nula/nečitelno je problém. V textu se hledá bez znaménka.
            if amount is None or amount == 0:
                f.confidence = _clamp(base - 0.35)
                warnings.append("Částka je nulová nebo nečitelná – zkontrolujte.")
            elif V.amount_in_text(abs(amount), raw_text):
                f.confidence = _clamp(base + 0.2)
            else:
                f.confidence = _clamp(base)

        elif name == "variable_symbol":
            in_text = V.token_in_text(str(f.value), raw_text)
            f.confidence = _clamp(base + (0.15 if in_text else 0.0))

        elif name == "taxable_date" and f.source in (SOURCE_LLM, SOURCE_VISION) \
                and raw_text and not mentions_tax_point(raw_text):
            # Model dopisuje DUZP i tam, kde ho doklad vůbec nemá — typicky
            # opíše datum vystavení. Pokyn v promptu na to nestačil (ověřeno
            # na dvou fakturách, kde v textu není o plnění ani slovo), a
            # vymyšlené datum plnění posouvá DPH do jiného období. Radši
            # prázdno: účetní ho doplní, když ho na papíře vidí.
            warnings.append(
                f"Datum zdanitelného plnění '{f.value}' doklad neuvádí — "
                f"model si ho domyslel, proto se nepoužilo."
            )
            f.value, f.raw, f.confidence = None, None, 0.0

        elif name in ("issue_date", "taxable_date", "due_date"):
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
    # Hotovostní doklad nemá VS ani splatnost (platí se na místě) – nesmí ho to
    # táhnout dolů jako „chybějící data". Rozhoduje jen dodavatel + částka.
    if inv.payment_in_cash:
        key_fields = ("supplier_name", "supplier_ico", "total_amount")
    scores = [getattr(inv, name).confidence for name in key_fields]
    present = [s for s in scores if s > 0]
    if not present:
        return 0.0
    # Chybějící klíčové pole táhne průměr dolů (počítá se jako 0).
    return round(sum(present) / len(key_fields), 3)
