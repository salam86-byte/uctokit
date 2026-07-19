"""Testy normalizace a validace (čistý Python, bez Djanga)."""

import unittest
from datetime import date
from decimal import Decimal

from uctokit.invoices import validators as V


class NormalizeAmountTests(unittest.TestCase):
    def test_czech_space_and_comma(self):
        self.assertEqual(V.normalize_amount("1 234,50"), Decimal("1234.50"))

    def test_thousands_dot_decimal_comma(self):
        self.assertEqual(V.normalize_amount("1.234,50"), Decimal("1234.50"))

    def test_machine_dot(self):
        self.assertEqual(V.normalize_amount("13000.00"), Decimal("13000.00"))

    def test_plain_number(self):
        self.assertEqual(V.normalize_amount(5000), Decimal("5000.00"))

    def test_garbage_is_none(self):
        self.assertIsNone(V.normalize_amount("abc"))
        self.assertIsNone(V.normalize_amount(None))


class NormalizeDateTests(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(V.normalize_date("2025-09-01"), date(2025, 9, 1))

    def test_czech_two_digit_year(self):
        self.assertEqual(V.normalize_date("08.06.26"), date(2026, 6, 8))

    def test_czech(self):
        self.assertEqual(V.normalize_date("1. 9. 2025"), date(2025, 9, 1))

    def test_invalid_calendar_day(self):
        self.assertIsNone(V.normalize_date("2025-13-40"))


class IcoTests(unittest.TestCase):
    def test_valid_checksum(self):
        self.assertTrue(V.valid_ico("12345679"))

    def test_invalid_checksum(self):
        self.assertFalse(V.valid_ico("12345678"))

    def test_normalize_pads(self):
        self.assertEqual(V.normalize_ico("1234"), "00001234")


class IbanTests(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(V.valid_iban("CZ9708000000191111111111"))

    def test_valid_with_spaces(self):
        self.assertTrue(V.valid_iban("CZ97 0800 0000 1911 1111 1111"))

    def test_bad_checksum(self):
        self.assertFalse(V.valid_iban("CZ0000000000000000000000"))

    def test_wrong_length_for_cz(self):
        self.assertFalse(V.valid_iban("CZ6508000000"))


class TextChecksTests(unittest.TestCase):
    def test_amount_in_text_grouped(self):
        self.assertTrue(V.amount_in_text(Decimal("13000.00"), "Celkem 13 000,00 Kč"))

    def test_amount_in_text_plain(self):
        self.assertTrue(V.amount_in_text(Decimal("5000.00"), "k úhradě 5000.00"))

    def test_amount_not_in_text(self):
        self.assertFalse(V.amount_in_text(Decimal("999.00"), "Celkem 13 000,00 Kč"))

    def test_token_in_text(self):
        self.assertTrue(V.token_in_text("55667788", "VS 55667788 prosím"))
        self.assertFalse(V.token_in_text("55667788", "VS 12345678"))


if __name__ == "__main__":
    unittest.main()
