"""Volitelné OCR (pytesseract) pro naskenované doklady bez textové vrstvy.

Importy jsou líné – když tesseract/pdf2image nejsou, vrátí se prázdný text a
pipeline spadne na jinou cestu.
"""

from __future__ import annotations

import io


def ocr_pdf(content: bytes, *, lang: str = "ces") -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        return ""
    try:
        images = convert_from_bytes(content)
        return "\n".join(pytesseract.image_to_string(img, lang=lang) for img in images)
    except Exception:
        return ""


def ocr_image(content: bytes, *, lang: str = "ces") -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        image = Image.open(io.BytesIO(content))
        return pytesseract.image_to_string(image, lang=lang)
    except Exception:
        return ""
