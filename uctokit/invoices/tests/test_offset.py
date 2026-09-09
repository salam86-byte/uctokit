"""Čtení zápočtu pohledávek a závazků.

Vzorová data: fiktivní firmy a IČO (stejně jako ve zbytku testů), tvar
sestavy odpovídá tiskovému výstupu ABRA Flexi.
"""

import unittest
from datetime import date
from decimal import Decimal

from uctokit.invoices.offset import (
    OffsetStatement,
    looks_like_offset,
    parse_offset_statement,
)

# IČO musí projít kontrolní číslicí, jinak se do hlavičky nedostanou.
ISSUER_ICO = "25596641"      # „Dodavatel s.r.o."
COUNTERPARTY_ICO = "27604977"  # „Odběratel s.r.o."

SAMPLE = f"""Jednostranný zápočet pohledávek a závazků
podle §1982 Občanského zákoníku
Číslo zápočtu: 2026104319
Dodavatel s.r.o. Odběratel s.r.o.
Ulice 1 Náměstí 2
60200 Brno 79201 Bruntál
Česká republika Česká republika
IČO: {ISSUER_ICO} IČO: {COUNTERPARTY_ICO}
DIČ: CZ{ISSUER_ICO} DIČ: CZ{COUNTERPARTY_ICO}
V Brně dne 08.09.2026.
Sdělujeme Vám, že ke dni 08.09.2026 započítáváme níže uvedené pohledávky a závazky:
závazek firmy Dodavatel s.r.o. vůči firmě Odběratel s.r.o.:
Doklad Vystaveno Splatnost VS Měna Fakturováno Započteno Zbývá uhradit
SB-03207/2026 07.09.2026 21.09.2026 1117626081 CZK 25 871,54 25 871,54 0,00
Celkem započteno: 25 871,54
pohledávka firmy Dodavatel s.r.o. za firmu Odběratel s.r.o.:
Doklad Vystaveno Splatnost VS Měna Fakturováno Započteno Zbývá uhradit
2026104319 07.09.2026 21.09.2026 2026104319 CZK 134 775,43 25 871,54 108 903,89
Celkem započteno: 25 871,54
S pozdravem
za Dodavatel s.r.o.
"""


class LooksLikeOffsetTests(unittest.TestCase):
    def test_recognizes_the_usual_headings(self):
        for text in (
            "Jednostranný zápočet pohledávek a závazků",
            "JEDNOSTRANNÝ ZÁPOČET POHLEDÁVEK",
            "Dohoda o vzájemném zápočtu pohledávek",
            "Jednostranny zapocet pohledavek",   # bez diakritiky (OCR)
        ):
            self.assertTrue(looks_like_offset(text), text)

    def test_ordinary_invoice_is_not_an_offset(self):
        self.assertFalse(looks_like_offset("Faktura - daňový doklad 2026104319"))
        self.assertFalse(looks_like_offset(""))


class ParseOffsetStatementTests(unittest.TestCase):
    def setUp(self):
        self.stmt = parse_offset_statement(SAMPLE)
        self.assertIsInstance(self.stmt, OffsetStatement)

    def test_header(self):
        self.assertEqual(self.stmt.number, "2026104319")
        self.assertEqual(self.stmt.date, date(2026, 9, 8))
        self.assertEqual(self.stmt.total_offset, Decimal("25871.54"))

    def test_sides_come_from_the_headings_not_the_order(self):
        """„Pohledávka firmy X" = doklady vystavitele, „závazek" = protistrany."""
        self.assertEqual([l.document for l in self.stmt.issuer_claims], ["2026104319"])
        self.assertEqual(
            [l.document for l in self.stmt.counterparty_claims], ["SB-03207/2026"]
        )

    def test_amounts_of_the_offset_invoice(self):
        line = self.stmt.issuer_claims[0]
        self.assertEqual(line.invoiced, Decimal("134775.43"))
        self.assertEqual(line.offset, Decimal("25871.54"))
        self.assertEqual(line.remaining, Decimal("108903.89"))
        self.assertFalse(line.fully_offset)

    def test_row_details(self):
        line = self.stmt.issuer_claims[0]
        self.assertEqual(line.issued, date(2026, 9, 7))
        self.assertEqual(line.due, date(2026, 9, 21))
        self.assertEqual(line.vs, "2026104319")
        self.assertEqual(line.currency, "CZK")

    def test_our_invoice_is_consumed_whole(self):
        line = self.stmt.counterparty_claims[0]
        self.assertEqual(line.invoiced, Decimal("25871.54"))
        self.assertEqual(line.remaining, Decimal("0.00"))
        self.assertTrue(line.fully_offset)
        self.assertEqual(line.vs, "1117626081")

    def test_company_names_keep_diacritics(self):
        self.assertEqual(self.stmt.issuer_name, "Dodavatel s.r.o.")
        self.assertEqual(self.stmt.counterparty_name, "Odběratel s.r.o.")

    def test_icos_in_order_of_appearance(self):
        self.assertEqual(self.stmt.issuer_ico, ISSUER_ICO)
        self.assertEqual(self.stmt.counterparty_ico, COUNTERPARTY_ICO)

    def test_lookup_by_document_number(self):
        self.assertEqual(self.stmt.remaining_for("2026104319"), Decimal("108903.89"))
        self.assertEqual(self.stmt.offset_for("2026104319"), Decimal("25871.54"))
        self.assertEqual(self.stmt.remaining_for("SB-03207/2026"), Decimal("0.00"))

    def test_lookup_ignores_separators(self):
        """Doklad zapsaný jinak je pořád tentýž doklad."""
        self.assertEqual(self.stmt.remaining_for("SB 03207 / 2026"), Decimal("0.00"))

    def test_lookup_by_variable_symbol(self):
        self.assertEqual(self.stmt.remaining_for("1117626081"), Decimal("0.00"))

    def test_unknown_document_is_none_not_a_guess(self):
        self.assertIsNone(self.stmt.remaining_for("FV999/2026"))
        self.assertIsNone(self.stmt.remaining_for(""))


