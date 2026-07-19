"""Parsování Fio bankovního výpisu (frameworkově neutrální).

Rozparsuje JSON výpisu z Fio REST API na seznam jednoduchých dictů. Čistý Python –
zakládání plateb a párování dělá aplikační vrstva nad tímhle výstupem.

Formát: https://www.fio.cz/docs/cz/API_Bankovnictvi.pdf
Sloupce transakce (``column<N>``): 22=ID pohybu, 0=Datum, 1=Objem, 5=VS,
10=Název protiúčtu, 2=Protiúčet, 8=Typ pohybu, 16=Zpráva pro příjemce.
"""

from __future__ import annotations


def _col(txn: dict, n: int):
    cell = txn.get(f"column{n}")
    return cell.get("value") if cell else None


def parse_transactions(data: dict) -> list[dict]:
    """Rozparsuje Fio JSON na seznam jednoduchých dictů."""
    statement = (data or {}).get("accountStatement") or {}
    txn_list = (statement.get("transactionList") or {}).get("transaction") or []
    result = []
    for txn in txn_list:
        raw_date = _col(txn, 0)
        vs = _col(txn, 5)
        result.append(
            {
                "fio_id": str(_col(txn, 22)) if _col(txn, 22) is not None else None,
                "date": str(raw_date)[:10] if raw_date else None,
                "amount": _col(txn, 1),
                "vs": str(vs).strip() if vs not in (None, "") else "",
                "counterparty_name": _col(txn, 10) or "",
                "counterparty_account": str(_col(txn, 2) or ""),
                "message": _col(txn, 16) or "",
                "type": _col(txn, 8) or "",
            }
        )
    return result
