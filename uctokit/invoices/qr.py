"""Čtení QR Platby / QR Faktury (formát SPAYD / SID) z PDF a obrázků.

Deterministický zdroj platebních údajů (účet/IBAN, částka, VS, měna) a u QR
Faktury i hlavičky (číslo faktury, data, IČO dodavatele). V žebříčku extrakce
stojí hned za ISDOCem: když QR pokryje platební pole, není potřeba AI ani OCR.

Vše je frameworkově neutrální. Renderer (pypdfium2) i dekodér QR (zxing-cpp /
OpenCV / pyzbar) se importují **líně**; když chybí, funkce vrátí prázdno a
pipeline spadne zpět na text/AI cestu – žádná tvrdá závislost, nic se nerozbije.

SPAYD (QR Platba):  ``SPD*1.0*ACC:CZ..+BIC*AM:450.00*CC:CZK*X-VS:123*DT:20260131``
QR Faktura (SID):   ``SID*1.0*ID:2026001*DD:20260101*AM:450.00*INI:12345679*...``
Oba deskriptory mohou být i spojené v jednom kódu; parsujeme sjednocení klíčů.
"""

from __future__ import annotations

import io
import re
from datetime import date
from urllib.parse import unquote

from . import validators as V
from .types import SOURCE_QR, ExtractedInvoice, Field, SourceDocument

# Kolik prvních stran PDF prohledat na QR (kód bývá na první, občas poslední).
_MAX_PAGES = 3
# Měřítko renderu PDF→obrázek (≈216 DPI), ať je QR čitelný pro dekodér.
_RENDER_SCALE = 3.0


def extract_from_qr(document: SourceDocument) -> ExtractedInvoice | None:
    """Přečte QR kódy z dokladu a vrátí vytěžená pole, nebo ``None``.

    Vrací první QR, který se podaří rozparsovat na aspoň jedno platné pole.
    """
    for payload in read_qr_payloads(document):
        inv = parse_spayd(payload)
        if inv is not None and any(f.is_present for _, f in inv.items()):
            return inv
    return None


# --- Parser SPAYD / SID (čistý, plně testovatelný) --------------------------

def parse_spayd(payload: str) -> ExtractedInvoice | None:
    """Rozparsuje řetězec SPAYD/SID na :class:`ExtractedInvoice`.

    Nezná-li payload hlavičku ``SPD*``/``SID*``, vrátí ``None``.
    """
    text = (payload or "").strip()
    if not (text[:4].upper() in ("SPD*", "SID*")):
        return None

    outer = _parse_tokens(text)
    data = dict(outer)
    # QR Platba + F: hlavička faktury (SID) jede uvnitř platby jako hodnota
    # klíče `X-INV`, a protože by se jinak tloukla s oddělovačem SPAYD, je
    # v ní hvězdička zakódovaná jako `%2A`. Bez rozbalení z kódu zbyla jen
    # platba — číslo faktury, datum vystavení, IČO i DUZP se zahodily
    # (FakturaOnline, říjen 2026: sken bez textové vrstvy, kde je QR
    # jediný strojově čitelný zdroj). Vnější platba má přednost
    # (`setdefault`), SID jen doplňuje, co v ní není.
    inner = outer.get("X-INV")
    if inner:
        # Rozbalit jen hvězdičky — hodnoty uvnitř se dekódují níž spolu se
        # vším ostatním, ať se nic nedekóduje dvakrát.
        for key, val in _parse_tokens(re.sub(r"%2A", "*", inner, flags=re.I)).items():
            data.setdefault(key, val)

    inv = _build(_decoded(data))
    # Která pole dala JEN vnořená hlavička. Ta údaje DOPLŇUJE, řízení
    # vytěžení nemění: hodnotu z textu nepřebije a AI se kvůli ní
    # nevynechává (`pipeline._apply_qr`, `_qr_covers_invoice`) — dokud se
    # nečetla, běžel u takových dokladů model vždy a bral se text. Příklad,
    # proč na tom záleží: betonárna má na dokladu „Číslo: 2026000123",
    # v kódu interní „VF12-262026000123".
    vnejsi = _build(_decoded(outer)) if inner else inv
    inv.qr_nested_fields = frozenset(
        name for name, f in inv.items()
        if f.is_present and not getattr(vnejsi, name).is_present)
    return inv


def _decoded(data: dict[str, str]) -> dict[str, str]:
    """Hodnoty smí být URL-kódované (UTF-8): jméno příjemce od živnostníka
    přišlo jako „Ji%C5%99%C3%AD …" a tak se i uložilo. `unquote` nechává neplatné sekvence („sleva 10%") být."""
    return {key: unquote(val) for key, val in data.items()}


