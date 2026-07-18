"""Orchestrace extrakce – žebřík ISDOC → text+LLM → sken (vision/OCR)+LLM.

Čisté funkce nad dataclassami. LLM se předává jako :class:`LLMInvoiceExtractor`
(v testech falešný), nebo se sestaví z konfigurace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import ocr as _ocr
from . import pdf_text as _pdf
from .heuristics import extract_from_text
from .isdoc import parse_isdoc
from .llm.base import LLMConfig, LLMInvoiceExtractor, build_provider
from .scoring import overall_confidence, rescore
from .types import (
    ExtractedInvoice,
    ExtractionResult,
    Field,
    SOURCE_HEURISTIC,
    SOURCE_ISDOC,
    SOURCE_OCR,
    SOURCE_VISION,
    SourceDocument,
)


@dataclass(frozen=True)
class ExtractionConfig:
    """Nastavení běhu extrakce (frameworkově neutrální; Django slupka ho sestaví)."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    enable_llm: bool = True
    enable_ocr: bool = True
    min_text_chars: int = 40
    vision_max_pages: int = 3


def extract(
    document: SourceDocument,
    config: ExtractionConfig | None = None,
    *,
    llm: LLMInvoiceExtractor | None = None,
) -> ExtractionResult:
    """Hlavní vstupní bod. Vždy vrátí :class:`ExtractionResult` (i prázdný)."""
    config = config or ExtractionConfig()
    content = document.content or b""

    # 1) ISDOC (podle přípony i podle obsahu – XML začíná '<').
    if document.looks_like_isdoc or content.lstrip()[:1] == b"<":
        inv = parse_isdoc(content)
        if inv is not None:
            warnings = rescore(inv, None)
            return ExtractionResult(
                invoice=inv,
                method=SOURCE_ISDOC,
                overall_confidence=overall_confidence(inv),
                warnings=warnings,
            )

    extractor = _resolve_llm(config, llm)

    # 2) PDF s textovou vrstvou.
    if document.looks_like_pdf:
        raw_text = _pdf.extract_text(content)
        if not _pdf.is_scanned(raw_text, min_chars=config.min_text_chars):
            return _from_text(
                raw_text, extractor, config,
                heur_source=SOURCE_HEURISTIC,
                method_with_llm="pdf-text+llm", method_plain="pdf-text",
            )
        return _from_scan(document, raw_text, extractor, config)

    # 3) Obrázek (sken jako PNG/JPG).
    if document.looks_like_image:
        return _from_image(document, extractor, config)

    # 4) Neznámý typ: zkus text (pdfplumber si někdy poradí), jinak prázdné.
    raw_text = _pdf.extract_text(content)
    return _from_text(
        raw_text, extractor, config,
        heur_source=SOURCE_HEURISTIC,
        method_with_llm="text+llm", method_plain="text",
    )


# --- Dílčí cesty -------------------------------------------------------------

def _from_text(
    raw_text, extractor, config, *, heur_source, method_with_llm, method_plain,
) -> ExtractionResult:
    warnings: list[str] = []
    heur = extract_from_text(raw_text, source=heur_source)

    llm_inv, llm_used = None, False
    if extractor and config.enable_llm and (raw_text or "").strip():
        try:
            llm_inv = extractor.from_text(raw_text)
            llm_used = any(f.is_present for _, f in llm_inv.items())
        except Exception:
            warnings.append("LLM extrakce z textu selhala – použity heuristiky.")

    merged, merge_warnings = _merge(llm_inv, heur)
    warnings.extend(merge_warnings)
    warnings.extend(rescore(merged, raw_text))

    method = method_with_llm if llm_used else method_plain
    if not any(f.is_present for _, f in merged.items()):
        method = ""
    return ExtractionResult(
        invoice=merged,
        method=method,
        overall_confidence=overall_confidence(merged),
        warnings=warnings,
        raw_text=raw_text or None,
    )


