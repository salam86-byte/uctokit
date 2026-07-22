"""Testy ISDOC parseru."""

import unittest
from datetime import date
from decimal import Decimal

from uctokit.invoices.isdoc import parse_isdoc, parse_totals

ISDOC_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="http://isdoc.cz/namespace/2013" version="6.0.1">
  <ID>2025-1234</ID>
  <IssueDate>2025-09-15</IssueDate>
  <VariableSymbol>20251234</VariableSymbol>
  <AccountingSupplierParty><Party>
    <PartyIdentification><ID>12345679</ID></PartyIdentification>
    <PartyName><Name>Sportovni potreby s.r.o.</Name></PartyName>
  </Party></AccountingSupplierParty>
  <PaymentMeans><Payment>
    <Details><ID>1111111111</ID><BankCode>0800</BankCode><IBAN>CZ9708000000191111111111</IBAN></Details>
    <PaymentDueDate>2025-09-30</PaymentDueDate>
  </Payment></PaymentMeans>
  <InvoiceSummary><PayableAmount>13000.00</PayableAmount></InvoiceSummary>
</Invoice>"""


# Vyúčtovací faktura: hodnota dokladu 72 721, ale záloha už je zaplacená,
# takže k úhradě zbývá nula. Obě čísla musí přežít.
SETTLEMENT_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="http://isdoc.cz/namespace/2013" version="6.0.1">
  <ID>2026-0133</ID>
  <IssueDate>2026-05-31</IssueDate>
  <AccountingSupplierParty><Party>
    <PartyIdentification><ID>12345679</ID></PartyIdentification>
    <PartyName><Name>Vzorovy dodavatel a.s.</Name></PartyName>
  </Party></AccountingSupplierParty>
  <LegalMonetaryTotal>
    <TaxExclusiveAmount>60100.00</TaxExclusiveAmount>
    <TaxInclusiveAmount>72721.00</TaxInclusiveAmount>
    <AlreadyClaimedTaxInclusiveAmount>72721.00</AlreadyClaimedTaxInclusiveAmount>
    <DifferenceTaxInclusiveAmount>0.00</DifferenceTaxInclusiveAmount>
    <PayableAmount>0.00</PayableAmount>
  </LegalMonetaryTotal>
</Invoice>"""


class ParseTotalsTests(unittest.TestCase):
    def test_settlement_invoice_keeps_both_amounts(self):
        total, payable = parse_totals(SETTLEMENT_SAMPLE)
        self.assertEqual(total, Decimal("72721.00"))
        self.assertEqual(payable, Decimal("0.00"))

    def test_total_amount_is_not_the_payable_zero(self):
        """Doklad na 72 tisíc nesmí vypadat jako prázdný."""
        inv = parse_isdoc(SETTLEMENT_SAMPLE)
        self.assertEqual(inv.total_amount.value, Decimal("72721.00"))

    def test_difference_used_when_payable_missing(self):
        xml = SETTLEMENT_SAMPLE.replace(b"<PayableAmount>0.00</PayableAmount>", b"")
        total, payable = parse_totals(xml)
        self.assertEqual(total, Decimal("72721.00"))
        self.assertEqual(payable, Decimal("0.00"))

    def test_without_legal_monetary_total_returns_nothing(self):
        self.assertEqual(parse_totals(ISDOC_SAMPLE), (None, None))

    def test_invalid_bytes_return_nothing(self):
        self.assertEqual(parse_totals(b"not xml"), (None, None))


class ParseIsdocTests(unittest.TestCase):
    def test_extracts_all_fields(self):
        inv = parse_isdoc(ISDOC_SAMPLE)
        self.assertIsNotNone(inv)
        self.assertEqual(inv.supplier_name.value, "Sportovni potreby s.r.o.")
        self.assertEqual(inv.supplier_ico.value, "12345679")
        self.assertEqual(inv.supplier_iban.value, "CZ9708000000191111111111")
        self.assertEqual(inv.supplier_account.value, "1111111111/0800")
        self.assertEqual(inv.total_amount.value, Decimal("13000.00"))
        self.assertEqual(inv.currency.value, "CZK")
        self.assertEqual(inv.variable_symbol.value, "20251234")
        self.assertEqual(inv.invoice_number.value, "2025-1234")
        self.assertEqual(inv.issue_date.value, date(2025, 9, 15))
        self.assertEqual(inv.due_date.value, date(2025, 9, 30))

    def test_present_fields_high_confidence(self):
        inv = parse_isdoc(ISDOC_SAMPLE)
        self.assertGreaterEqual(inv.supplier_name.confidence, 0.9)
        self.assertEqual(inv.supplier_name.source, "isdoc")

    def test_non_isdoc_xml_returns_none(self):
        self.assertIsNone(parse_isdoc(b"<Something><x>1</x></Something>"))

    def test_invalid_bytes_returns_none(self):
        self.assertIsNone(parse_isdoc(b"not xml at all"))


if __name__ == "__main__":
    unittest.main()
