"""Testy vytěžení DUZP (datum uskutečnění zdanitelného plnění).

DUZP rozhoduje, do kterého období spadne DPH. Účetní systém si ho bez údaje
z faktury doplní datem pořízení, což přes přelom měsíce znamená daň ve
špatném období — proto se čte ze všech zdrojů, které ho nesou.
"""

import unittest
from datetime import date

from uctokit.invoices import heuristics, isdoc, qr, scoring
from uctokit.invoices.types import (
    FIELD_NAMES, SOURCE_ISDOC, SOURCE_LLM, ExtractedInvoice, Field,
)


class FieldContractTests(unittest.TestCase):
    def test_taxable_date_is_a_canonical_field(self):
        self.assertIn("taxable_date", FIELD_NAMES)


class IsdocTaxableDateTests(unittest.TestCase):
    def _isdoc(self, body: str) -> bytes:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Invoice xmlns="http://isdoc.cz/namespace/2013" version="6.0.1">'
            f"{body}"
            "</Invoice>"
        ).encode("utf-8")

    def test_tax_point_date_is_read(self):
        inv = isdoc.parse_isdoc(self._isdoc(
            "<ID>2026001</ID>"
            "<IssueDate>2026-06-30</IssueDate>"
            "<TaxPointDate>2026-06-15</TaxPointDate>"
            "<PaymentDueDate>2026-07-30</PaymentDueDate>"
        ))
        self.assertEqual(inv.taxable_date.value, date(2026, 6, 15))
        self.assertEqual(inv.issue_date.value, date(2026, 6, 30))

    def test_missing_tax_point_date_stays_empty(self):
        # Nedoplňovat datem vystavení — prázdno je informace, že ho doklad nemá.
        inv = isdoc.parse_isdoc(self._isdoc(
            "<ID>2026001</ID><IssueDate>2026-06-30</IssueDate>"
        ))
        self.assertFalse(inv.taxable_date.is_present)


class QrTaxableDateTests(unittest.TestCase):
    def test_duzp_from_qr_faktura(self):
        inv = qr.parse_spayd(
            "SID*1.0*ID:2026001*DD:20260630*DUZP:20260615*AM:1210.00*CC:CZK"
        )
        self.assertEqual(inv.taxable_date.value, date(2026, 6, 15))

    def test_dppd_is_used_when_duzp_is_absent(self):
        inv = qr.parse_spayd(
            "SID*1.0*ID:2026001*DD:20260630*DPPD:20260620*AM:1210.00*CC:CZK"
        )
        self.assertEqual(inv.taxable_date.value, date(2026, 6, 20))


class HeuristicTaxableDateTests(unittest.TestCase):
    def _taxable(self, text: str):
        return heuristics.extract_from_text(text).taxable_date.value

    def test_full_wording(self):
        self.assertEqual(
            self._taxable("Datum uskutečnění zdanitelného plnění: 15. 6. 2026"),
            date(2026, 6, 15))

    def test_short_wording(self):
        self.assertEqual(self._taxable("Datum zdanitelného plnění 15.6.2026"),
                         date(2026, 6, 15))

    def test_abbreviation(self):
        self.assertEqual(self._taxable("DUZP: 2026-06-15"), date(2026, 6, 15))

    def test_abbreviated_words(self):
        self.assertEqual(self._taxable("Dat. usk. zdan. plnění 15. 6. 2026"),
                         date(2026, 6, 15))

    def test_wording_used_on_a_real_invoice(self):
        # Přesně takhle to píše jeden z dodavatelů — na tomhle tvaru
        # původní regex selhal.
        self.assertEqual(
            self._taxable("Datum vystavení: 16.07.2026 "
                          "Datum zdanit. plnění: 16.07.2026 IČ: 27800334"),
            date(2026, 7, 16))

    def test_unrelated_fulfilment_wording_is_not_matched(self):
        # „plnění dle smlouvy" není DUZP a nesmí se tak číst.
        self.assertIsNone(self._taxable("Plnění dle smlouvy ze dne 15. 6. 2026"))

    def test_issue_date_is_not_confused_with_taxable_date(self):
        text = "Datum vystavení: 30. 6. 2026\nDUZP: 15. 6. 2026"
        inv = heuristics.extract_from_text(text)
        self.assertEqual(inv.issue_date.value, date(2026, 6, 30))
        self.assertEqual(inv.taxable_date.value, date(2026, 6, 15))



