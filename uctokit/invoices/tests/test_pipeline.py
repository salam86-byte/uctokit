"""Testy orchestrace (žebřík ISDOC -> text+LLM -> sken). LLM je vždy falešný."""

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from uctokit.invoices import (
    ExtractionConfig,
    LLMInvoiceExtractor,
    SourceDocument,
    extract,
)
from uctokit.invoices.tests.test_isdoc import ISDOC_SAMPLE

SAMPLE_TEXT = (
    "Dodavatel: Vzorovy dodavatel s.r.o.\n"
    "IČO: 12345679\n"
    "Variabilní symbol: 55667788\n"
    "Datum vystavení: 1. 9. 2025\n"
    "Celkem k úhradě: 5 000,00 Kč"
)

LLM_PAYLOAD = {
    "supplier_name": "Vzorovy dodavatel s.r.o.",
    "supplier_ico": "12345679",
    "supplier_iban": None,
    "supplier_account": None,
    "total_amount": "5000.00",
    "currency": "CZK",
    "variable_symbol": "55667788",
    "invoice_number": "FV2025-9",
    "issue_date": "2025-09-01",
    "due_date": "2025-09-14",
}


class FakeProvider:
    """Falešný LLM provider – vrací předem daný JSON, žádná síť."""

    def __init__(self, payload, *, supports_vision=False):
        self.payload = payload
        self._vision = supports_vision
        self.calls = []

    @property
    def supports_vision(self):
        return self._vision

    def complete_json(self, *, system, user, images=None):
        self.calls.append({"images": images})
        return self.payload


def fake_extractor(payload, **kw):
    return LLMInvoiceExtractor(FakeProvider(payload, **kw))


class IsdocDispatchTests(unittest.TestCase):
    def test_by_filename(self):
        r = extract(SourceDocument(ISDOC_SAMPLE, "faktura.isdoc"))
        self.assertEqual(r.method, "isdoc")
        self.assertEqual(r.invoice.total_amount.value, Decimal("13000.00"))

    def test_by_content_sniff(self):
        # Přípona neříká nic, ale obsah je XML -> pozná se jako ISDOC.
        r = extract(SourceDocument(ISDOC_SAMPLE, "dokument.txt"))
        self.assertEqual(r.method, "isdoc")

    def test_invalid_ico_lowers_confidence_and_warns(self):
        bad = ISDOC_SAMPLE.replace(b"12345679", b"12345678")
        r = extract(SourceDocument(bad, "faktura.isdoc"))
        self.assertLess(r.invoice.supplier_ico.confidence, 0.8)
        self.assertTrue(any("IČO" in w for w in r.warnings))


class TextPathTests(unittest.TestCase):
    def _run(self, extractor=None):
        with mock.patch("uctokit.invoices.pdf_text.extract_text", return_value=SAMPLE_TEXT):
            doc = SourceDocument(b"%PDF-fake", "faktura.pdf")
            return extract(doc, llm=extractor)

    def test_text_plus_llm(self):
        r = self._run(fake_extractor(LLM_PAYLOAD))
        self.assertEqual(r.method, "pdf-text+llm")
        self.assertEqual(r.invoice.supplier_name.value, "Vzorovy dodavatel s.r.o.")
        self.assertEqual(r.invoice.due_date.value, date(2025, 9, 14))
        # Shoda LLM + heuristiky na IČO -> vysoká jistota.
        self.assertGreaterEqual(r.invoice.supplier_ico.confidence, 0.9)
        self.assertGreater(r.overall_confidence, 0.7)

    def test_text_only_heuristics_when_no_llm(self):
        r = self._run(None)
        self.assertEqual(r.method, "pdf-text")
        self.assertEqual(r.invoice.supplier_ico.value, "12345679")
        self.assertEqual(r.invoice.total_amount.value, Decimal("5000.00"))
        # supplier_name heuristika nezná -> prázdné
        self.assertFalse(r.invoice.supplier_name.is_present)

    def test_disagreement_warns(self):
        payload = dict(LLM_PAYLOAD, supplier_ico="00000000")
        r = self._run(fake_extractor(payload))
        self.assertEqual(r.invoice.supplier_ico.value, "00000000")  # LLM vyhrává hodnotou
        self.assertTrue(any("supplier_ico" in w for w in r.warnings))


class ScanPathTests(unittest.TestCase):
    def test_vision_when_available(self):
        extractor = fake_extractor(LLM_PAYLOAD, supports_vision=True)
        with mock.patch("uctokit.invoices.pdf_text.extract_text", return_value=""), \
             mock.patch("uctokit.invoices.pdf_text.render_page_images", return_value=[b"imgbytes"]):
            r = extract(SourceDocument(b"%PDF-scan", "sken.pdf"), llm=extractor)
        self.assertEqual(r.method, "vision")
        self.assertEqual(r.invoice.supplier_name.value, "Vzorovy dodavatel s.r.o.")
        self.assertTrue(extractor.provider.calls[0]["images"])

    def test_unreadable_scan_returns_empty_with_warning(self):
        with mock.patch("uctokit.invoices.pdf_text.extract_text", return_value=""), \
             mock.patch("uctokit.invoices.ocr.ocr_pdf", return_value=""):
            r = extract(SourceDocument(b"%PDF-scan", "sken.pdf"), llm=None)
        self.assertEqual(r.method, "")
        self.assertFalse(r.invoice.total_amount.is_present)
        self.assertTrue(r.warnings)


class LegacyDictTests(unittest.TestCase):
    def test_shape(self):
        r = extract(SourceDocument(ISDOC_SAMPLE, "faktura.isdoc"))
        d = r.to_legacy_dict()
        self.assertEqual(d["supplier_ico"], "12345679")
        self.assertEqual(d["total_amount"], Decimal("13000.00"))
        self.assertEqual(d["method"], "isdoc")
        self.assertIn("supplier_iban", d)


if __name__ == "__main__":
    unittest.main()
