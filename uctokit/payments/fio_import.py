"""Sestavení a čtení Fio importu platebních příkazů (frameworkově neutrální).

Generuje Fio XML (tuzemský příkaz, `type=xml`, schéma importIB.xsd) a parsuje
odpověď serveru. Čistý Python – samotné HTTP odeslání dělá Django vrstva.

Pozor: nahraná dávka se v bance **nezpracuje bez dodatečné autorizace** (SMS /
Fio podpis) oprávněnou osobou. Tenhle modul jen skládá data, nic neposílá.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

SCHEMA_LOCATION = "http://www.fio.cz/schema/importIB.xsd"
PAYMENT_TYPE_STANDARD = "431001"


@dataclass
class DomesticOrder:
    """Jeden tuzemský platební příkaz (CZK, v rámci ČR)."""

    account_from: str          # číslo účtu příkazce (16n) – daný Fio tokenem
    account_to: str            # číslo účtu příjemce (6–10n, příp. s předčíslím)
    bank_code: str             # kód banky příjemce (4!n)
    amount: Decimal
    date: date                 # datum splatnosti
    currency: str = "CZK"
    vs: str = ""
    ks: str = ""
    ss: str = ""
    message: str = ""          # zpráva pro příjemce (140)
    comment: str = ""          # interní označení (255)
    payment_type: str = PAYMENT_TYPE_STANDARD


def _el(tag: str, value) -> str:
    if value in (None, ""):
        return ""
    return f"<{tag}>{escape(str(value))}</{tag}>"


def _format_amount(amount) -> str:
    return f"{Decimal(amount):.2f}"


def _format_date(value) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def build_import_xml(orders: list[DomesticOrder]) -> str:
    """Sestaví Fio XML pro dávku tuzemských příkazů."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Import xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xsi:noNamespaceSchemaLocation="{SCHEMA_LOCATION}">',
        "<Orders>",
    ]
    for order in orders:
        lines.append("<DomesticTransaction>")
        lines.append(_el("accountFrom", order.account_from))
        lines.append(_el("currency", order.currency or "CZK"))
        lines.append(_el("amount", _format_amount(order.amount)))
        lines.append(_el("accountTo", order.account_to))
        lines.append(_el("bankCode", order.bank_code))
        lines.append(_el("ks", order.ks))
        lines.append(_el("vs", order.vs))
        lines.append(_el("ss", order.ss))
        lines.append(_el("date", _format_date(order.date)))
        lines.append(_el("messageForRecipient", order.message))
        lines.append(_el("comment", order.comment))
        lines.append(_el("paymentType", order.payment_type or PAYMENT_TYPE_STANDARD))
        lines.append("</DomesticTransaction>")
    lines.append("</Orders>")
    lines.append("</Import>")
    return "\n".join(line for line in lines if line)


# --- IBAN → tuzemský účet (CZ) ----------------------------------------------

def iban_to_domestic(iban: str) -> tuple[str, str] | None:
    """Rozloží český IBAN na (číslo účtu, kód banky). ``None`` když to není CZ IBAN.

    CZ IBAN (24 znaků): CZkk BBBB PPPPPP AAAAAAAAAA – banka, předčíslí, účet.
    """
    iban = re.sub(r"\s", "", (iban or "")).upper()
    if not re.fullmatch(r"CZ\d{22}", iban):
        return None
    bank_code = iban[4:8]
    prefix = iban[8:14].lstrip("0")
    account = iban[14:24].lstrip("0")
    number = f"{prefix}-{account}" if prefix else account
    return number, bank_code


def domestic_to_iban(number: str, bank_code: str = "") -> str:
    """Složí z tuzemského účtu český IBAN. Prázdný řetězec, když to nejde.

    Opak :func:`iban_to_domestic`. Bere „19-1111111111/0800", „1111111111/0800"
    i dvojici (číslo, kód banky). IBAN je potřeba všude, kde tuzemský formát
    nestačí – hlavně **QR platba (SPAYD)**, která jiný než IBAN nezná.

    Kontrolní číslice se počítá dle ISO 13616 (mod 97): přeskládá se
    ``BBBBPPPPPPAAAAAAAAAA`` + ``CZ00`` na číslo (C→12, Z→35) a doplní se
    ``98 - (n mod 97)``. Vstup se **nedopočítává ani nehádá** – když číslo
    účtu nedává smysl (příliš dlouhé, nečíselné, bez kódu banky), vrací se
    prázdno a volající ať radši nenabídne nic než špatný účet.
    """
    raw = re.sub(r"\s", "", str(number or ""))
    code = re.sub(r"\s", "", str(bank_code or ""))
    if "/" in raw:
        raw, _, tail = raw.partition("/")
        code = code or tail
    if not re.fullmatch(r"\d{4}", code):
        return ""
    prefix, _, account = raw.rpartition("-")
    if not re.fullmatch(r"\d{1,6}", prefix or "0"):
        return ""
    if not re.fullmatch(r"\d{1,10}", account or ""):
        return ""
    body = f"{code}{(prefix or '0'):0>6}{account:0>10}"
    # ISO 13616: tělo + „CZ00" (C=12, Z=35), zbytek po dělení 97.
    numeric = int(body + "123500")
    check = 98 - (numeric % 97)
    return f"CZ{check:02d}{body}"


# --- Odpověď serveru ---------------------------------------------------------

@dataclass
class ImportResult:
    status: str                # ok / warning / error / fatal / ""
    error_code: int | None
    instruction_id: str        # číslo dávky (idInstruction)
    sum_debet: str
    messages: list[str]
    raw: str

    @property
    def accepted(self) -> bool:
        """Dávka přijata bankou (čeká na autorizaci)? ok nebo warning."""
        return self.status in ("ok", "warning")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_import_response(xml_text: str) -> ImportResult:
    """Rozparsuje XML odpověď Fio importu (defenzivně, dle localname)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ImportResult("fatal", None, "", "", ["Neplatná odpověď serveru."], xml_text or "")

    def first(name: str) -> str:
        for el in root.iter():
            if _localname(el.tag) == name and el.text and el.text.strip():
                return el.text.strip()
        return ""

    messages = [
        el.text.strip()
        for el in root.iter()
        if _localname(el.tag) == "message" and el.text and el.text.strip()
    ]
    error_code = first("errorCode")
    return ImportResult(
        status=first("status"),
        error_code=int(error_code) if error_code.isdigit() else None,
        instruction_id=first("idInstruction"),
        sum_debet=first("sumDebet"),
        messages=messages,
        raw=xml_text or "",
    )
