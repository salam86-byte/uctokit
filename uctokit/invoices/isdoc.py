"""ISDOC (XML) – český standard elektronické faktury.

Strukturální parse: co je v ISDOC uvedené, bereme 1:1 s vysokou jistotou.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from . import validators as V
from .types import Field, ExtractedInvoice, SOURCE_ISDOC

# Jistota strukturálně přečtených polí. Ne 1.0 – i ISDOC může být vyplněný chybně.
ISDOC_CONFIDENCE = 0.98


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _field(value, *, raw=None) -> Field:
    if value in (None, ""):
        return Field()
    return Field(value=value, confidence=ISDOC_CONFIDENCE, source=SOURCE_ISDOC, raw=raw)


def parse_isdoc(content: bytes) -> ExtractedInvoice | None:
    """Vrátí :class:`ExtractedInvoice`, nebo ``None`` když to není ISDOC."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    if _localname(root.tag) != "Invoice":
        return None

    def first(name, within=None):
        node = root if within is None else within
        for el in node.iter():
            if _localname(el.tag) == name and el.text and el.text.strip():
                return el.text.strip()
        return None

    def subtree(name):
        for el in root.iter():
            if _localname(el.tag) == name:
                return el
        return None

    # Číslo faktury = přímý potomek <ID> kořene (ne IČO dodavatele hlouběji).
    invoice_number = ""
    for child in root:
        if _localname(child.tag) == "ID" and child.text:
            invoice_number = child.text.strip()
            break

    supplier = subtree("AccountingSupplierParty")
    raw_ico = first("ID", supplier)
    raw_iban = first("IBAN")
    raw_amount = first("PayableAmount")

    # Číslo účtu z platebních údajů (Details: <ID> účet + <BankCode>).
    account = ""
    details = subtree("Details")
    if details is None:
        details = subtree("BankAccount")
    if details is not None:
        acc_id = first("ID", details)
        bank_code = first("BankCode", details)
        if acc_id and bank_code:
            account = f"{acc_id}/{bank_code}"
        elif acc_id:
            account = acc_id

    inv = ExtractedInvoice(
        supplier_name=_field(first("Name", supplier)),
        supplier_ico=_field(V.normalize_ico(raw_ico) if raw_ico else None, raw=raw_ico),
        supplier_iban=_field(V.normalize_iban(raw_iban) if raw_iban else None, raw=raw_iban),
        supplier_account=_field(V.normalize_account(account) if account else None, raw=account or None),
        total_amount=_field(V.normalize_amount(raw_amount), raw=raw_amount),
        currency=_field(first("CurrencyCode") or ("CZK" if raw_amount else None)),
        variable_symbol=_field(first("VariableSymbol")),
        invoice_number=_field(invoice_number or None),
        issue_date=_field(V.normalize_date(first("IssueDate")), raw=first("IssueDate")),
        # DUZP nese ISDOC jako `TaxPointDate` — strojově a přesně, takže se
        # u elektronické faktury nemusí dohadovat z textu.
        taxable_date=_field(V.normalize_date(first("TaxPointDate")),
                            raw=first("TaxPointDate")),
        due_date=_field(V.normalize_date(first("PaymentDueDate")), raw=first("PaymentDueDate")),
    )
    return inv
