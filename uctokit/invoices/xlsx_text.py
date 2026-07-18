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
            for row in ws.iter_rows(values_only=True):
                cells = [_fmt(c) for c in row if c is not None and str(c).strip() != ""]
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


def _fmt(value) -> str:
    """Buňka na text – datum/čas bez času navíc, čísla bez zbytečných nul."""
    from datetime import date, datetime

    if isinstance(value, datetime):
        return value.date().isoformat() if value.time().hour == 0 and value.time().minute == 0 else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
