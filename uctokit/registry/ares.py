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
    )
