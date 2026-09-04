"""DIČ dodavatele (v0.5.3).

Na párování se NEPOUŽÍVÁ — klíčem zůstává IČO. Cenu má jako křížová
kontrola: české DIČ právnické osoby je „CZ" + IČO, takže rozpor mezi nimi
na jednom dokladu je levný signál překlepu i podvrhu. A u zahraničního
plátce bez českého IČO je to jediný daňový identifikátor na dokladu.

Hlavní úskalí: DIČ jsou na faktuře DVĚ — dodavatele a naše.
"""
import unittest

from uctokit.invoices import heuristics, isdoc, validators as V


class NormalizeDicTests(unittest.TestCase):
    def test_uppercases_and_strips(self):
        self.assertEqual(V.normalize_dic(" cz 251 947 98 "), "CZ25194798")

    def test_keeps_foreign_shapes(self):
        self.assertEqual(V.normalize_dic("SK2020317068"), "SK2020317068")
        self.assertEqual(V.normalize_dic("ATU12345678"), "ATU12345678")

    def test_rejects_nonsense(self):
        for raw in ("", None, "12345678", "CZ", "jen text"):
            self.assertEqual(V.normalize_dic(raw), "", repr(raw))

    def test_does_not_pad_like_ico(self):
        """IČO se doplňuje nulami na osm; DIČ se doplnit NESMÍ — u fyzické
        osoby je to rodné číslo a nula by z platného čísla udělala neplatné."""
        self.assertEqual(V.normalize_dic("CZ7001011234"), "CZ7001011234")


class HeuristicDicTests(unittest.TestCase):
    def _dic(self, text):
        return heuristics.extract_from_text(text).supplier_dic.value

    def test_simple(self):
        self.assertEqual(self._dic("DIČ: CZ25194798"), "CZ25194798")

    def test_without_diacritics(self):
        self.assertEqual(self._dic("DIC: CZ25194798"), "CZ25194798")

    def test_missing_stays_empty(self):
        self.assertIsNone(self._dic("Faktura c. 123, castka 1000 Kc"))

    def test_supplier_wins_over_customer(self):
        """Ostrý tvar z PPL faktury 3260915247: obě strany mají DIČ a to
        naše (CZ27800334) se do dokladu vzít NESMÍ."""
        text = ("Dodavatel:\nPPL CZ s.r.o.\nIČO: 25194798\nDIČ: CZ25194798\n"
                "Číslo účtu: 522821041\nKód banky: 2700\n"
                "Odběratel:\nMX-NET Telekomunikace s.r.o.\n"
                "IČO: 27800334\nDIČ: CZ27800334\n")
        inv = heuristics.extract_from_text(text)
        self.assertEqual(inv.supplier_ico.value, "25194798")
        self.assertEqual(inv.supplier_dic.value, "CZ25194798")

    def test_customer_first_layout(self):
        """Když je odběratel NAHOŘE, „první DIČ“ je to naše — proto se
        kotví na už vybrané IČO dodavatele, ne na pořadí."""
        text = ("Odběratel:\nMX-NET Telekomunikace s.r.o.\n"
                "IČO: 27800334\nDIČ: CZ27800334\n"
                "Dodavatel:\nPPL CZ s.r.o.\nIČO: 25194798\nDIČ: CZ25194798\n"
                "Číslo účtu: 522821041/2700\n")
        inv = heuristics.extract_from_text(text)
        self.assertEqual(inv.supplier_dic.value, "CZ25194798")


class IsdocDicTests(unittest.TestCase):
    XML = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="http://isdoc.cz/namespace/2013">
  <ID>2026001</ID>
  <IssueDate>2026-08-21</IssueDate>
  <AccountingSupplierParty><Party>
    <PartyIdentification><ID>25194798</ID></PartyIdentification>
    <PartyName><Name>PPL CZ s.r.o.</Name></PartyName>
    <PartyTaxScheme><CompanyID>CZ25194798</CompanyID></PartyTaxScheme>
  </Party></AccountingSupplierParty>
  <AccountingCustomerParty><Party>
    <PartyIdentification><ID>27800334</ID></PartyIdentification>
    <PartyTaxScheme><CompanyID>CZ27800334</CompanyID></PartyTaxScheme>
  </Party></AccountingCustomerParty>
  <LegalMonetaryTotal><PayableAmount>648.73</PayableAmount></LegalMonetaryTotal>
