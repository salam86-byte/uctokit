"""Textová vrstva PDF přes pdfplumber a render stránek na obrázky pro vision.

pdfplumber i pdf2image jsou frameworkově neutrální knihovny; importují se líně,
aby se instalace dala udělat volitelnou a jádro šlo použít i bez nich.
"""

from __future__ import annotations

import io


def extract_text(content: bytes) -> str:
    """Vrátí textovou vrstvu PDF, nebo prázdný řetězec (sken / bez textu)."""
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return ""


def is_scanned(text: str, *, min_chars: int = 40) -> bool:
    """Heuristika: pod prahem smysluplného textu jde nejspíš o sken."""
    return len((text or "").strip()) < min_chars


def render_page_images(content: bytes, *, max_pages: int = 3, dpi: int = 200) -> list[bytes]:
    """Vyrenderuje prvních ``max_pages`` stránek PDF na PNG bajty (pro vision LLM).

    Prázdný seznam, když render není k dispozici (chybí pdf2image/poppler).
    """
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        return []
    try:
        images = convert_from_bytes(content, dpi=dpi, first_page=1, last_page=max_pages)
    except Exception:
        return []
    out: list[bytes] = []
    for img in images:
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        out.append(buffer.getvalue())
    return out