def _from_scan(document, raw_text, extractor, config) -> ExtractionResult:
    warnings: list[str] = []

    # a) Vision LLM (preferováno, když je vision model k dispozici).
    if extractor and config.enable_llm and extractor.supports_vision:
        images = _pdf.render_page_images(document.content, max_pages=config.vision_max_pages)
        if images:
            try:
                vis = extractor.from_images(images)
                if any(f.is_present for _, f in vis.items()):
                    warnings.extend(rescore(vis, raw_text or None))
                    return ExtractionResult(
                        invoice=vis,
                        method=SOURCE_VISION,
                        overall_confidence=overall_confidence(vis),
                        warnings=warnings,
                        raw_text=raw_text or None,
                    )
            except Exception:
                warnings.append("Vision extrakce selhala – zkouším OCR.")

    # b) OCR → textová cesta.
    if config.enable_ocr:
        ocr_text = _ocr.ocr_pdf(document.content)
        if ocr_text.strip():
            result = _from_text(
                ocr_text, extractor, config,
                heur_source=SOURCE_OCR,
                method_with_llm="ocr+llm", method_plain="ocr",
            )
            result.warnings = warnings + result.warnings
            return result

    return ExtractionResult(
        invoice=ExtractedInvoice(),
        method="",
        overall_confidence=0.0,
        warnings=warnings + ["Naskenovaný doklad se nepodařilo přečíst (chybí vision model i OCR)."],
        raw_text=raw_text or None,
    )


def _from_image(document, extractor, config) -> ExtractionResult:
    warnings: list[str] = []
    if extractor and config.enable_llm and extractor.supports_vision:
        try:
            vis = extractor.from_images([document.content])
            if any(f.is_present for _, f in vis.items()):
                warnings.extend(rescore(vis, None))
                return ExtractionResult(
                    invoice=vis,
                    method=SOURCE_VISION,
                    overall_confidence=overall_confidence(vis),
                    warnings=warnings,
                )
        except Exception:
            warnings.append("Vision extrakce z obrázku selhala – zkouším OCR.")

    if config.enable_ocr:
        ocr_text = _ocr.ocr_image(document.content)
        if ocr_text.strip():
            result = _from_text(
                ocr_text, extractor, config,
                heur_source=SOURCE_OCR,
                method_with_llm="ocr+llm", method_plain="ocr",
            )
            result.warnings = warnings + result.warnings
            return result

    return ExtractionResult(
        invoice=ExtractedInvoice(),
        method="",
        overall_confidence=0.0,
        warnings=warnings + ["Obrázek se nepodařilo přečíst (chybí vision model i OCR)."],
    )


# --- Slučování LLM + heuristik ----------------------------------------------

def _values_agree(name: str, a, b) -> bool:
    if name == "total_amount":
        return isinstance(a, Decimal) and isinstance(b, Decimal) and a == b
    if name in ("issue_date", "due_date"):
        return a == b
    return str(a).strip().upper() == str(b).strip().upper()


def _merge(llm_inv: ExtractedInvoice | None, heur: ExtractedInvoice):
    """Sloučí LLM a heuristiku po polích. LLM vyhrává hodnotou; shoda zvedá důvěru."""
    result = ExtractedInvoice()
    warnings: list[str] = []

    for name, _ in result.items():
        lf = getattr(llm_inv, name) if llm_inv is not None else Field()
        hf = getattr(heur, name)

        if lf.is_present and hf.is_present:
            if _values_agree(name, lf.value, hf.value):
                chosen = Field(lf.value, min(0.95, lf.confidence + 0.15), lf.source, lf.raw)
            else:
                chosen = Field(lf.value, max(0.4, lf.confidence - 0.15), lf.source, lf.raw)
                warnings.append(
                    f"{name}: heuristika '{hf.value}' != LLM '{lf.value}' - zkontrolujte."
                )
        elif lf.is_present:
            chosen = lf
        elif hf.is_present:
            chosen = hf
        else:
            chosen = Field()
        setattr(result, name, chosen)

    return result, warnings


def _resolve_llm(config: ExtractionConfig, override):
    if override is not None:
        return override
    if not config.enable_llm:
        return None
    provider = build_provider(config.llm)
    return LLMInvoiceExtractor(provider) if provider else None
