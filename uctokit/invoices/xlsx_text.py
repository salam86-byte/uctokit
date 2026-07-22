"""Převod tabulkové faktury (XLSX) na text pro heuristiky + LLM.

Excel faktury nemají pevný layout, takže sešit jen „zploštíme" na text (řádek po
řádku, přes všechny listy) a poženeme ho stejnou textovou cestou jako PDF text.
openpyxl se importuje **líně** – když chybí, vrátíme prázdno a pipeline spadne
zpět (žádná tvrdá závislost; openpyxl už je v projektu kvůli importu členů).
"""

from __future__ import annotations

import io


def extract_text(content: bytes, *, max_rows: int = 400) -> str:
    """Vrátí text sešitu (buňky oddělené tabem, listy nadpisem), nebo ``""``."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return ""
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        return ""
    lines: list[str] = []
    try:
        for ws in wb.worksheets:
            if len(wb.worksheets) > 1:
                lines.append(f"# List: {ws.title}")
            rows = 0
            for row in ws.iter_rows():
                cells = [
                    _fmt(c.value, number_format=c.number_format)
                    for c in row if c.value is not None and str(c.value).strip() != ""
                ]
                if cells:
                    lines.append("\t".join(cells))
                rows += 1
                if rows >= max_rows:
                    break
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return "\n".join(lines)


def _fmt(value, *, number_format: str = "") -> str:
    """Buňka na text – datum/čas bez času navíc, čísla bez zbytečných nul."""
    from datetime import date, datetime

    if isinstance(value, datetime):
        return value.date().isoformat() if value.time().hour == 0 and value.time().minute == 0 else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and _format_has_decimals(number_format):
        return f"{value:.2f}"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _format_has_decimals(number_format: str) -> bool:
    """Pozná, že zobrazení buňky vyžaduje alespoň dvě desetinná místa."""
    import re

    fmt = re.sub(r'"[^"]*"', "", number_format or "")
    return bool(re.search(r"[.,]0{2,}", fmt))


def extract_text_xls(content: bytes, *, max_rows: int = 400) -> str:
    """Totéž pro STARÝ binární `.xls` (Excel 97–2003, OLE2).

    openpyxl umí jen `.xlsx`, takže se sáhne po `xlrd` (moderní verze čte
    naopak výhradně `.xls`). Import je líný — bez knihovny se vrátí prázdno
    a pipeline spadne zpět, žádná tvrdá závislost.

    Datum je v `.xls` uložené jako pořadové číslo (17. 1. 2026 = 46039).
    Bez převodu by se do textu dostalo „46039" a extrakce by z toho datum
    nikdy nedostala — proto se datové buňky poznají podle typu a přeloží.
    """
    try:
        import xlrd
    except ImportError:
        return ""
    try:
        wb = xlrd.open_workbook(file_contents=content)
    except Exception:
        return ""

    lines: list[str] = []
    for sheet in wb.sheets():
        if wb.nsheets > 1:
            lines.append(f"# List: {sheet.name}")
        for row_idx in range(min(sheet.nrows, max_rows)):
            cells = []
            for col_idx in range(sheet.ncols):
                cell = sheet.cell(row_idx, col_idx)
                text = _fmt_xls(cell, wb.datemode, xlrd)
                if text:
                    cells.append(text)
            if cells:
                lines.append("\t".join(cells))
    return "\n".join(lines)


def _fmt_xls(cell, datemode, xlrd) -> str:
    """Buňka `.xls` na text — datum z pořadového čísla, čísla bez koncových nul."""
    from datetime import date, datetime

    if cell.ctype == xlrd.XL_CELL_EMPTY:
        return ""
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            y, m, d, hh, mm, ss = xlrd.xldate_as_tuple(cell.value, datemode)
        except Exception:
            return str(cell.value).strip()
        if (hh, mm, ss) == (0, 0, 0):
            return date(y, m, d).isoformat()
        return datetime(y, m, d, hh, mm, ss).isoformat(sep=" ")
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return "ano" if cell.value else "ne"
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        value = float(cell.value)
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(cell.value).strip()
