"""Celková jistota (v0.11.0): zahraniční dodavatel bez IČO a VS.

Jistota je průměr pěti klíčových polí a chybějící pole se počítá jako
nula. Německá faktura IČO ani variabilní symbol nemá — a tak i dokonale
vytěžená končila na 59 % (3 × 0,99 / 5): pod prahem kontroly i pod prahem
vision fallbacku, který se kvůli tomu spouštěl pokaždé zbytečně (reichelt,
Luděk 21. 9. 2026). Stejná výjimka, jakou má hotovostní doklad, teď platí
i pro dodavatele s cizím DIČ. Tuzemský doklad se počítá přesně jako dřív.
"""
import datetime
import unittest
from decimal import Decimal

from uctokit.invoices.scoring import (KEY_FIELDS, key_fields, overall_confidence,
                                      overall_from_confidences, rescore)
from uctokit.invoices.types import SOURCE_LLM, ExtractedInvoice, Field
from uctokit.invoices.validators import dic_country


def _f(value, conf=0.95):
    return Field(value, conf, SOURCE_LLM)


def _german():
    inv = ExtractedInvoice()
    inv.supplier_name = _f("reichelt elektronik GmbH & Co. KG")
    inv.supplier_dic = _f("DE216817039")
    inv.total_amount = _f(Decimal("781.70"))
    inv.issue_date = _f(datetime.date(2026, 9, 20))
    inv.due_date = _f(datetime.date(2026, 10, 20))
    inv.invoice_number = _f("I-670212")
    return inv


def _czech():
    inv = ExtractedInvoice()
    inv.supplier_name = _f("Tuzemský dodavatel s.r.o.")
    inv.supplier_ico = _f("25194798")
    inv.supplier_dic = _f("CZ25194798")
    inv.total_amount = _f(Decimal("781.70"))
    inv.variable_symbol = _f("2617989")
    inv.due_date = _f(datetime.date(2026, 10, 20))
    return inv


class DicCountryTests(unittest.TestCase):
    def test_reads_prefix(self):
        self.assertEqual(dic_country("DE216817039"), "DE")
        self.assertEqual(dic_country(" de 216 817 039 "), "DE")
        self.assertEqual(dic_country("PL 526-373-68-24"), "PL")
        self.assertEqual(dic_country("CZ25851187"), "CZ")
        self.assertEqual(dic_country("ATU12345678"), "AT")

    def test_no_prefix_is_unknown(self):
        for raw in ("", None, "25851187", "CZ", "jen text", "JENOMTEXTU", "1234CZ"):
            self.assertEqual(dic_country(raw), "", repr(raw))


class ForeignSupplierTests(unittest.TestCase):
    def test_by_dic_country(self):
        self.assertTrue(_german().foreign_supplier)
        self.assertFalse(_czech().foreign_supplier)

    def test_without_dic_counts_as_domestic(self):
        inv = _german()
        inv.supplier_dic = Field()
        self.assertFalse(inv.foreign_supplier)


class KeyFieldsTests(unittest.TestCase):
    def test_default(self):
        self.assertEqual(key_fields(), KEY_FIELDS)

    def test_cash_drops_vs_and_due(self):
        self.assertEqual(key_fields(cash=True),
                         ("supplier_name", "supplier_ico", "total_amount"))

    def test_foreign_drops_ico_and_vs(self):
        self.assertEqual(key_fields(foreign=True),
                         ("supplier_name", "total_amount", "due_date"))

    def test_foreign_cash(self):
        self.assertEqual(key_fields(cash=True, foreign=True),
                         ("supplier_name", "total_amount"))


class OverallConfidenceTests(unittest.TestCase):
    def test_german_scores_like_czech(self):
        de, cz = _german(), _czech()
        rescore(de, "reichelt elektronik 781,70 EUR 20.10.2026 DE 216817039")
        rescore(cz, "ICO 25194798 VS 2617989 celkem 781,70 splatnost 20.10.2026")
        self.assertGreaterEqual(overall_confidence(de), 0.9)
        self.assertAlmostEqual(overall_confidence(de), overall_confidence(cz), delta=0.05)

    def test_same_invoice_without_dic_is_still_penalised(self):
        """Bez DIČ nevíme, odkud dodavatel je — chybějící IČO a VS se počítají."""
        inv = _german()
        inv.supplier_dic = Field()
        rescore(inv, None)
        self.assertLess(overall_confidence(inv), 0.6)

    def test_czech_formula_unchanged(self):
        """Tuzemský doklad: průměr pěti polí, chybějící jako nula."""
        inv = _czech()
        inv.variable_symbol = Field()
        rescore(inv, None)
        conf = {n: f.confidence for n, f in inv.items()}
        self.assertEqual(overall_confidence(inv),
                         round(sum(conf[k] for k in KEY_FIELDS) / 5, 3))

    def test_missing_fields_still_drag_a_foreign_invoice_down(self):
        """Výjimka je jen na IČO a VS — bez částky je cizí doklad pořád nejistý."""
        inv = _german()
        inv.total_amount = Field()
        rescore(inv, None)
        self.assertLess(overall_confidence(inv), 0.7)

    def test_dict_form_matches_object_form(self):
        de = _german()
        rescore(de, None)
        self.assertEqual(overall_from_confidences(de.confidences(), foreign=True),
                         overall_confidence(de))
        self.assertEqual(overall_from_confidences({"supplier_name": 1.0}), 0.2)
        self.assertEqual(overall_from_confidences({}), 0.0)
        self.assertEqual(overall_from_confidences(
            {"supplier_name": 0.9, "supplier_ico": 0.9, "total_amount": 0.9}, cash=True), 0.9)
