"""Zápočet pohledávek a závazků (§ 1982 obč. zák.) – čtení tiskové sestavy.

Zápočet **není doklad k zaplacení**: je to oznámení, že se proti sobě
postavily dvě pohledávky a zaplatit se má jen rozdíl. Kdo ho přehlédne,
zaplatí fakturu celou – tedy i tu část, kterou už pokryla jeho vlastní
vydaná faktura.

Typický tvar (ABRA Flexi, „Jednostranný zápočet pohledávek a závazků")::

    Jednostranný zápočet pohledávek a závazků
    Číslo zápočtu: 2026104319
    ...
    závazek firmy Dodavatel s.r.o. vůči firmě Odběratel s.r.o.:
    Doklad        Vystaveno   Splatnost   VS         Měna Fakturováno Započteno Zbývá uhradit
    VF-00120/2026 07.09.2026  21.09.2026  1117626081 CZK    25 871,54 25 871,54          0,00
    Celkem započteno: 25 871,54
    pohledávka firmy Dodavatel s.r.o. za firmou Odběratel s.r.o.:
    Doklad        Vystaveno   Splatnost   VS         Měna Fakturováno Započteno Zbývá uhradit
    2026104319    07.09.2026  21.09.2026  2026104319 CZK   134 775,43 25 871,54    108 903,89

Strany se poznají podle NADPISU sekce, ne podle pořadí – dohoda o vzájemném
zápočtu je může mít obráceně:

* ``závazek firmy X vůči firmě Y``   → doklady, které dluží vystavitel,
  tedy faktury vystavené protistranou → :attr:`OffsetStatement.counterparty_claims`;
* ``pohledávka firmy X za firmou Y`` → faktury vystavitele → :attr:`OffsetStatement.issuer_claims`.

Nerozpoznanou sekci parser **zahodí**. Přiřadit řádek ke špatné straně
znamená odečíst částku od špatného dokladu, a to je horší než nevědět nic.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from . import validators as V

__all__ = [
    "OffsetLine",
    "OffsetStatement",
    "looks_like_offset",
    "parse_offset_statement",
]


# --- rozpoznání dokumentu ----------------------------------------------------

# Obraty, kterými se zápočet představuje. Hledá se bez diakritiky a malými
# písmeny, ať to přežije OCR i tisk s velkými písmeny v nadpisu.
_OFFSET_MARKERS = (
    "zapocet pohledavek",
    "zapoctu pohledavek",
    "jednostranny zapocet",
    "vzajemny zapocet",
    "vzajemnem zapoctu",
    "dohoda o zapoctu",
)


def _fold(text: str) -> str:
    """Malá písmena bez diakritiky, **znak za znak**.

    Skládá se po jednotlivých znacích schválně: výsledek má pak stejnou
    délku jako vstup, takže se pozice nalezené ve složeném textu dají
    použít na text původní. Díky tomu se hledá bez ohledu na diakritiku,
    ale názvy firem a čísla dokladů se vracejí tak, jak jsou na papíře
    (jinak by ze ``SB-03207/2026`` bylo ``sb-03207/2026``).
    """
    out = []
    for ch in text or "":
        base = "".join(
            c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)
        )
        lowered = (base[:1] or ch).lower()
        out.append(lowered[:1] or ch)
    return "".join(out)


def looks_like_offset(text: str) -> bool:
    """Vypadá text jako zápočet? Levá branka před :func:`parse_offset_statement`."""
    folded = _fold(text)
    return any(m in folded for m in _OFFSET_MARKERS)


# --- řádky tabulky -----------------------------------------------------------

_DATE = r"\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4}"
# Mezera v tisícech může být obyčejná, nezlomitelná i úzká – ABRA i OCR
# střídají všechny tři.
_SPACE = r"[   ]"
_AMOUNT = (
    rf"-?\d{{1,3}}(?:{_SPACE}\d{{3}})+(?:,\d+)?"   # 25 871,54
    r"|-?\d{1,3}(?:\.\d{3})+(?:,\d+)?"             # 25.871,54
    r"|-?\d+(?:[.,]\d+)?"                          # 871,54 i 871.54
)
_CURRENCY = r"\b[A-Z]{3}\b"

# Kolik slov smí být v označení dokladu. „FV 2026001" se vyskytuje, delší
# text před datem už znamená, že to není řádek tabulky.
_MAX_DOC_WORDS = 3


@dataclass(frozen=True)
class OffsetLine:
    """Jeden doklad v tabulce zápočtu.

    ``invoiced`` je částka dokladu, ``offset`` kolik se z ní započetlo a
    ``remaining`` co zbývá uhradit. U započtené faktury je ``remaining``
    to jediné, co se má poslat do banky.
    """

    document: str
    issued: date | None = None
    due: date | None = None
    vs: str = ""
    currency: str = ""
    invoiced: Decimal | None = None
    offset: Decimal | None = None
    remaining: Decimal | None = None

    @property
    def fully_offset(self) -> bool:
        """Pokryl zápočet celý doklad? (pak se neplatí nic)"""
        return self.remaining is not None and self.remaining == 0


def _amounts(text: str) -> list[Decimal | None]:
    return [V.normalize_amount(m.group()) for m in re.finditer(_AMOUNT, text)]


def _parse_row(line: str) -> OffsetLine | None:
    """Řádek tabulky → :class:`OffsetLine`. ``None`` = tohle není řádek.

    Čte se od kotev, ne podle pozic sloupců: nejdřív data (ta jsou
    jednoznačná), pak měna, a částky až za ní. Kdyby se sloupce četly zleva
    napevno, rozhodilo by je jediné chybějící pole – a variabilní symbol
    (desetimístné číslo) by se dal snadno splést s částkou.
    """
    dates = list(re.finditer(_DATE, line))
    if not dates:
        return None

    document = line[: dates[0].start()].strip(" \t|")
    if not document or len(document.split()) > _MAX_DOC_WORDS:
        return None
    # Souhrnné řádky („Celkem započteno") mají za textem rovnou částku, ne
    # datum – sem se tedy nedostanou. Tohle chytá zbytek: řádek, kde je
    # „doklad" jen interpunkce.
    if not re.search(r"[0-9A-Za-z]", document):
        return None

    rest = line[dates[-1].end():]
    currency_match = re.search(_CURRENCY, rest)
    if currency_match:
        head, tail = rest[: currency_match.start()], rest[currency_match.end():]
        currency = currency_match.group()
    else:
        head, tail, currency = "", rest, ""

    values = _amounts(tail)
    if len(values) < 2:
        return None
    if len(values) > 3:
        # Víc čísel než sloupců: bereme poslední tři, protože tabulka končí
        # částkami. Cokoli dřív je zbytek sousedního sloupce.
        values = values[-3:]

    invoiced, offset = values[0], values[1]
    if len(values) >= 3:
        remaining = values[2]
    elif invoiced is not None and offset is not None:
        remaining = invoiced - offset
    else:
        remaining = None

    vs_source = head if currency else ""
    vs_match = re.search(r"\d{2,10}", vs_source)

    return OffsetLine(
        document=document,
        issued=V.normalize_date(dates[0].group()),
        due=V.normalize_date(dates[1].group()) if len(dates) > 1 else None,
        vs=vs_match.group() if vs_match else "",
        currency=currency,
        invoiced=invoiced,
        offset=offset,
        remaining=remaining,
    )


# --- hlavičky sekcí ----------------------------------------------------------

# „závazek firmy X vůči firmě Y" – vystavitel dluží protistraně, takže
# následující doklady vystavila PROTISTRANA.
_LIABILITY_HEADER = re.compile(
    r"zavaz\w*\s+firmy\s+(?P<issuer>.+?)\s+vuci\s+firm\w+\s+(?P<other>.+?)\s*:?\s*$"
)
# „pohledávka firmy X za firmou Y" – doklady vystavil VYSTAVITEL.
_CLAIM_HEADER = re.compile(
    r"pohledavk\w*\s+firmy\s+(?P<issuer>.+?)\s+za\s+firm\w+\s+(?P<other>.+?)\s*:?\s*$"
)

_ISSUER, _COUNTERPARTY = "issuer", "counterparty"


@dataclass(frozen=True)
class OffsetStatement:
    """Rozparsovaný zápočet.

    ``issuer_claims`` jsou faktury vystavitele (pro příjemce zápočtu jde
    o **přijaté** faktury, kterým zápočet snižuje úhradu), ``counterparty_claims``
    faktury protistrany (tedy naše vydané, které zápočet spotřeboval).
    """

    number: str = ""
    date: date | None = None
    issuer_name: str = ""
    counterparty_name: str = ""
    issuer_ico: str = ""
    counterparty_ico: str = ""
    issuer_claims: tuple[OffsetLine, ...] = ()
    counterparty_claims: tuple[OffsetLine, ...] = ()
    total_offset: Decimal | None = None

    @property
    def lines(self) -> tuple[OffsetLine, ...]:
        return self.issuer_claims + self.counterparty_claims

    def line_for(self, number: str) -> OffsetLine | None:
        """Řádek pro doklad daného čísla (hledá i podle variabilního symbolu).

        Porovnává se přes „klíč" bez oddělovačů: ``SB-03207/2026`` a
        ``SB 03207/2026`` je pro účetní tentýž doklad a rozdíl v zápisu
        nesmí rozhodovat o tom, jestli se zápočet uplatní.
        """
        wanted = _key(number)
        if not wanted:
            return None
        for line in self.lines:
            if _key(line.document) == wanted or _key(line.vs) == wanted:
                return line
        # Až jako druhá vlna: jen číslice. Doklad „SB-03207/2026" a symbol
        # „032072026" se takhle potkají, ale nechceme, aby slabší shoda
        # přebila přesnou.
        digits = _digits(number)
        if not digits:
            return None
        for line in self.lines:
            if _digits(line.document) == digits or _digits(line.vs) == digits:
                return line
        return None

    def remaining_for(self, number: str) -> Decimal | None:
        """Kolik po zápočtu zbývá uhradit na daném dokladu."""
        line = self.line_for(number)
        return line.remaining if line else None

    def offset_for(self, number: str) -> Decimal | None:
        """Kolik se na daném dokladu započetlo."""
        line = self.line_for(number)
        return line.offset if line else None


def _key(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value or "").upper()


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _icos(text: str) -> list[str]:
    """Platná IČO v pořadí výskytu, bez opakování."""
    found: list[str] = []
    for match in re.finditer(r"\b\d{8}\b", text or ""):
        value = match.group()
        if value not in found and V.valid_ico(value):
            found.append(value)
    return found


def parse_offset_statement(text: str) -> OffsetStatement | None:
    """Text zápočtu → :class:`OffsetStatement`. ``None`` = nedá se přečíst.

    Vrací ``None`` i tehdy, když se v dokumentu nenajde ani jedna
    rozpoznaná sekce: zápočet bez stran je k odečítání částek nepoužitelný
    a tvářit se, že jsme něco přečetli, by bylo horší než přiznat, že ne.
    """
    if not looks_like_offset(text):
        return None

    folded_text = _fold(text)

    number = ""
    match = re.search(r"cislo\s+zapoctu\s*:?\s*(\S+)", folded_text)
    if match:
        # Vyříznuto z PŮVODNÍHO textu podle pozice ve složeném – `_fold`
        # nemění délku, takže číslo si zachová velká písmena.
        number = text[match.start(1):match.end(1)].strip(".,;")

    statement_date = None
    date_match = re.search(rf"(?:ke dni|dne)\s+({_DATE})", folded_text)
    if date_match:
        statement_date = V.normalize_date(date_match.group(1))

    issuer_name = counterparty_name = ""
    issuer_rows: list[OffsetLine] = []
    counterparty_rows: list[OffsetLine] = []
    side: str | None = None

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        folded = _fold(line)
        header = _CLAIM_HEADER.search(folded)
        if header:
            side = _ISSUER
        else:
            header = _LIABILITY_HEADER.search(folded)
            if header:
                side = _COUNTERPARTY
        if header:
            # Jména z původního řádku podle pozic ve složeném – `_fold`
            # zachovává délku, takže indexy sedí a diakritika zůstane.
            issuer_name = issuer_name or line[header.start("issuer"):header.end("issuer")].strip()
            counterparty_name = (
                counterparty_name or line[header.start("other"):header.end("other")].strip(" :")
            )
            continue

        if side is None:
            continue
        row = _parse_row(line)
        if row is None:
            continue
        (issuer_rows if side == _ISSUER else counterparty_rows).append(row)

    if not issuer_rows and not counterparty_rows:
        return None

    total = None
    total_match = re.search(rf"celkem\s+zapocteno\s*:?\s*({_AMOUNT})", folded_text)
    if total_match:
        total = V.normalize_amount(total_match.group(1))
    if total is None:
        offsets = [r.offset for r in issuer_rows if r.offset is not None]
        total = sum(offsets, Decimal("0.00")) if offsets else None

    icos = _icos(text)
    return OffsetStatement(
        number=number,
        date=statement_date,
        issuer_name=issuer_name,
        counterparty_name=counterparty_name,
        # Vystavitel se v hlavičce podepisuje první (levý sloupec adresního
        # bloku). Je to jen odhad – kdo je kdo, pozná spolehlivě až aplikace
        # podle vlastního IČO, protože ta jediná ví, která strana je „my".
        issuer_ico=icos[0] if icos else "",
        counterparty_ico=icos[1] if len(icos) > 1 else "",
        issuer_claims=tuple(issuer_rows),
        counterparty_claims=tuple(counterparty_rows),
        total_offset=total,
    )
