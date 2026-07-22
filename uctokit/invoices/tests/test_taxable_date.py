"""Testy vytěžení DUZP (datum uskutečnění zdanitelného plnění).

DUZP rozhoduje, do kterého období spadne DPH. Účetní systém si ho bez údaje
z faktury doplní datem pořízení, což přes přelom měsíce znamená daň ve
špatném období — proto se čte ze všech zdrojů, které ho nesou.
"""

import unittest
from datetime import date

from uctokit.invoices import heuristics, isdoc, qr
from uctokit.invoices.types import FIELD_NAMES


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


if __name__ == "__main__":
    unittest.main()
