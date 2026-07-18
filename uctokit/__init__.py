"""uctokit – frameworkově neutrální knihovna sdílené business logiky.

Čistý Python bez závislosti na Djangu. Vstup/výstup přes dataclassy, aby se
jádro dalo použít i mimo tento projekt (FastAPI, jiné Django appky). První
modul: :mod:`uctokit.invoices` – extrakce údajů z přijatých faktur.
"""

__all__ = ["invoices"]
