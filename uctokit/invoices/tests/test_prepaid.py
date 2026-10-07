"""Doklad celý uhrazený zálohou: „k úhradě 0", částka = cena vč. DPH."""

import unittest
from decimal import Decimal
from unittest import mock

from uctokit.invoices import SourceDocument, extract
from uctokit.invoices.heuristics import prepaid_total
from uctokit.invoices.scoring import overall_confidence
from uctokit.invoices.tests.test_pipeline import fake_extractor

# Servisní doklad placený předem v aplikaci — anglické oddělovače částek.
SERVICE_TEXT = (
    "Vzorový servis s.r.o.\n"
    "IČO 123 45 679, DIČ CZ12345679 Placeno\n"
    "Celková částka k úhradě: 0.00 (CZK)\n"
    "Díly celkem 1,000.00\n"
    "Cena bez DPH 1,500.00\n"
    "DPH 315.00\n"
    "Celková cena vč. DPH (CZK) 1,815.00\n"
    "Zaplacená záloha 1,815.00\n"
    "Celkem k úhradě 0.00\n"
)

# Vyúčtování celé pokryté zálohou — české oddělovače, odpočet záporně.
SETTLEMENT_TEXT = (
    "Celkem s DPH 48 400,00\n"
    "Odpočet zálohy -48 400,00\n"
    "Celkem k úhradě 0,00 Kč\n"
)


# E-shop s platbou předem — tabulkové řádky (základ, DPH, s DPH vedle sebe)
# a na řádku úhrady ještě VS a sazba, které se lepí na částky.
ESHOP_TEXT = (
    "Celkem za fakturu: 500,00 105,00 605,00Kč\n"
    "Přijatá úplata v.s.: 2026000999 21 500,00 105,00 605,00Kč\n"
    "Celkem: 0,00 0,00 0,00Kč\n"
    "Zaplaceno 0,00Kč\n"
    "Celkem k úhradě 0,00Kč\n"
)


class PrepaidTotalTests(unittest.TestCase):
    def test_service_receipt(self):
        self.assertEqual(prepaid_total(SERVICE_TEXT), Decimal("1815.00"))

    def test_eshop_table_rows(self):
        self.assertEqual(prepaid_total(ESHOP_TEXT), Decimal("605.00"))

    def test_narrow_space_thousands(self):
        text = (SERVICE_TEXT.replace("1,815.00", "1\u202f815,00")
                .replace("0.00", "0,00"))
        self.assertEqual(prepaid_total(text), Decimal("1815.00"))
        text = SERVICE_TEXT.replace("1,815.00", "1\u00a0815,00")
        self.assertEqual(prepaid_total(text), Decimal("1815.00"))

    def test_settlement_with_negative_deduction(self):
        self.assertEqual(prepaid_total(SETTLEMENT_TEXT), Decimal("48400.00"))

    def test_something_left_to_pay(self):
        text = SERVICE_TEXT.replace("Celkem k úhradě 0.00", "Celkem k úhradě 100.00")
        self.assertIsNone(prepaid_total(text))

    def test_advance_does_not_cover_total(self):
        # Záloha jiná než cena — nula „k úhradě" pak nedává smysl, nic se nebere.
        text = SERVICE_TEXT.replace("Zaplacená záloha 1,815.00", "Zaplacená záloha 1,000.00")
        self.assertIsNone(prepaid_total(text))

    def test_plain_invoice(self):
        self.assertIsNone(prepaid_total(
            "Celkem vč. DPH 1 210,00\nCelkem k úhradě 1 210,00 Kč\n"))


class PrepaidPipelineTests(unittest.TestCase):
    PAYLOAD = {
        "supplier_name": "Vzorový servis s.r.o.",
        "supplier_ico": "12345679",
        "total_amount": "0.00",          # model vzal „k úhradě"
        "currency": "CZK",
        "invoice_number": "S-1",
        "issue_date": "2026-10-06",
    }

    def _run(self, payload):
        with mock.patch("uctokit.invoices.pdf_text.extract_text", return_value=SERVICE_TEXT):
            return extract(SourceDocument(b"%PDF-fake", "doklad.pdf"),
                           llm=fake_extractor(payload))

    def test_zero_becomes_gross_total_and_nothing_due(self):
        r = self._run(self.PAYLOAD)
        inv = r.invoice
        self.assertEqual(inv.total_amount.value, Decimal("1815.00"))
        self.assertEqual(inv.amount_due, Decimal("0.00"))
        self.assertTrue(inv.fully_paid)
        self.assertFalse(any("nulová" in w for w in r.warnings))

    def test_paid_document_does_not_miss_vs_and_due_date(self):
        r = self._run(self.PAYLOAD)
        # Klíčová pole jsou jen název, IČO a částka — VS ani splatnost
        # na zaplaceném dokladu nechybí.
        self.assertGreater(r.overall_confidence, 0.75)
        self.assertEqual(r.overall_confidence, overall_confidence(r.invoice))

    def test_present_vs_and_due_date_still_count(self):
        from uctokit.invoices.scoring import overall_from_confidences

        base = {"supplier_name": 0.7, "supplier_ico": 0.9, "total_amount": 0.8}
        # Chybějící VS a splatnost zaplacený doklad nesrážejí…
        self.assertEqual(overall_from_confidences(base, paid=True), 0.8)
        # …ale když na dokladu jsou, počítají se — vyřadit je by jistotu
        # dobře vytěženého dokladu snížilo.
        full = {**base, "variable_symbol": 0.99, "due_date": 0.99}
        self.assertEqual(overall_from_confidences(full, paid=True),
                         overall_from_confidences(full))

    def test_other_nonzero_amount_is_left_alone(self):
        r = self._run({**self.PAYLOAD, "total_amount": "999.00"})
        self.assertEqual(r.invoice.total_amount.value, Decimal("999.00"))
        self.assertIsNone(r.invoice.amount_due)
        self.assertFalse(r.invoice.fully_paid)


if __name__ == "__main__":
    unittest.main()
