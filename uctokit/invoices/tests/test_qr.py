"""Testy čtení QR Platby / QR Faktury (SPAYD/SID) a jejich zapojení do pipeline.

Parser je čistý (bez závislostí) → testuje se přímo. Zapojení do pipeline se
testuje injektováním známého QR výsledku (bez reálného dekódování obrázku).
"""

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from uctokit.invoices import pipeline as P
from uctokit.invoices import qr
from uctokit.invoices.types import ExtractedInvoice, Field, SourceDocument


class ParseSpaydTests(unittest.TestCase):
    def test_qr_platba_basic(self):
        payload = (
            "SPD*1.0*ACC:CZ6706000000001111111111*AM:1234.50*CC:CZK"
            "*X-VS:2026001*DT:20260215*MSG:Faktura"
        )
        inv = qr.parse_spayd(payload)
        self.assertIsNotNone(inv)
        self.assertEqual(inv.supplier_iban.value, "CZ6706000000001111111111")
        self.assertEqual(inv.supplier_account.value, "1111111111/0600")
        self.assertEqual(inv.total_amount.value, Decimal("1234.50"))
        self.assertEqual(inv.currency.value, "CZK")
        self.assertEqual(inv.variable_symbol.value, "2026001")
        self.assertEqual(inv.due_date.value, date(2026, 2, 15))
        # Deterministický zdroj → vysoká jistota u platných polí.
        self.assertEqual(inv.supplier_iban.source, "qr")
        self.assertGreater(inv.supplier_iban.confidence, 0.9)

    def test_qr_faktura_sid_header_fields(self):
        payload = (
            "SID*1.0*ID:FA2026-007*DD:20260101*AM:500.00*VS:12345"
            "*INI:12345679*ACC:CZ6706000000001111111111"
        )
        inv = qr.parse_spayd(payload)
        self.assertIsNotNone(inv)
        self.assertEqual(inv.invoice_number.value, "FA2026-007")
        self.assertEqual(inv.issue_date.value, date(2026, 1, 1))
        self.assertEqual(inv.supplier_ico.value, "12345679")   # platné IČO
        self.assertGreater(inv.supplier_ico.confidence, 0.9)
        self.assertEqual(inv.variable_symbol.value, "12345")

    def test_prefixed_account(self):
        # Předčíslí účtu v IBAN (pozice 9–14).
        inv = qr.parse_spayd("SPD*1.0*ACC:CZ9708000000191111111111*AM:10*CC:CZK")
        self.assertEqual(inv.supplier_account.value, "19-1111111111/0800")

    def test_invalid_ico_lowers_confidence(self):
        inv = qr.parse_spayd("SPD*1.0*ACC:CZ6706000000001111111111*AM:1*INI:12345678")
        self.assertEqual(inv.supplier_ico.value, "12345678")
        self.assertLess(inv.supplier_ico.confidence, 0.7)

    def test_not_spayd_returns_none(self):
        self.assertIsNone(qr.parse_spayd("https://example.com/faktura"))
        self.assertIsNone(qr.parse_spayd(""))

    def test_iban_with_bic_suffix(self):
        inv = qr.parse_spayd("SPD*1.0*ACC:CZ6706000000001111111111+RZBCCZPP*AM:5*CC:CZK")
        self.assertEqual(inv.supplier_iban.value, "CZ6706000000001111111111")


class _StubLLM:
    """Falešný LLM extraktor – kdyby ho pipeline zavolala, hlídáme to."""

    supports_vision = True

    def __init__(self):
        self.text_calls = 0
        self.image_calls = 0

    def from_text(self, text):
        self.text_calls += 1
        return ExtractedInvoice()

    def from_images(self, images):
        self.image_calls += 1
        return ExtractedInvoice()


class QrInPipelineTests(unittest.TestCase):
    def setUp(self):
        self._orig = qr.extract_from_qr

    def tearDown(self):
        qr.extract_from_qr = self._orig

    def _inject(self, inv):
        qr.extract_from_qr = lambda document: inv

    def test_payment_only_qr_does_not_skip_llm(self):
        payment = ExtractedInvoice()
        payment.supplier_account = Field("1111111111/0600", 0.95, "qr")
        payment.total_amount = Field(Decimal("100.00"), 0.95, "qr")
        payment.variable_symbol = Field("2026001", 0.95, "qr")
        self.assertTrue(P._qr_covers_payment(payment))  # účet + částka stačí
        self._inject(payment)

        llm = _StubLLM()
        self.assertFalse(P._qr_covers_invoice(payment))
        doc = SourceDocument(content=b"%PDF-1.4 sken", filename="faktura.pdf")
        with mock.patch("uctokit.invoices.pdf_text.extract_text", return_value="Faktura 2026001\n" + "x" * 50):
            result = P.extract(doc, llm=llm)

        self.assertEqual(llm.text_calls, 1)      # AI doplní hlavičku faktury
        self.assertEqual(llm.image_calls, 0)
        self.assertTrue(result.method.startswith("qr"))
        self.assertEqual(result.invoice.total_amount.value, Decimal("100.00"))
        self.assertEqual(result.invoice.supplier_account.value, "1111111111/0600")

    def test_complete_sid_can_skip_llm(self):
        complete = qr.parse_spayd(
            "SID*1.0*ACC:CZ6706000000001111111111*AM:100*CC:CZK*DT:20260215"
            "*DD:20260201*ID:FA-1*INI:12345679"
        )
        self.assertTrue(P._qr_covers_invoice(complete))
        self._inject(complete)
        llm = _StubLLM()
        with mock.patch("uctokit.invoices.pdf_text.extract_text", return_value="Faktura FA-1\n" + "x" * 50):
            P.extract(SourceDocument(b"%PDF", "faktura.pdf"), llm=llm)
        self.assertEqual(llm.text_calls, 0)

    def test_partial_qr_does_not_skip_ai_but_wins_values(self):
        # QR nese jen částku (ne účet) → AI se nepřeskočí, ale QR přebije hodnotu.
        partial = ExtractedInvoice()
        partial.total_amount = Field(Decimal("777.00"), 0.95, "qr")
        self.assertFalse(P._qr_covers_payment(partial))  # bez účtu → AI se nevypne
        self._inject(partial)

        doc = SourceDocument(content=b"%PDF-1.4 sken", filename="faktura.pdf")
        result = P.extract(doc)  # bez LLM – ověřujeme jen překrytí QR polem
        self.assertEqual(result.invoice.total_amount.value, Decimal("777.00"))
        self.assertIn("qr", result.method)

    def test_qr_overrides_conflicting_value_with_warning(self):
        # Přímé ověření překrytí: základ má jinou částku, QR ji přebije + varuje.
        base = P.ExtractionResult(
            invoice=ExtractedInvoice(), method="pdf-text",
        )
        base.invoice.total_amount = Field(Decimal("500.00"), 0.8, "pdf-text")
        qr_inv = ExtractedInvoice()
        qr_inv.total_amount = Field(Decimal("450.00"), 0.95, "qr")

        merged = P._apply_qr(base, qr_inv)
        self.assertEqual(merged.invoice.total_amount.value, Decimal("450.00"))
        self.assertEqual(merged.method, "qr+pdf-text")
        self.assertTrue(any("QR" in w for w in merged.warnings))


if __name__ == "__main__":
    unittest.main()
