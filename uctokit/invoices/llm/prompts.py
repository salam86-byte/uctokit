"""Prompty a schéma pro LLM extrakci faktury.

Model dostane text (nebo obrázek) faktury a má vrátit *jen* JSON s poli. Formáty
polí držíme strojové (částka s tečkou, datum ISO), normalizaci pak dělá jádro.
"""

from __future__ import annotations

# Klíče, které od modelu čekáme (shodné s uctokit.invoices.types.FIELD_NAMES).
FIELD_KEYS: tuple[str, ...] = (
    "supplier_name",
    "supplier_ico",
    "supplier_dic",
    "supplier_iban",
    "supplier_account",
    "total_amount",
    "currency",
    "variable_symbol",
    "invoice_number",
    "issue_date",
    "taxable_date",
    "due_date",
)

SYSTEM_PROMPT = (
    "Jsi extraktor údajů z českých přijatých faktur. Ze vstupu vytěž údaje "
    "DODAVATELE (ne odběratele) a vrať POUZE jeden JSON objekt, nic dalšího.\n"
    "Pole a formáty (co nevíš, dej null):\n"
    "- supplier_name: jen NÁZEV dodavatele bez adresy – nedávej do něj ulici, "
    "číslo popisné, město ani PSČ (např. Střední průmyslová škola a Obchodní "
    "akademie, příspěvková organizace – ne ulici Větrná 1809/18 ani město)\n"
    "- supplier_ico: IČO dodavatele, 8 číslic jako řetězec. IČO má JEN česká "
    "(a slovenská) firma. Je-li dodavatel ZAHRANIČNÍ, dej null — nedělej IČO "
    "z PSČ, telefonu ani z čísla objednávky; jeho identifikátor patří do "
    "supplier_dic\n"
    "- supplier_dic: DIČ DODAVATELE (ne odběratele) — kód země a číslo, "
    "např. CZ25194798. Na dokladu jsou DIČ zpravidla dvě, vezmi to ze stejného "
    "bloku jako IČO dodavatele; slovenský dodavatel ho má u popisku 'IČ DPH', "
    "zahraniční u 'VAT'. U zahraničního dodavatele je tohle jediný "
    "identifikátor, který má. Když doklad DIČ neuvádí, dej null\n"
    "- supplier_iban: IBAN účtu dodavatele bez mezer, nebo null\n"
    "- supplier_account: číslo účtu ve tvaru cislo/kodbanky, nebo null\n"
    "- total_amount: celková částka k úhradě jako číslo s desetinnou tečkou "
    "(např. 13000.00), bez měny a mezer\n"
    "- currency: měna, třípísmenný kód (např. CZK)\n"
    "- variable_symbol: variabilní symbol, jen číslice\n"
    "- invoice_number: číslo faktury (řetězec)\n"
    "- issue_date: datum vystavení ve formátu YYYY-MM-DD\n"
    "- taxable_date: datum uskutečnění zdanitelného plnění ve formátu "
    "YYYY-MM-DD. Je to SAMOSTATNÝ řádek dokladu s vlastním popiskem: DUZP, "
    "„Datum zdanitelného plnění“, „Datum uskutečnění plnění“, „Datum UZP“ "
    "nebo zkráceně („Datum usk. zd. plnění“) – popisek bývá i oříznutý nebo "
    "bez diakritiky. "
    "Vezmi datum z TOHOTO řádku, nikdy ne z řádku „Datum vystavení“; ta dvě "
    "data se běžně liší (u služeb bývá DUZP poslední den měsíce, za který se "
    "fakturuje). Když doklad takový řádek nemá, dej null\n"
    "- due_date: datum splatnosti ve formátu YYYY-MM-DD\n"
    "Text z PDF může mít promíchané levé a pravé sloupce. Vždy rozliš "
    "sekci Dodavatel od Odběratel/Plátce a neber údaje našeho klubu z odběratele. "
    "Dodavatel je ta strana, na jejíž bankovní účet se platí (peníze jdou jemu); "
    "odběratel/plátce je náš klub – jeho název, IČO ani adresu nikam nedávej. "
    # KB SmartPay / Worldline (v0.8.1): odběratel má adresu velkým nahoře
    # a jeho IČO/DIČ stojí u čísla faktury s popiskem „zákazníka";
    # dodavatel je jen v patičce malým písmem a v hlavičce má LOGO.
    "Popisky „IČO zákazníka“, „DIČ zákazníka“, „Číslo zákazníka“, „Odběratel“, "
    "„Plátce“, „Kupující“ označují ODBĚRATELE – ty hodnoty (ani firmu s adresou "
    "nad nimi) nikdy nedávej do supplier_*. Dodavatel (vystavitel) bývá u bank, "
    "platebních služeb a telekomunikací uveden JEN V PATIČCE malým písmem: "
    "obchodní firma, IČ, DIČ, zápis v rejstříku, účet – hledej ho tam, když "
    "v hlavičce žádný blok Dodavatel není. Logo nebo obchodní značka v hlavičce "
    "(např. „KB SmartPay“) NENÍ název dodavatele; použij právní název od IČ. "
    "Datum vystavení nezaměň s datem v patě, rejstříku ani s DUZP — to patří "
    "do taxable_date. U XLSX mohou být číslo účtu a kód banky v oddělených "
    "buňkách.\n"
    "Nevymýšlej hodnoty. Vrať čistý JSON bez markdown ohraničení."
)

_USER_TEXT_TEMPLATE = (
    "Text faktury (mezi značkami):\n"
    "<<<\n{text}\n>>>\n"
    "Vrať JSON s poli: " + ", ".join(FIELD_KEYS) + "."
)

VISION_USER = (
    "Na obrázcích je přijatá faktura. Přečti ji a vrať JSON s poli: "
    + ", ".join(FIELD_KEYS)
    + ". Co nepřečteš, dej null."
)


def build_user_text(text: str, *, max_chars: int = 12000) -> str:
    """Sestaví uživatelskou zprávu z textu faktury (ořízne přebytek)."""
    snippet = (text or "")[:max_chars]
    return _USER_TEXT_TEMPLATE.format(text=snippet)
