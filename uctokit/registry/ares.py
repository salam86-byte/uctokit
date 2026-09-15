"""ARES – ověření IČO v registru ekonomických subjektů (frameworkově neutrální).

Veřejné REST API zdarma, bez autentizace. ``fetch`` jde vstříknout kvůli testům
bez sítě. Vrací dataclassu :class:`AresResult`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

ARES_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}"


@dataclass
class AresResult:
    found: bool
    ico: str = ""
    name: str = ""
    dic: str = ""
    address: str = ""
    # Adresa po částech – ARES ji v ``sidlo`` má strukturovaně, takže není
    # důvod rozebírat ``textovaAdresa`` řetězcem. Konzument je může nabídnout
    # do samostatných polí (ulice / obec / PSČ) místo jednoho slepence.
    street: str = ""
    city: str = ""
    postal_code: str = ""


def _street_from(sidlo: dict) -> str:
    """„Nádražní 994/20", u adres bez ulice „Vítkovice 3020".

    Malé obce a některé areály ulici nemají – ARES tam dává jen číslo domovní
    a název části obce, přesně jak to skládá i do ``textovaAdresa``."""
    cd = sidlo.get("cisloDomovni")
    if not cd:
        return ""

    cislo = str(cd)
    co = sidlo.get("cisloOrientacni")
    if co:
        cislo = f"{cislo}/{co}{sidlo.get('cisloOrientacniPismeno') or ''}"

    zacatek = sidlo.get("nazevUlice") or sidlo.get("nazevCastiObce") or ""
    return f"{zacatek} {cislo}".strip()


def _postal_code_from(sidlo: dict) -> str:
    """PSČ jak se píše na doklad: „792 01"."""
    psc = re.sub(r"\D", "", str(sidlo.get("psc") or ""))
    return f"{psc[:3]} {psc[3:]}" if len(psc) == 5 else psc


def _default_fetch(url: str):
    import requests

    resp = requests.get(url, timeout=15, headers={"Accept": "application/json"})
    return resp.status_code, resp.text


def lookup_ico(ico, *, fetch=None) -> AresResult:
    """Ověří IČO v ARES. ``fetch`` = callable(url) -> (status_code, text)."""
    digits = re.sub(r"\D", "", str(ico or ""))
    if len(digits) != 8:
        return AresResult(found=False)
    fetch = fetch or _default_fetch
    try:
        status, text = fetch(ARES_URL.format(ico=digits))
    except Exception:
        return AresResult(found=False, ico=digits)
    if status != 200:
        return AresResult(found=False, ico=digits)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return AresResult(found=False, ico=digits)
    sidlo = data.get("sidlo") or {}
    return AresResult(
        found=True,
        ico=str(data.get("ico") or digits),
        name=data.get("obchodniJmeno", "") or "",
        dic=data.get("dic", "") or "",
        address=sidlo.get("textovaAdresa", "") or "",
        street=_street_from(sidlo),
        # Obec, ne městský obvod: „Ostrava" (ne „Vítkovice"), „Praha"
        # (ne „Praha 4"). S PSČ je to vždycky doručitelné a nikdy to není
        # špatně – obvod si případně dopíše člověk.
        city=sidlo.get("nazevObce") or "",
        postal_code=_postal_code_from(sidlo),
    )
