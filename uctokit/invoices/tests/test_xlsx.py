"""Testy extrakce z tabulkové faktury (XLSX)."""

import io
import unittest

from uctokit.invoices import pipeline as P
from uctokit.invoices import xlsx_text
from uctokit.invoices.types import SourceDocument


def _xlsx(rows) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class XlsxTextTests(unittest.TestCase):
    def test_flatten_cells_to_text(self):
        data = _xlsx([
            ["Faktura č.", "2026-77"],
            ["IČO:", "12345679"],
            ["Celkem k úhradě", "12 100,00 Kč"],
        ])
        text = xlsx_text.extract_text(data)
        for token in ("2026-77", "12345679", "12 100,00"):
            self.assertIn(token, text)

    def test_empty_on_garbage(self):
        self.assertEqual(xlsx_text.extract_text(b"nonsense"), "")


class XlsxPipelineTests(unittest.TestCase):
    def test_routes_through_xlsx_and_heuristics(self):
        data = _xlsx([
            ["Dodavatel: Test s.r.o., IČO: 12345679"],
            ["Variabilní symbol 202677"],
            ["Celkem k úhradě: 12 100,00 Kč"],
        ])
        doc = SourceDocument(content=data, filename="faktura.xlsx")
        res = P.extract(doc)  # bez LLM – jen heuristiky nad zploštěným textem
        self.assertEqual(res.method, "xlsx")
        self.assertEqual(res.invoice.supplier_ico.value, "12345679")
        self.assertEqual(res.invoice.variable_symbol.value, "202677")


if __name__ == "__main__":
    unittest.main()
