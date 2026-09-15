"""Testy VIES lookupu (fetch injektovaný, žádná síť)."""

import unittest

from uctokit.registry.vies import lookup_vat, split_vat

PLATNE = (
    '{"isValid":true,"userError":"VALID","name":"WUHAN LINXIA FENGQI E-COMMERCE CO., LTD",'
    '"address":"ROOM 806\\n430000 WUHAN\\nCHINY","vatNumber":"5263736824"}'
)
NEPLATNE = '{"isValid":false,"userError":"INVALID","name":"---","address":"---"}'
NEDOSTUPNO = '{"isValid":false,"userError":"MS_UNAVAILABLE","name":"---","address":"---"}'


class SplitVatTests(unittest.TestCase):
    def test_prefix_a_cislo(self):
        self.assertEqual(split_vat("PL 526-373-68-24"), ("PL", "5263736824"))

    def test_recko_ma_danovy_prefix_el(self):
        self.assertEqual(split_vat("GR123456789"), ("EL", "123456789"))

    def test_pismena_v_cisle_zustanou(self):
        """Nizozemské a irské DIČ nejsou jen číslice."""
        self.assertEqual(split_vat("NL123456789B01"), ("NL", "123456789B01"))

    def test_bez_prefixu_se_nehada(self):
        self.assertEqual(split_vat("5263736824"), ("", ""))

    def test_mimoevropske_neni_eu_dic(self):
        self.assertEqual(split_vat("CH123456789"), ("", ""))

    def test_prazdne(self):
        self.assertEqual(split_vat(None), ("", ""))


class LookupVatTests(unittest.TestCase):
    def test_platne_vraci_jmeno_jednoradkove(self):
        r = lookup_vat("PL5263736824", fetch=lambda url: (200, PLATNE))
        self.assertTrue(r.checked)
        self.assertTrue(r.valid)
        self.assertEqual(r.country, "PL")
        self.assertIn("WUHAN", r.name)
        self.assertEqual(r.address, "ROOM 806, 430000 WUHAN, CHINY")
        self.assertEqual(r.vat_id, "PL5263736824")

    def test_url_ma_prefix_i_cislo(self):
        videne = []

        def fetch(url):
            videne.append(url)
            return 200, PLATNE

        lookup_vat("PL5263736824", fetch=fetch)
        self.assertTrue(videne[0].endswith("/ms/PL/vat/5263736824"), videne)

    def test_neplatne(self):
        r = lookup_vat("PL1234567890", fetch=lambda url: (200, NEPLATNE))
        self.assertTrue(r.checked)
        self.assertFalse(r.valid)
        self.assertEqual(r.name, "")      # „---" = nezveřejněno, ne název

    def test_nedostupny_registr_neni_neplatne_dic(self):
        r = lookup_vat("PL5263736824", fetch=lambda url: (200, NEDOSTUPNO))
        self.assertFalse(r.checked)
        self.assertFalse(r.valid)
        self.assertEqual(r.error, "MS_UNAVAILABLE")

    def test_vypadek_site_je_bezpecny(self):
        def boom(url):
            raise RuntimeError("no net")

        r = lookup_vat("PL5263736824", fetch=boom)
        self.assertFalse(r.checked)
        self.assertEqual(r.error, "NETWORK")

    def test_http_chyba(self):
        self.assertFalse(lookup_vat("PL5263736824", fetch=lambda url: (500, "")).checked)

    def test_rozbita_odpoved(self):
        self.assertFalse(lookup_vat("PL5263736824", fetch=lambda url: (200, "<html>")).checked)

    def test_bez_prefixu_se_neptame(self):
        def nesmi(url):
            raise AssertionError("dotaz na VIES bez prefixu země")

        r = lookup_vat("5263736824", fetch=nesmi)
        self.assertFalse(r.checked)
        self.assertEqual(r.error, "NOT_EU_VAT")


if __name__ == "__main__":
    unittest.main()
