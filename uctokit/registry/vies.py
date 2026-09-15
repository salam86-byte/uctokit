"""VIES – ověření DIČ (EU VAT ID) v evropském registru plátců DPH.

Veřejné REST API Evropské komise, zdarma a bez autentizace. Odpovídá na jednu
otázku: je tohle DIČ v den dotazu platné a komu patří? U dodavatele mimo ČR je
to jediná obdoba ARESu — IČO nemá, takže v českých rejstřících není k nalezení.

Zvlášť u zahraničního dokladu je odpověď užitečná i obsahem, ne jen platností:
polské DIČ může být registrované firmě úplně odjinud (a na faktuře je pak jiné
jméno, než komu DIČ patří) — přesně tohle je na přeprodejích vidět.

**`checked` a `valid` jsou dvě různé věci.** VIES dotaz jen přeposílá do
národního registru, a když je ten dole (`MS_UNAVAILABLE`, timeout), NESMÍ z
toho vzniknout tvrzení „neplatné DIČ". Nedostupnost je `checked=False`;
`valid=False` říká, že se registr ozval a DIČ nezná.

``fetch`` jde vstříknout kvůli testům bez sítě. Frameworkově neutrální.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{ms}/vat/{vat}"

# Daňové prefixy DIČ, ne ISO kódy zemí: Řecko má v DIČ „EL" (ISO je GR) a
# Severní Irsko po brexitu „XI" (zboží zůstalo v režimu EU).
EU_VAT_COUNTRIES = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR",
    "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
    "SE", "SI", "SK", "XI",
})

# Chybové kódy, které znamenají „zeptali jsme se a odpověď je NE" — všechno
# ostatní (MS_UNAVAILABLE, TIMEOUT, SERVICE_UNAVAILABLE, *_MAX_CONCURRENT_REQ)
# je nedostupnost registru, ne neplatné DIČ.
_ANSWERED = {"", "VALID", "INVALID", "INVALID_INPUT"}


@dataclass
class ViesResult:
    checked: bool           # podařilo se zeptat a odpověď dává smysl
    valid: bool = False     # DIČ je platné (jen když checked)
    country: str = ""       # daňový prefix („PL")
    number: str = ""        # část za prefixem („5263736824")
    name: str = ""          # majitel DIČ; "" = stát ho nezveřejňuje
    address: str = ""       # jednořádkově; "" = nezveřejněno
    error: str = ""         # proč se nedalo ověřit (kód z VIES / HTTP …)

    @property
    def vat_id(self) -> str:
        return f"{self.country}{self.number}" if self.country else ""


def split_vat(vat_id) -> tuple[str, str]:
    """„PL 526-373-68-24" → ``("PL", "5263736824")``. ``("", "")`` = není EU DIČ.

    Bez prefixu země se hádat nedá (samotné číslo vypadá v každé zemi jinak),
    takže takový zápis projde jako „nevím" — ne jako chyba.
    """
    raw = re.sub(r"[^A-Za-z0-9]", "", str(vat_id or "")).upper()
    if len(raw) < 3:
        return "", ""
    code = raw[:2]
    if code == "GR":           # ISO kód Řecka; VIES chce daňový prefix
        code = "EL"
    if code not in EU_VAT_COUNTRIES:
        return "", ""
    return code, raw[2:]


def _clean(value) -> str:
    """Hodnota z VIES jednořádkově. „---" = stát údaj nezveřejňuje → ""."""
    text = str(value or "").strip()
    if not text or set(text) <= {"-"}:
        return ""
    radky = [re.sub(r"\s+", " ", r).strip() for r in text.splitlines()]
    return ", ".join(r for r in radky if r)


def _default_fetch(url: str):
    import requests

    resp = requests.get(url, timeout=20, headers={"Accept": "application/json"})
    return resp.status_code, resp.text


def lookup_vat(vat_id, *, fetch=None) -> ViesResult:
    """Ověří DIČ ve VIES. ``fetch`` = callable(url) -> (status_code, text)."""
    country, number = split_vat(vat_id)
    if not country or not number:
        return ViesResult(checked=False, error="NOT_EU_VAT")
    fetch = fetch or _default_fetch
    zaklad = {"country": country, "number": number}
    try:
        status, text = fetch(VIES_URL.format(ms=country, vat=number))
    except Exception:
        return ViesResult(checked=False, error="NETWORK", **zaklad)
    if status != 200:
        return ViesResult(checked=False, error=f"HTTP {status}", **zaklad)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return ViesResult(checked=False, error="BAD_RESPONSE", **zaklad)
    if not isinstance(data, dict):
        return ViesResult(checked=False, error="BAD_RESPONSE", **zaklad)

    # `isValid` je REST podoba, `valid` starší SOAP — brát obojí.
    valid = bool(data.get("isValid", data.get("valid", False)))
    chyba = str(data.get("userError") or "").upper()
    if not valid and chyba not in _ANSWERED:
        return ViesResult(checked=False, error=chyba, **zaklad)
    return ViesResult(
        checked=True, valid=valid,
        name=_clean(data.get("name")), address=_clean(data.get("address")),
        **zaklad,
    )
