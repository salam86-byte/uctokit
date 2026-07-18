"""Testy ARES lookupu (fetch injektovaný, žádná síť)."""

import unittest

from uctokit.registry.ares import lookup_ico

ARES_JSON = (
    '{"ico":"12345679","obchodniJmeno":"Vzorový dodavatel, s.r.o.","dic":"CZ12345679",'
    '"sidlo":{"textovaAdresa":"Ukázková 1, 100 00 Praha"}}'
)


class AresTests(unittest.TestCase):
    def test_found(self):
        result = lookup_ico("12345679", fetch=lambda url: (200, ARES_JSON))
        self.assertTrue(result.found)
        self.assertEqual(result.name, "Vzorový dodavatel, s.r.o.")
        self.assertEqual(result.dic, "CZ12345679")
        self.assertIn("Praha", result.address)

    def test_not_found_404(self):
        self.assertFalse(lookup_ico("12345678", fetch=lambda url: (404, "")).found)

    def test_invalid_ico_skips_fetch(self):
        self.assertFalse(lookup_ico("123", fetch=lambda url: (200, ARES_JSON)).found)

    def test_network_error_is_safe(self):
        def boom(url):
            raise RuntimeError("no net")

        self.assertFalse(lookup_ico("12345679", fetch=boom).found)


if __name__ == "__main__":
    unittest.main()