class RobustnessTests(unittest.TestCase):
    def test_not_an_offset_returns_none(self):
        self.assertIsNone(parse_offset_statement("Faktura - daňový doklad 123"))

    def test_offset_without_readable_sections_returns_none(self):
        """Radši nic než odečíst částku od špatného dokladu."""
        text = ("Jednostranný zápočet pohledávek a závazků\n"
                "Číslo zápočtu: 5\n"
                "Nějaký text bez tabulky.\n")
        self.assertIsNone(parse_offset_statement(text))

    def test_several_invoices_on_one_side(self):
        text = """Jednostranný zápočet pohledávek a závazků
pohledávka firmy Dodavatel s.r.o. za firmu Odběratel s.r.o.:
Doklad Vystaveno Splatnost VS Měna Fakturováno Započteno Zbývá uhradit
FV1/2026 01.08.2026 15.08.2026 12345 CZK 1 000,00 400,00 600,00
FV2/2026 02.08.2026 16.08.2026 12346 CZK 2 500,50 2 500,50 0,00
Celkem započteno: 2 900,50
"""
        stmt = parse_offset_statement(text)
        self.assertEqual(len(stmt.issuer_claims), 2)
        self.assertEqual(stmt.remaining_for("FV1/2026"), Decimal("600.00"))
        self.assertEqual(stmt.remaining_for("FV2/2026"), Decimal("0.00"))
        self.assertEqual(stmt.total_offset, Decimal("2900.50"))

    def test_nonbreaking_spaces_in_amounts(self):
        text = ("Jednostranný zápočet pohledávek\n"
                "pohledávka firmy A s.r.o. za firmu B s.r.o.:\n"
                "FV1/2026 01.08.2026 15.08.2026 12345 CZK 1 234,50 234,50 1 000,00\n")
        stmt = parse_offset_statement(text)
        self.assertEqual(stmt.remaining_for("FV1/2026"), Decimal("1000.00"))

    def test_missing_remaining_column_is_computed(self):
        text = ("Jednostranný zápočet pohledávek\n"
                "pohledávka firmy A s.r.o. za firmu B s.r.o.:\n"
                "FV1/2026 01.08.2026 15.08.2026 12345 CZK 1 000,00 400,00\n")
        stmt = parse_offset_statement(text)
        self.assertEqual(stmt.remaining_for("FV1/2026"), Decimal("600.00"))

    def test_total_falls_back_to_the_sum_of_rows(self):
        text = ("Jednostranný zápočet pohledávek\n"
                "pohledávka firmy A s.r.o. za firmu B s.r.o.:\n"
                "FV1/2026 01.08.2026 15.08.2026 12345 CZK 1 000,00 400,00 600,00\n"
                "FV2/2026 01.08.2026 15.08.2026 12346 CZK 1 000,00 100,00 900,00\n")
        stmt = parse_offset_statement(text)
        self.assertEqual(stmt.total_offset, Decimal("500.00"))

    def test_table_header_and_totals_are_not_rows(self):
        stmt = parse_offset_statement(SAMPLE)
        self.assertEqual(len(stmt.lines), 2)

    def test_address_block_before_the_sections_is_ignored(self):
        """Adresní blok nese datum i čísla, ale řádek tabulky to není."""
        stmt = parse_offset_statement(SAMPLE)
        self.assertNotIn("V", [l.document for l in stmt.lines])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