</Invoice>"""

    def test_company_id_from_the_supplier_block(self):
        inv = isdoc.parse_isdoc(self.XML.encode("utf-8"))
        self.assertEqual(inv.supplier_dic.value, "CZ25194798")

    def test_customer_dic_is_not_taken(self):
        inv = isdoc.parse_isdoc(self.XML.encode("utf-8"))
        self.assertNotEqual(inv.supplier_dic.value, "CZ27800334")

    def test_missing_tax_scheme_stays_empty(self):
        xml = self.XML.replace(
            "<PartyTaxScheme><CompanyID>CZ25194798</CompanyID></PartyTaxScheme>", "")
        inv = isdoc.parse_isdoc(xml.encode("utf-8"))
        self.assertIsNone(inv.supplier_dic.value)


if __name__ == "__main__":
    unittest.main()


class CounterpartyDicTests(unittest.TestCase):
    """DIČ protistrany se vzít NESMÍ, ani když je na dokladu jediné (v0.6.1).

    Proforma Phobosstudia (TONEZO 1009980368): dodavatel má „DIČ
    1073812245" BEZ kódu země a „IČ DPH SK1073812245" s ním, odběratel
    „DIČ CZ27800334". Starý vzor našel jen to poslední — tedy NAŠE DIČ —
    a protože byl jediný, vzal ho.
    """

    def _inv(self, text):
        return heuristics.extract_from_text(text)

    PROFORMA = ("Dodavatel Jaroslav Adamec - Phobosstudio  Odberatel MX-NET\n"
                "ICO 41596404 ICO 27800334\n"
                "DIC 1073812245 DIC CZ27800334\n"
                "IC DPH SK1073812245\n")

    def test_slovak_supplier_wins_over_our_dic(self):
        inv = self._inv(self.PROFORMA)
        self.assertEqual(inv.supplier_ico.value, "41596404")
        self.assertEqual(inv.supplier_dic.value, "SK1073812245")

    def test_ic_dph_label_is_recognised(self):
        inv = self._inv("Dodavatel s.r.o.\nIC 41596404\nIC DPH SK1073812245\n")
        self.assertEqual(inv.supplier_dic.value, "SK1073812245")

    def test_customer_dic_alone_is_refused(self):
        """Jediný nález, ale sedí na IČO odběratele → radši nic než cizí."""
        inv = self._inv("Dodavatel s.r.o.\nICO 41596404\n"
                        "Odberatel MX-NET ICO 27800334 DIC CZ27800334\n")
        self.assertIsNone(inv.supplier_dic.value)

    def test_supplier_dic_matching_its_own_ico_is_kept(self):
        """Běžný případ: DIČ = CZ + IČO dodavatele. Nesmí se zahodit."""
        inv = self._inv("Dodavatel PPL CZ s.r.o.\nICO 25194798\nDIC CZ25194798\n"
                        "Odberatel MX-NET ICO 27800334 DIC CZ27800334\n")
        self.assertEqual(inv.supplier_dic.value, "CZ25194798")

    def test_foreign_supplier_is_beyond_this_layer(self):
        """Polská faktura (PL26000900401): dodavatel má „VAT # PL5263736824",
        což není popisek DIČ. Na dokladu je pak JEDINÉ IČO to naše, takže
        `_supplier_ico_match` ho vezme za dodavatelovo a „CZ27800334" na
        něj sedí — odsud se to rozeznat NEDÁ.

        Rozliší to až konzument, který ví, kdo je „my": v HUBu to dělá
        `after_extraction` (`OwnDicTests`). Test drží hranici zodpovědnosti,
        ať se sem nezavádí hádání."""
        inv = self._inv("wuhanlinxiafengqidianzishangwuyouxiangongsi\n"
                        "VAT # PL5263736824\n"
                        "Kupujacy MX-NET ICO 27800334 DIC: CZ27800334\n")
        self.assertEqual(inv.supplier_dic.value, "CZ27800334")

    def test_foreign_dic_labelled_as_dic_is_kept(self):
        """Když zahraniční dodavatel popisek „DIČ" použije a na žádné IČO
        z dokladu nesedí, není důvod ho zahazovat."""
        inv = self._inv("Dodavatel GmbH\nDIC DE123456789\n")
        self.assertEqual(inv.supplier_dic.value, "DE123456789")
