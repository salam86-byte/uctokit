"""Dodavatel jen v patičce, odběratel v hlavičce s popiskem „zákazníka" (v0.8.1).

KB SmartPay (Worldline Czech Republic s.r.o., faktura 3260184122, MX-NET
15. 9. 2026): nahoře velkým adresa ODBĚRATELE bez nadpisu, u čísla faktury
„DIČ zákazníka : CZ27800334" a „IČO zákazníka : 27800334", v hlavičce logo
„KB SmartPay"; dodavatel jen v patičce malým písmem s „IČ: 03633144",
„DIČ: CZ699001182" a účtem. HUB dodavatele „nenačetl" — model vzal blok
odběratele.
"""
import unittest

from uctokit.invoices import heuristics, pipeline
from uctokit.invoices.llm.prompts import SYSTEM_PROMPT
from uctokit.invoices.types import ExtractedInvoice, Field

KB_SMARTPAY = """MX-NET Telekomunikace s.r.o.
Větrná 1809/18
792 01 Bruntál
Czech Republic

Číslo faktury : 3260184122
Datum vystavení : 11.09.2026
Datum zdanitelného plnění : 31.08.2026
DIČ zákazníka : CZ27800334
Číslo zákazníka : 628323
IČO zákazníka : 27800334
Číslo objednávky :
Strana : 1/1

Fakturační období      TID      Období      Množství   Jednotka   Cena/j.   Celkem CZK  DPH
01.08.2026-31.08.2026
Materiálu rental
Formule : YOXIMO 3G FLEX (CZ)   01137762   03.08-31.08   1   MO   320,00   299,35   21
Telekomunikace
FEE 3G (CZ)   01137762   1   MO   150,00   150,00   21
Součet BEZ DPH   449,35

Datum splatnosti
11.10.2026
Rozpis DPH   Cena bez DPH   DPH
21: DPH 21%   449,35   94,36
Variabilní symbol
3260184122
Celkem s DPH   543,71 CZK
Prosím, celkovou částku uhraďte bankovním převodem do 11.10.2026, na náš účet
č. 107-4649060217/0100, použijte variabilní symbol 3260184122

Worldline Czech Republic s.r.o.
Rohanské nábřeží 670/17, 186 00 Praha 8
Česká republika
IČ: 03633144
DIČ: CZ699001182
účet č. 107-4649060217/0100
Společnost zapsána v obchodním rejstříku vedeném Městským soudem v Praze, spisová značka C 235160
Worldline Czech Republic s.r.o. používá obchodní označení KB SmartPay
"""


class CustomerLabelTests(unittest.TestCase):
    def test_customer_icos_are_recognised(self):
        self.assertEqual(heuristics.customer_icos(KB_SMARTPAY), {"27800334"})
        self.assertEqual(heuristics.customer_icos("IČ odběratele: 25194798"), {"25194798"})
        self.assertEqual(heuristics.customer_icos("ICO zakaznika 27800334"), {"27800334"})
        self.assertEqual(heuristics.customer_icos("IČO: 27800334"), set())

    def test_labeled_icos_skip_the_customer(self):
        self.assertEqual(heuristics.labeled_icos(KB_SMARTPAY), ["03633144"])

    def test_heuristics_take_the_footer_supplier(self):
        inv = heuristics.extract_from_text(KB_SMARTPAY)
        self.assertEqual(inv.supplier_ico.value, "03633144")
        self.assertEqual(inv.supplier_dic.value, "CZ699001182")
        self.assertEqual(inv.supplier_account.value, "107-4649060217/0100")
        self.assertEqual(inv.variable_symbol.value, "3260184122")

    def test_customer_ico_is_never_the_supplier_even_when_first(self):
        text = "IČO zákazníka: 27800334\nIČO: 03633144"
        self.assertEqual(heuristics.extract_from_text(text).supplier_ico.value, "03633144")


class MergeRejectsCustomerIdentityTests(unittest.TestCase):
    def _llm(self, **pole):
        inv = ExtractedInvoice()
        for k, v in pole.items():
            setattr(inv, k, Field(v, 0.9, "llm"))
        return inv

    def test_llm_ico_of_the_customer_is_replaced_by_heuristics(self):
        llm = self._llm(supplier_name="MX-NET Telekomunikace s.r.o.",
                        supplier_ico="27800334", supplier_dic="CZ27800334",
                        total_amount="543.71")
        heur = heuristics.extract_from_text(KB_SMARTPAY)
        merged, warnings = pipeline._merge(llm, heur, text=KB_SMARTPAY)
        self.assertEqual(merged.supplier_ico.value, "03633144")
        self.assertEqual(merged.supplier_dic.value, "CZ699001182")
        # Název z téhož (odběratelova) bloku je pryč — doplní ho rejstřík.
        self.assertFalse(merged.supplier_name.is_present)
        self.assertEqual(merged.total_amount.value, "543.71")
        self.assertTrue(any("odběratele" in w for w in warnings), warnings)

    def test_customer_dic_alone_is_enough_to_reject(self):
        llm = self._llm(supplier_name="MX-NET Telekomunikace s.r.o.",
                        supplier_dic="CZ27800334")
        heur = heuristics.extract_from_text(KB_SMARTPAY)
        merged, _ = pipeline._merge(llm, heur, text=KB_SMARTPAY)
        self.assertEqual(merged.supplier_ico.value, "03633144")
        self.assertFalse(merged.supplier_name.is_present)

    def test_supplier_from_the_model_survives_without_customer_labels(self):
        llm = self._llm(supplier_name="Discomp s.r.o.", supplier_ico="25236792")
        heur = heuristics.extract_from_text("IČO: 25236792\nDIČ: CZ25236792")
        merged, warnings = pipeline._merge(llm, heur, text="IČO: 25236792")
        self.assertEqual(merged.supplier_name.value, "Discomp s.r.o.")
        self.assertEqual(merged.supplier_ico.value, "25236792")
        self.assertFalse(any("odběratele" in w for w in warnings), warnings)

    def test_merge_without_text_behaves_as_before(self):
        llm = self._llm(supplier_ico="27800334")
        merged, _ = pipeline._merge(llm, ExtractedInvoice())
        self.assertEqual(merged.supplier_ico.value, "27800334")


class PromptTests(unittest.TestCase):
    def test_prompt_explains_footers_and_customer_labels(self):
        self.assertIn("IČO zákazníka", SYSTEM_PROMPT)
        self.assertIn("PATIČCE", SYSTEM_PROMPT)
        self.assertIn("KB SmartPay", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
