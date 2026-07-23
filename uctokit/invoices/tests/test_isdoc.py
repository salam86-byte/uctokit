"""Testy ISDOC parseru."""

import unittest
from datetime import date
from decimal import Decimal

from uctokit.invoices.isdoc import parse_amounts, parse_isdoc, parse_totals
from uctokit.invoices.scoring import overall_confidence, rescore

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


# Běžná faktura BEZ zálohy, kde se jen zaokrouhluje na celé koruny:
# 4812.66 + 0.34 = 4813. Rozdíl mezi `total` a `payable` tu NENÍ záloha.
ROUNDED_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="http://isdoc.cz/namespace/2013" version="6.0.1">
  <ID>2026-0170</ID>
  <IssueDate>2026-05-20</IssueDate>
  <LegalMonetaryTotal>
    <TaxExclusiveAmount>4309.94</TaxExclusiveAmount>
    <TaxInclusiveAmount>4812.66</TaxInclusiveAmount>
    <AlreadyClaimedTaxInclusiveAmount>0</AlreadyClaimedTaxInclusiveAmount>
    <DifferenceTaxInclusiveAmount>4812.66</DifferenceTaxInclusiveAmount>
    <PayableRoundingAmount>0.34</PayableRoundingAmount>
    <PaidDepositsAmount>0</PaidDepositsAmount>
    <PayableAmount>4813</PayableAmount>
  </LegalMonetaryTotal>
</Invoice>"""


class ParseAmountsTests(unittest.TestCase):
    def test_deposit_is_recognised(self):
        amounts = parse_amounts(SETTLEMENT_SAMPLE)
        self.assertEqual(amounts.total, Decimal("72721.00"))
        self.assertEqual(amounts.payable, Decimal("0.00"))
        self.assertEqual(amounts.deposit, Decimal("72721.00"))
        self.assertTrue(amounts.has_deposit)

    def test_rounding_is_not_a_deposit(self):
        """Zaokrouhlení o 34 haléřů nesmí vypadat jako odečtená záloha."""
        amounts = parse_amounts(ROUNDED_SAMPLE)
        self.assertEqual(amounts.total, Decimal("4812.66"))
        self.assertEqual(amounts.payable, Decimal("4813.00"))
        self.assertEqual(amounts.rounding, Decimal("0.34"))
        self.assertFalse(amounts.has_deposit)

    def test_to_pay_prefers_payable(self):
        self.assertEqual(parse_amounts(ROUNDED_SAMPLE).to_pay, Decimal("4813.00"))

    def test_to_pay_falls_back_to_total(self):
        xml = ROUNDED_SAMPLE.replace(b"<PayableAmount>4813</PayableAmount>", b"")
        amounts = parse_amounts(xml)
        self.assertEqual(amounts.payable, Decimal("4812.66"))  # z Difference…
        self.assertEqual(amounts.to_pay, Decimal("4812.66"))

    def test_document_without_amounts(self):
        amounts = parse_amounts(ISDOC_SAMPLE)
        self.assertIsNone(amounts.total)
        self.assertFalse(amounts.has_deposit)
        self.assertIsNone(amounts.to_pay)


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


# Hotovostní faktura (ALL SPORTS): PaymentMeansCode 10 = v hotovosti, proto
# BEZ účtu, VS i splatnosti – schválně, ne chybou extrakce.
CASH_SAMPLE = b"""<?xml version="1.0" encoding="utf-8"?>
<Invoice xmlns="http://isdoc.cz/namespace/invoice" version="5.2">
  <ID>26002894</ID>
  <IssueDate>2026-06-18</IssueDate>
  <AccountingSupplierParty><Party>
    <PartyIdentification><ID>26770164</ID></PartyIdentification>
    <PartyName><Name>ALL SPORTS a. s.</Name></PartyName>
  </Party></AccountingSupplierParty>
  <PaymentMeans><Payment>
    <PaidAmount>4130</PaidAmount>
    <PaymentMeansCode>10</PaymentMeansCode>
  </Payment></PaymentMeans>
  <LegalMonetaryTotal><TaxInclusiveAmount>4130.00</TaxInclusiveAmount></LegalMonetaryTotal>
</Invoice>"""


# Dobropis: záporná částka je legitimní opravný doklad, ne „nulová/nečitelná".
CREDIT_NOTE_SAMPLE = b"""<?xml version="1.0" encoding="utf-8"?>
<Invoice xmlns="http://isdoc.cz/namespace/invoice" version="5.2">
  <ID>26003338</ID>
  <IssueDate>2026-06-26</IssueDate>
  <AccountingSupplierParty><Party>
    <PartyIdentification><ID>26770164</ID></PartyIdentification>
    <PartyName><Name>ALL SPORTS a. s.</Name></PartyName>
  </Party></AccountingSupplierParty>
  <PaymentMeans><Payment>
    <PaymentMeansCode>42</PaymentMeansCode>
    <Details><ID>2106782708</ID><BankCode>2700</BankCode></Details>
    <PaymentDueDate>2026-07-10</PaymentDueDate>
  </Payment></PaymentMeans>
  <LegalMonetaryTotal><TaxInclusiveAmount>-1453.00</TaxInclusiveAmount></LegalMonetaryTotal>
</Invoice>"""


class CashAndCreditNoteTests(unittest.TestCase):
    """Hotovostní doklad a dobropis se nesmí hodnotit jako chybně vyčtené."""

    def test_cash_payment_is_detected(self):
        inv = parse_isdoc(CASH_SAMPLE)
        self.assertTrue(inv.payment_in_cash)
        self.assertFalse(inv.supplier_account.is_present)  # hotovost = bez účtu
        self.assertFalse(inv.variable_symbol.is_present)

    def test_transfer_is_not_cash(self):
        self.assertFalse(parse_isdoc(ISDOC_SAMPLE).payment_in_cash)

    def test_cash_invoice_keeps_high_confidence(self):
        # Chybějící VS/splatnost NESMÍ srazit jistotu – u hotovosti tam nepatří.
        inv = parse_isdoc(CASH_SAMPLE)
        rescore(inv, None)
        self.assertGreaterEqual(overall_confidence(inv), 0.9)

    def test_negative_amount_is_a_credit_note_not_error(self):
        inv = parse_isdoc(CREDIT_NOTE_SAMPLE)
        self.assertEqual(inv.total_amount.value, Decimal("-1453.00"))
        warnings = rescore(inv, None)
        self.assertFalse(any("nulová nebo nečitelná" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
