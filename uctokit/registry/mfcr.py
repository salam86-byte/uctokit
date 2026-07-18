"""MFČR – registr plátců DPH: nespolehlivý plátce + zveřejněné účty.

SOAP služba Finanční správy (operace getStatusNespolehlivyPlatce). Právní smysl:
platba na NEzveřejněný účet nespolehlivého plátce = ručení za jeho DPH. Proto
ověřujeme, jestli je dodavatel nespolehlivý a jestli je účet z faktury mezi
zveřejněnými.

``fetch`` jde vstříknout kvůli testům bez sítě. Frameworkově neutrální.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

MFCR_URL = "https://adisrws.mfcr.cz/adistc/axis2/services/rozhraniCRPDPH.rozhraniCRPDPHSOAP"
_NS = "http://adis.mfcr.cz/rozhraniCRPDPH/"


@dataclass
class VatResult:
    found: bool                       # DIČ nalezeno v registru plátců DPH
    unreliable: bool = False          # nespolehlivý plátce (ANO)
    accounts: list = field(default_factory=list)  # zveřejněné účty (kanonicky)


def _build_request(dic_digits: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:urn="{_NS}">'
        "<soapenv:Body>"
        "<urn:StatusNespolehlivyPlatceRequest>"
        f"<urn:dic>{dic_digits}</urn:dic>"
        "</urn:StatusNespolehlivyPlatceRequest>"
        "</soapenv:Body></soapenv:Envelope>"
    )


def _default_fetch(body: str):
    import requests

    resp = requests.post(
        MFCR_URL,
        data=body.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": ""},
        timeout=20,
    )
    return resp.status_code, resp.text


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def check_unreliable_vat(dic, *, fetch=None) -> VatResult:
    """Ověří spolehlivost plátce a jeho zveřejněné účty. ``fetch`` = callable(body)->(status,text)."""
    digits = re.sub(r"\D", "", str(dic or ""))  # CZ12345679 -> 12345679
    if not digits:
        return VatResult(found=False)
    fetch = fetch or _default_fetch
    try:
        status, text = fetch(_build_request(digits))
    except Exception:
        return VatResult(found=False)
    if status != 200 or not text:
        return VatResult(found=False)
    return _parse_response(text)


def _parse_response(text: str) -> VatResult:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return VatResult(found=False)

    status_el = next((el for el in root.iter() if _localname(el.tag) == "statusPlatceDPH"), None)
    if status_el is None:
        return VatResult(found=False)
    flag = (status_el.get("nespolehlivyPlatce") or "").upper()
    if flag == "NENALEZEN":
        return VatResult(found=False)

    accounts = []
    for el in root.iter():
        name = _localname(el.tag)
        if name == "standardniUcet":
            prefix = (el.get("predcisli") or "").lstrip("0")
            number = (el.get("cislo") or "").lstrip("0")
            bank = el.get("kodBanky") or ""
            if number and bank:
                accounts.append(f"{prefix}-{number}/{bank}" if prefix else f"{number}/{bank}")
        elif name == "nestandardniUcet":
            iban = (el.get("iban") or el.get("cislo") or "").replace(" ", "").upper()
            if iban:
                accounts.append(iban)
    return VatResult(found=True, unreliable=(flag == "ANO"), accounts=accounts)


# --- Porovnání účtů ----------------------------------------------------------

def normalize_account(key: str) -> str:
    """Kanonizuje 'předčíslí-číslo/kód' nebo IBAN pro porovnání (bez vodicích nul)."""
    key = re.sub(r"\s", "", key or "").upper()
    if re.fullmatch(r"[A-Z]{2}\d+", key):  # IBAN
        return key
    m = re.match(r"(?:(\d+)-)?(\d+)/(\d+)$", key)
    if not m:
        return key
    prefix = (m.group(1) or "").lstrip("0")
    number = m.group(2).lstrip("0")
    bank = m.group(3)
    return f"{prefix}-{number}/{bank}" if prefix else f"{number}/{bank}"


def account_is_published(account_key: str, published: list) -> bool:
    """Je účet (kanonicky) mezi zveřejněnými?"""
    if not account_key:
        return False
    target = normalize_account(account_key)
    return any(normalize_account(a) == target for a in published)
