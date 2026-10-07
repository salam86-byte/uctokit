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

    def test_qr_platba_f_nested_invoice(self):
        # QR Platba + F: SID uvnitř platby jako `X-INV` s hvězdičkou `%2A`.
        payload = (
            "SPD*1.0*AM:1234.50*MSG:FA2026007*X-VS:2026007*CC:CZK"
            "*ACC:CZ6706000000001111111111*X-INV:SID%2A1.0%2AID:FA2026007"
            "%2ADD:20261007%2AINI:12345679%2AVII:CZ12345679%2AINR:87654321"
            "%2AVIR:CZ87654321%2ADUZP:20261005%2AAM:1.00%2AX-SW:Test*"
        )
        inv = qr.parse_spayd(payload)
        self.assertEqual(inv.invoice_number.value, "FA2026007")
        self.assertEqual(inv.issue_date.value, date(2026, 10, 7))
        self.assertEqual(inv.taxable_date.value, date(2026, 10, 5))
        # Dodavatel z INI/VII, nikdy odběratel z INR/VIR.
        self.assertEqual(inv.supplier_ico.value, "12345679")
        self.assertEqual(inv.supplier_dic.value, "CZ12345679")
        # Vnější platba má přednost před částkou ze SID.
        self.assertEqual(inv.total_amount.value, Decimal("1234.50"))
        self.assertEqual(inv.variable_symbol.value, "2026007")

    def test_url_encoded_values_are_decoded(self):
        inv = qr.parse_spayd(
            "SPD*1.0*ACC:CZ6706000000001111111111*AM:10*RN:Ji%C5%99%C3%AD%20Vzor*MSG:sleva 10%")
        self.assertEqual(inv.supplier_name.value, "Jiří Vzor")

    def test_customer_ids_are_not_supplier(self):
        inv = qr.parse_spayd(
            "SID*1.0*ID:1*INR:12345679*VIR:CZ12345679*AM:10")
        self.assertFalse(inv.supplier_ico.is_present)
        self.assertFalse(inv.supplier_dic.is_present)

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

    def test_nested_header_does_not_skip_llm(self):
        # QR Platba + F nese i kompletní hlavičku, ale AI se kvůli ní
        # nevynechává — dokud se nečetla, model u takových dokladů běžel.
        vnorena = qr.parse_spayd(
            "SPD*1.0*ACC:CZ6706000000001111111111*AM:100*CC:CZK*DT:20260215"
            "*RN:VZOROVY DODAVATEL*X-INV:SID%2A1.0%2AID:FA-1%2ADD:20260201"
            "%2AINI:12345679"
        )
        self.assertEqual(vnorena.invoice_number.value, "FA-1")
        self.assertFalse(P._qr_covers_invoice(vnorena))

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

    def test_qr_does_not_override_full_supplier_name(self):
        # QR/SPD název je oříznutý/verzálky → NEPŘEPÍŠE plný název z textu,
        # ostatní QR pole (VS) se ale přiloží normálně.
        base = P.ExtractionResult(invoice=ExtractedInvoice(), method="pdf-text")
        base.invoice.supplier_name = Field("Čerpací karty s.r.o., odštěpný závod", 0.85, "pdf-text")
        base.invoice.total_amount = Field(Decimal("100.00"), 0.8, "pdf-text")
        qr_inv = ExtractedInvoice()
        qr_inv.supplier_name = Field("CERPACI KARTY S.R.O. ODSTEPNY ZA", 0.9, "qr")
        qr_inv.variable_symbol = Field("2640173", 0.95, "qr")
        merged = P._apply_qr(base, qr_inv)
        self.assertEqual(
            merged.invoice.supplier_name.value, "Čerpací karty s.r.o., odštěpný závod"
        )
        self.assertEqual(merged.invoice.variable_symbol.value, "2640173")

    def test_nested_number_does_not_override_printed_one(self):
        # Hlavička v QR Platbě + F: `ID` bývá interní označení — na papíře
        # „2026000123", v kódu „VF12-262026000123". Bez čísla z textu se ale vezme.
        vnorena = qr.parse_spayd(
            "SPD*1.0*ACC:CZ6706000000001111111111*AM:10"
            "*X-INV:SID%2A1.0%2AID:VF12-262026000123")
        base = P.ExtractionResult(invoice=ExtractedInvoice(), method="pdf-text")
        base.invoice.invoice_number = Field("2026000123", 0.85, "llm")
        merged = P._apply_qr(base, vnorena)
        self.assertEqual(merged.invoice.invoice_number.value, "2026000123")

        empty = P.ExtractionResult(invoice=ExtractedInvoice(), method="pdf-text")
        merged = P._apply_qr(empty, vnorena)
        self.assertEqual(merged.invoice.invoice_number.value, "VF12-262026000123")

    def test_nested_fields_only_fill_gaps(self):
        # Vnořená hlavička nepřebije nic z textu — ani DUZP, ani IČO; jen
        # doplní, co text nemá. Vnější platba (VS) přebíjí jako dřív.
        from datetime import date as d

        vnorena = qr.parse_spayd(
            "SPD*1.0*ACC:CZ6706000000001111111111*AM:10*X-VS:777"
            "*X-INV:SID%2A1.0%2ADUZP:20261005%2AINI:12345679%2ADD:20261007")
        self.assertEqual(vnorena.qr_nested_fields,
                         {"taxable_date", "supplier_ico", "issue_date"})
        base = P.ExtractionResult(invoice=ExtractedInvoice(), method="pdf-text")
        base.invoice.taxable_date = Field(d(2026, 9, 30), 0.87, "llm")
        base.invoice.variable_symbol = Field("123", 0.87, "llm")
        merged = P._apply_qr(base, vnorena).invoice
        self.assertEqual(merged.taxable_date.value, d(2026, 9, 30))
        self.assertEqual(merged.supplier_ico.value, "12345679")       # doplněno
        self.assertEqual(merged.issue_date.value, d(2026, 10, 7))     # doplněno
        self.assertEqual(merged.variable_symbol.value, "777")         # platba přebíjí

    def test_plain_sid_number_still_wins(self):
        # Samotná QR Faktura přebíjí číslo z textu jako dřív.
        sid = qr.parse_spayd("SID*1.0*ID:FA-2026-7*AM:10")
        base = P.ExtractionResult(invoice=ExtractedInvoice(), method="pdf-text")
        base.invoice.invoice_number = Field("2026007", 0.85, "llm")
        merged = P._apply_qr(base, sid)
        self.assertEqual(merged.invoice.invoice_number.value, "FA-2026-7")


if __name__ == "__main__":
    unittest.main()