def _build(data: dict[str, str]) -> ExtractedInvoice:
    """Pole faktury z rozparsovaných klíčů SPAYD/SID."""
    inv = ExtractedInvoice()

    acc = data.get("ACC")  # IBAN, případně "IBAN+BIC"
    if acc:
        iban = V.normalize_iban(acc.split("+")[0])
        if iban:
            ok = V.valid_iban(iban)
            inv.supplier_iban = Field(iban, 0.97 if ok else 0.55, SOURCE_QR, acc)
            domestic = _iban_to_domestic(iban) if ok else None
            if domestic:
                inv.supplier_account = Field(domestic, 0.95, SOURCE_QR, iban)

    amount = V.normalize_amount(data.get("AM"))
    if amount is not None:
        inv.total_amount = Field(amount, 0.95, SOURCE_QR, data.get("AM"))

    cc = data.get("CC")
    if cc:
        inv.currency = Field(cc.upper(), 0.9, SOURCE_QR, cc)

    vs = V.normalize_vs(data.get("X-VS") or data.get("VS") or "")
    if vs:
        inv.variable_symbol = Field(vs, 0.95, SOURCE_QR, data.get("X-VS") or data.get("VS"))

    due = _parse_date(data.get("DT"))
    if due:
        inv.due_date = Field(due, 0.9, SOURCE_QR, data.get("DT"))

    issued = _parse_date(data.get("DD"))
    if issued:
        inv.issue_date = Field(issued, 0.9, SOURCE_QR, data.get("DD"))

    # QR Faktura veze DUZP přímo (klíč `DUZP`); `DPPD` je datum povinnosti
    # přiznat daň, což u přijaté faktury vychází nastejno a bereme ho jako
    # náhradu, když samotné DUZP v kódu není.
    taxable = _parse_date(data.get("DUZP") or data.get("DPPD"))
    if taxable:
        inv.taxable_date = Field(taxable, 0.9, SOURCE_QR,
                                 data.get("DUZP") or data.get("DPPD"))

    number = data.get("ID")
    if number:
        inv.invoice_number = Field(number, 0.9, SOURCE_QR, number)

    # Jen `INI`/`VII` = dodavatel. `INR`/`VIR` jsou IČO a DIČ ODBĚRATELE,
    # tedy naše — v kódu bývají obě dvojice vedle sebe.
    ico = V.normalize_ico(data.get("INI") or "")
    if ico:
        inv.supplier_ico = Field(ico, 0.95 if V.valid_ico(ico) else 0.55, SOURCE_QR, ico)

    dic = V.normalize_dic(data.get("VII") or "")
    if dic:
        inv.supplier_dic = Field(dic, 0.9, SOURCE_QR, data.get("VII"))

    name = data.get("RN")  # jméno příjemce platby = dodavatel
    if name:
        inv.supplier_name = Field(name, 0.85, SOURCE_QR, name)

    return inv


def _parse_tokens(text: str) -> dict[str, str]:
    """`KLÍČ:hodnota` páry oddělené hvězdičkou; první výskyt klíče vyhrává."""
    data: dict[str, str] = {}
    for token in (text or "").split("*"):
        key, sep, val = token.partition(":")
        if not sep:  # hlavička deskriptoru (SPD/SID) nebo verze (1.0) – bez ":"
            continue
        key, val = key.strip().upper(), val.strip()
        if key and val:
            data.setdefault(key, val)  # první výskyt vyhrává (SPD před SID)
    return data


def _iban_to_domestic(iban: str) -> str | None:
    """Český IBAN → tuzemský tvar ``[předčíslí-]číslo/kód`` (jinak ``None``).

    CZ IBAN (24 znaků): CZkk BBBB PPPPPP AAAAAAAAAA – banka, předčíslí, účet.
    """
    iban = V.normalize_iban(iban)
    if len(iban) != 24 or not iban.startswith("CZ") or not iban[2:].isdigit():
        return None
    bank = iban[4:8]
    prefix = iban[8:14].lstrip("0")
    number = iban[14:24].lstrip("0") or "0"
    account = f"{prefix}-{number}" if prefix else number
    return f"{account}/{bank}"


def _parse_date(raw) -> date | None:
    """SPAYD/SID datum ``YYYYMMDD`` (jinak zkusí obecný normalizátor)."""
    s = (raw or "").strip()
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return V.normalize_date(s) if s else None


# --- Načtení QR z dokladu (líné závislosti) ---------------------------------

def read_qr_payloads(document: SourceDocument) -> list[str]:
    """Vrátí seznam dekódovaných QR řetězců z dokladu (prázdný při chybě/absenci)."""
    images = _document_images(document)
    if not images:
        return []
    decode = _get_decoder()
    if decode is None:
        return []
    payloads: list[str] = []
    for img in images:
        try:
            payloads.extend(decode(img))
        except Exception:
            continue
    return payloads


def _document_images(document: SourceDocument) -> list:
    """Vyrenderuje doklad na PIL obrázky (PDF přes pypdfium2, obrázek přímo)."""
    content = document.content or b""
    if document.looks_like_pdf:
        return _render_pdf(content)
    if document.looks_like_image:
        try:
            from PIL import Image

            return [Image.open(io.BytesIO(content)).convert("RGB")]
        except Exception:
            return []
    return []


def _render_pdf(content: bytes) -> list:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return []
    images: list = []
    pdf = None
    try:
        pdf = pdfium.PdfDocument(content)
        for i in range(min(len(pdf), _MAX_PAGES)):
            bitmap = pdf[i].render(scale=_RENDER_SCALE)
            images.append(bitmap.to_pil().convert("RGB"))
    except Exception:
        return []
    finally:
        try:
            if pdf is not None:
                pdf.close()
        except Exception:
            pass
    return images


def _get_decoder():
    """Vrátí ``callable(PIL.Image) -> list[str]`` podle dostupné knihovny, nebo ``None``."""
    try:
        import zxingcpp

        def decode(img):
            return [r.text for r in zxingcpp.read_barcodes(img) if r.text]

        return decode
    except Exception:
        pass

    try:
        import cv2
        import numpy as np

        detector = cv2.QRCodeDetector()

        def decode(img):
            arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            ok, texts, _pts, _ = detector.detectAndDecodeMulti(arr)
            if ok:
                return [t for t in texts if t]
            text, _pts2, _ = detector.detectAndDecode(arr)
            return [text] if text else []

        return decode
    except Exception:
        pass

    try:
        from pyzbar.pyzbar import decode as zbar_decode

        def decode(img):
            return [d.data.decode("utf-8", "ignore") for d in zbar_decode(img)]

        return decode
    except Exception:
        pass

    return None
