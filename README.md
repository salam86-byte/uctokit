# uctokit

Frameworkově neutrální jádro business logiky pro zpracování faktur, plateb a
firemních registrů v ČR. **Čistý Python, bez závislosti na web frameworku**
(Django, FastAPI, …) – vstupy a výstupy jsou dataclassy, takže se dá jádro použít
v libovolné aplikaci.

## Moduly

- **`uctokit.invoices`** – extrakce údajů z přijatých faktur: ISDOC (XML),
  text z PDF, QR Platba/Faktura (SPAYD/SID), XLSX, OCR skenů a AI/LLM extrakce.
  Heuristiky, validátory a scoring spolehlivosti.
- **`uctokit.payments`** – Fio bankovní API: parsování výpisů a generování
  tuzemského importu příkazů (importIB XML), IBAN ↔ tuzemský účet.
- **`uctokit.registry`** – ARES (ověření IČO) a MFČR (nespolehlivý plátce DPH,
  zveřejněné bankovní účty).

## Instalace

```bash
pip install uctokit
```

Těžké a systémově závislé části jsou volitelné extras (importy v balíku jsou líné –
funkce se aktivují, jen když si extra nainstaluješ):

```bash
pip install "uctokit[ocr]"          # OCR skenů  – vyžaduje systémový tesseract + poppler
pip install "uctokit[qr-fallback]"  # záložní QR dekodéry – pyzbar vyžaduje systémový zbar
```

Primární čtení QR (`pypdfium2` + `zxing-cpp`) je v jádře a nepotřebuje žádné
systémové knihovny.

## Vývoj a testy

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Testy jsou čistě `unittest`/`pytest` a běží **bez** jakéhokoli web frameworku.
