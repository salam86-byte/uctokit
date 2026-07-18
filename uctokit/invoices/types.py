"""Datové kontrakty extrakce přijatých faktur.

Všechno je čistý Python (dataclassy, ``Decimal``, ``date``) – žádná vazba na
Django ani konkrétní úložiště. Django slupka si výsledek namapuje na modely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# Kanonický seznam polí, která z faktury vytěžujeme. Pořadí = pořadí v UI.
FIELD_NAMES: tuple[str, ...] = (
    "supplier_name",
    "supplier_ico",
    "supplier_iban",
    "supplier_account",
    "total_amount",
    "currency",
    "variable_symbol",
    "invoice_number",
    "issue_date",
    "due_date",
)

# Zdroj hodnoty (řadí se od nejdůvěryhodnějšího). Slouží i jako `method`.
SOURCE_ISDOC = "isdoc"
SOURCE_PDF_TEXT = "pdf-text"
SOURCE_OCR = "ocr"
SOURCE_LLM = "llm"
SOURCE_VISION = "vision"
SOURCE_HEURISTIC = "heuristic"
SOURCE_MANUAL = "manual"


# --- Vstup -------------------------------------------------------------------

@dataclass(frozen=True)
class SourceDocument:
    """Jeden nahraný doklad k extrakci.

    ``content`` jsou syrové bajty souboru, ``filename`` původní název (kvůli
    příponě) a ``media_type`` volitelně MIME z HTTP uploadu.
    """

    content: bytes
    filename: str
    media_type: str | None = None

    @property
    def suffix(self) -> str:
        name = (self.filename or "").lower()
        _, _, ext = name.rpartition(".")
        return ext if "." in name else ""

    @property
    def looks_like_isdoc(self) -> bool:
        return self.suffix in {"isdoc", "xml"} or (
            self.media_type or ""
        ).endswith(("xml", "isdoc"))

    @property
    def looks_like_pdf(self) -> bool:
        return self.suffix == "pdf" or (self.media_type or "") == "application/pdf"

    @property
    def looks_like_image(self) -> bool:
        return self.suffix in {"png", "jpg", "jpeg", "webp", "tiff", "tif"} or (
            self.media_type or ""
        ).startswith("image/")


# --- Výstup ------------------------------------------------------------------

# Normalizovaná hodnota pole může být řetězec, částka nebo datum (nebo nic).
FieldValue = str | Decimal | date | None


@dataclass
class Field:
    """Jedno vytěžené pole i s mírou jistoty a původem.

    ``confidence`` je 0.0–1.0. ``raw`` je původní podoba tak, jak byla v dokladu
    nalezena (pro audit / vysvětlení účetnímu).
    """

    value: FieldValue = None
    confidence: float = 0.0
    source: str = ""
    raw: str | None = None

    @property
    def is_present(self) -> bool:
        return self.value not in (None, "")


def empty_field() -> Field:
    return Field()


@dataclass
class ExtractedInvoice:
    """Sada vytěžených polí. Každé pole nese vlastní jistotu."""

    supplier_name: Field = field(default_factory=empty_field)
    supplier_ico: Field = field(default_factory=empty_field)
    supplier_iban: Field = field(default_factory=empty_field)
    supplier_account: Field = field(default_factory=empty_field)
    total_amount: Field = field(default_factory=empty_field)
    currency: Field = field(default_factory=empty_field)
    variable_symbol: Field = field(default_factory=empty_field)
    invoice_number: Field = field(default_factory=empty_field)
    issue_date: Field = field(default_factory=empty_field)
    due_date: Field = field(default_factory=empty_field)

    def items(self):
        """Iteruje ``(název_pole, Field)`` v kanonickém pořadí."""
        for name in FIELD_NAMES:
            yield name, getattr(self, name)

    def values(self) -> dict[str, FieldValue]:
        """Ploché ``{pole: hodnota}`` (bez jistoty)."""
        return {name: f.value for name, f in self.items()}

    def confidences(self) -> dict[str, float]:
        return {name: round(f.confidence, 3) for name, f in self.items()}


@dataclass
class ExtractionResult:
    """Výsledek extrakce: pole + přehledová metadata."""

    invoice: ExtractedInvoice = field(default_factory=ExtractedInvoice)
    method: str = ""
    overall_confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    raw_text: str | None = None

    def to_legacy_dict(self) -> dict:
        """Zpětně kompatibilní plochý dict ve tvaru původního extraktoru.

        Používá tenká Django slupka i staré testy. Datové typy jsou stejné jako
        dřív (Decimal / date / str), prázdné řetězce místo ``None`` u textů.
        """
        inv = self.invoice
        return {
            "supplier_name": inv.supplier_name.value or "",
            "supplier_ico": inv.supplier_ico.value or "",
            "supplier_iban": inv.supplier_iban.value or "",
            "supplier_account": inv.supplier_account.value or "",
            "total_amount": inv.total_amount.value,
            "currency": inv.currency.value or "",
            "variable_symbol": inv.variable_symbol.value or "",
            "invoice_number": inv.invoice_number.value or "",
            "issue_date": inv.issue_date.value,
            "due_date": inv.due_date.value,
            "method": self.method,
        }
