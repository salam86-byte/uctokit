"""Testy MFČR kontroly nespolehlivého plátce (fetch injektovaný, žádná síť)."""

import unittest

from uctokit.registry.mfcr import account_is_published, check_unreliable_vat, normalize_account

RESP = (
    '<?xml version="1.0"?>'
    '<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/"><Body>'
    '<StatusNespolehlivyPlatceResponse xmlns="http://adis.mfcr.cz/rozhraniCRPDPH/">'
    '<statusPlatceDPH nespolehlivyPlatce="NE" dic="12345679">'
    "<zverejneneUcty><ucet>"
    '<standardniUcet predcisli="000027" cislo="1111111111" kodBanky="0100"/>'
    "</ucet></zverejneneUcty>"
    "</statusPlatceDPH></StatusNespolehlivyPlatceResponse>"
    "</Body></Envelope>"
)


class MfcrTests(unittest.TestCase):
    def test_reliable_with_account(self):
        result = check_unreliable_vat("CZ12345679", fetch=lambda body: (200, RESP))
        self.assertTrue(result.found)
        self.assertFalse(result.unreliable)
        self.assertIn("27-1111111111/0100", result.accounts)
        self.assertTrue(account_is_published("27-1111111111/0100", result.accounts))
        self.assertFalse(account_is_published("99-1111111111/0300", result.accounts))

    def test_unreliable_flag(self):
        resp = RESP.replace('nespolehlivyPlatce="NE"', 'nespolehlivyPlatce="ANO"')
        result = check_unreliable_vat("CZ12345679", fetch=lambda body: (200, resp))
        self.assertTrue(result.unreliable)

    def test_not_found(self):
        resp = RESP.replace('nespolehlivyPlatce="NE"', 'nespolehlivyPlatce="NENALEZEN"')
        self.assertFalse(check_unreliable_vat("CZ99999999", fetch=lambda body: (200, resp)).found)

    def test_normalize_strips_leading_zeros(self):
        self.assertEqual(normalize_account("027-01111111111/0100"), "27-1111111111/0100")

    def test_network_error_is_safe(self):
        def boom(body):
            raise RuntimeError("no net")

        self.assertFalse(check_unreliable_vat("CZ12345679", fetch=boom).found)


if __name__ == "__main__":
    unittest.main()