class RealWorldWordingTests(unittest.TestCase):
    """Znění DUZP odpozorovaná na skutečných fakturách.

    Každé z nich se opravdu vyskytlo a na každém původní regex selhal.
    """

    def _taxable(self, text: str):
        return heuristics.extract_from_text(text).taxable_date.value

    def test_without_the_word_zdanitelneho(self):
        # Varianta z praxe: „uskutečnění plnění" bez „zdanitelného".
        self.assertEqual(
            self._taxable("Datum splatnosti: 05.08.2026 "
                          "Datum uskutečnění plnění: 30.06.2026 Forma úhrady"),
            date(2026, 6, 30))

    def test_without_diacritics(self):
        # reca: celá faktura bez diakritiky.
        self.assertEqual(
            self._taxable("Datum uskutecneni zdanitelneho plneni: 31.01.2024"),
            date(2024, 1, 31))

    def test_plain_datum_plneni(self):
        self.assertEqual(self._taxable("Datum plnění 15. 6. 2026"), date(2026, 6, 15))


class TaxPointGroundingTests(unittest.TestCase):
    """Model dopisuje DUZP i tam, kde ho doklad nemá — na to je podlaha."""

    def test_invoice_without_any_mention(self):
        # ELITcar: v celém textu není o plnění ani slovo.
        self.assertFalse(scoring.mentions_tax_point(
            "Datum vystavení : 16.07.2026 Forma úhrady : Převodním příkazem "
            "Datum splatnosti : 26.07.2026"))

    def test_letter_spaced_text_still_counts(self):
        # Königsmark: PDF s proloženými písmeny. Model to přečte správně,
        # takže ho grounding nesmí shodit.
        self.assertTrue(scoring.mentions_tax_point(
            "d at u m u s ku te č n ěn í pl ně n í 20.07.2026"))

    def test_interleaved_columns_still_count(self):
        # Dvousloupcová faktura, kde se nadpisy prolnuly.
        self.assertTrue(scoring.mentions_tax_point(
            "Datum zdanitelného Datum vystavení / plnění / Delivery date: 28.01.2026"))

    def test_model_value_is_dropped_when_unfounded(self):
        inv = ExtractedInvoice(taxable_date=Field(date(2026, 7, 16), 0.7, SOURCE_LLM))
        warnings = scoring.rescore(inv, "Datum vystavení 16.07.2026 Splatnost 26.07.2026")
        self.assertIsNone(inv.taxable_date.value)
        self.assertTrue(any("domyslel" in w for w in warnings))

    def test_model_value_survives_when_the_document_mentions_it(self):
        inv = ExtractedInvoice(taxable_date=Field(date(2026, 1, 28), 0.7, SOURCE_LLM))
        scoring.rescore(inv, "Datum zdanitelného plnění / Delivery date: 28.01.2026")
        self.assertEqual(inv.taxable_date.value, date(2026, 1, 28))

    def test_structural_sources_are_never_dropped(self):
        # ISDOC/QR čtou pole strojově — ta se proti textu neověřují.
        inv = ExtractedInvoice(taxable_date=Field(date(2026, 6, 30), 0.98, SOURCE_ISDOC))
        scoring.rescore(inv, "faktura bez jakékoli zmínky")
        self.assertEqual(inv.taxable_date.value, date(2026, 6, 30))


if __name__ == "__main__":
    unittest.main()
