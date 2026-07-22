"""Orchestrace extrakce – žebřík ISDOC → text+LLM → sken (vision/OCR)+LLM.

Čisté funkce nad dataclassami. LLM se předává jako :class:`LLMInvoiceExtractor`
(v testech falešný), nebo se sestaví z konfigurace.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from . import ocr as _ocr
from . import pdf_text as _pdf
from . import qr as _qr
from . import validators as _V
from . import xlsx_text as _xlsx
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
    SOURCE_QR,
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

    # 2) QR Platba / QR Faktura (deterministické) – nejjistější zdroj po ISDOCu.
    #    Běžné QR Platba ale neobsahuje dodavatele, IČO ani datum vystavení,
    #    proto AI přeskakujeme jen u skutečně kompletního QR Faktura (SID).
    qr_inv = None
    try:
        qr_inv = _qr.extract_from_qr(document)
    except Exception:
        qr_inv = None
    run_config = config
    if qr_inv is not None and _qr_covers_invoice(qr_inv):
        run_config = replace(config, enable_llm=False)

    result = _run_ladder(document, run_config, llm)
    if qr_inv is not None:
        result = _apply_qr(result, qr_inv)
    return result


def _run_ladder(document, config, llm) -> ExtractionResult:
    """Zbytek žebříčku pod QR: text+LLM → sken (vision/OCR)+LLM."""
    content = document.content or b""
    extractor = _resolve_llm(config, llm)

    # PDF s textovou vrstvou.
    if document.looks_like_pdf:
        raw_text = _pdf.extract_text(content)
        if not _pdf.is_scanned(raw_text, min_chars=config.min_text_chars):
            return _from_text(
                raw_text, extractor, config,
                heur_source=SOURCE_HEURISTIC,
                method_with_llm="pdf-text+llm", method_plain="pdf-text",
            )
        return _from_scan(document, raw_text, extractor, config)

    # Tabulková faktura (XLSX) – sešit zploštíme na text a jedeme textovou cestou.
    if document.looks_like_xlsx or document.looks_like_xls:
        raw_text = (_xlsx.extract_text_xls(content) if document.looks_like_xls
                    else _xlsx.extract_text(content))
        return _from_text(
            raw_text, extractor, config,
            heur_source=SOURCE_HEURISTIC,
            method_with_llm="xlsx+llm", method_plain="xlsx",
        )

    # Obrázek (sken jako PNG/JPG).
    if document.looks_like_image:
        return _from_image(document, extractor, config)

    # Neznámý typ: zkus text (pdfplumber si někdy poradí), jinak prázdné.
    raw_text = _pdf.extract_text(content)
    return _from_text(
        raw_text, extractor, config,
        heur_source=SOURCE_HEURISTIC,
        method_with_llm="text+llm", method_plain="text",
    )


def _qr_covers_payment(qr: ExtractedInvoice) -> bool:
    """QR nese to podstatné k zaplacení (účet/IBAN + částka) → AI netřeba."""
    has_account = qr.supplier_account.is_present or qr.supplier_iban.is_present
    return has_account and qr.total_amount.is_present


def _qr_covers_invoice(qr: ExtractedInvoice) -> bool:
    """QR má platbu i identitu dokladu, takže AI už nemá co podstatného doplnit."""
    has_supplier = qr.supplier_name.is_present or qr.supplier_ico.is_present
    return (
        _qr_covers_payment(qr)
        and has_supplier
        and qr.invoice_number.is_present
        and qr.issue_date.is_present
        and qr.due_date.is_present
    )


# Pole, která QR NESMÍ přepsat, když už mají hodnotu z bohatšího zdroje.
# QR/SPD nese jen oříznutý název příjemce (verzálky, bez diakritiky).
_QR_NAME_KEEP = {"supplier_name"}


def _apply_qr(result: ExtractionResult, qr: ExtractedInvoice) -> ExtractionResult:
    """Přiloží QR pole navrch (deterministická, nejvyšší priorita)."""
    inv = result.invoice
    warnings = list(result.warnings)
    contributed = False
    for name, qf in qr.items():
        if not qf.is_present:
            continue
        existing = getattr(inv, name)
        # Název dodavatele z QR/SPD je verzálkami, bez diakritiky a oříznutý na
        # délku — pokud už název máme z bohatšího zdroje (text/LLM/ISDOC),
        # NEPŘEPISUJ ho. QR název slouží jen jako záloha, když jinde není.
        if name in _QR_NAME_KEEP and existing.is_present:
            continue
        if existing.is_present and not _values_agree(name, existing.value, qf.value):
            warnings.append(
                f"{name}: údaj z QR '{qf.value}' se liší od '{existing.value}' "
                f"- použit QR (jistější)."
            )
        setattr(inv, name, Field(qf.value, qf.confidence, SOURCE_QR, qf.raw))
        contributed = True
    if not contributed:
        return result
    # QR se povedl přečíst – zahoď matoucí „nepodařilo přečíst" hlášky.
    warnings = [w for w in warnings if "nepodařilo přečíst" not in w]
    base = result.method
    result.method = SOURCE_QR if (not base or base == SOURCE_QR) else f"{SOURCE_QR}+{base}"
    result.overall_confidence = overall_confidence(inv)
    result.warnings = warnings
    return result


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
    if name in ("issue_date", "taxable_date", "due_date"):
        return a == b
    return str(a).strip().upper() == str(b).strip().upper()


# Pole, u nichž umíme rozhodnout platnost tvrdým validátorem (checksum). Když
# se zdroje neshodnou a jen jedna hodnota projde, vyhraje ta platná.
_FIELD_VALIDATORS = {
    "supplier_ico": _V.valid_ico,
    "supplier_iban": _V.valid_iban,
}


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
                # Pole s tvrdým validátorem (IČO/IBAN checksum): když projde jen
                # jedna z hodnot, vyhraje PLATNÁ — neplatná hodnota (typicky
                # halucinace LLM) nikdy není správně, i kdyby ji dala „jistější"
                # cesta. Jen jedna z hodnot neplatná → vezmi tu druhou.
                validator = _FIELD_VALIDATORS.get(name)
                if validator is not None:
                    lf_ok, hf_ok = validator(str(lf.value)), validator(str(hf.value))
                    if hf_ok and not lf_ok:
                        setattr(result, name, Field(hf.value, max(0.4, hf.confidence), hf.source, hf.raw))
                        warnings.append(
                            f"{name}: hodnota z LLM '{lf.value}' neprošla kontrolou, "
                            f"použita platná '{hf.value}'."
                        )
                        continue
                    if lf_ok and not hf_ok:
                        setattr(result, name, Field(lf.value, max(0.4, lf.confidence), lf.source, lf.raw))
                        warnings.append(
                            f"{name}: hodnota z heuristiky '{hf.value}' neprošla kontrolou, "
                            f"použita platná '{lf.value}'."
                        )
                        continue
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
