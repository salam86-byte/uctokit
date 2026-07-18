"""Extrakce údajů z přijatých faktur (frameworkově neutrální jádro).

Veřejné API::

    from uctokit.invoices import extract, SourceDocument, ExtractionConfig
    from uctokit.invoices import LLMConfig, LLMEndpoint

    doc = SourceDocument(content=raw_bytes, filename="faktura.pdf")
    result = extract(doc, ExtractionConfig(llm=LLMConfig(endpoints=(...,))))
    result.invoice.total_amount.value        # Decimal("13000.00")
    result.invoice.total_amount.confidence   # 0.0–1.0
    result.overall_confidence, result.method, result.warnings
"""

from .llm.base import LLMConfig, LLMEndpoint, LLMInvoiceExtractor, LLMProvider
from .pipeline import ExtractionConfig, extract
from .types import (
    FIELD_NAMES,
    ExtractedInvoice,
    ExtractionResult,
    Field,
    SourceDocument,
)

__all__ = [
    "extract",
    "ExtractionConfig",
    "SourceDocument",
    "ExtractionResult",
    "ExtractedInvoice",
    "Field",
    "FIELD_NAMES",
    "LLMConfig",
    "LLMEndpoint",
    "LLMProvider",
    "LLMInvoiceExtractor",
]
