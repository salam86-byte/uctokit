"""Testy ARES lookupu (fetch injektovaný, žádná síť)."""

import unittest

from uctokit.registry.ares import lookup_ico

ARES_JSON = (
    '{"ico":"12345679","obchodniJmeno":"Vzorový dodavatel, s.r.o.","dic":"CZ12345679",'
    '"sidlo":{"textovaAdresa":"Ukázková 1, 100 00 Praha"}}'
)

# Tvary adres, jak je ARES opravdu vrací (odkoukáno z živého API).
S_ORIENTACNIM = (
    '{"ico":"27074358","obchodniJmeno":"Asseco Central Europe, a.s.",'
    '"sidlo":{"nazevUlice":"Budějovická","cisloDomovni":778,"cisloOrientacni":3,'
    '"cisloOrientacniPismeno":"a","nazevCastiObce":"Michle","nazevObce":"Praha",'
    '"nazevMestskeCastiObvodu":"Praha 4","psc":14000,'
    '"textovaAdresa":"Budějovická 778/3a, Michle, 14000 Praha 4"}}'
)
BEZ_PISMENE = (
    '{"ico":"00295892","obchodniJmeno":"Město Bruntál",'
    '"sidlo":{"nazevUlice":"Nádražní","cisloDomovni":994,"cisloOrientacni":20,'
    '"nazevObce":"Bruntál","nazevCastiObce":"Bruntál","psc":79201,'
    '"textovaAdresa":"Nádražní 994/20, 79201 Bruntál"}}'
)
BEZ_ULICE = (
    '{"ico":"45193070","obchodniJmeno":"VÍTKOVICE, a.s.",'
    '"sidlo":{"cisloDomovni":3020,"nazevCastiObce":"Vítkovice","nazevObce":"Ostrava",'
    '"nazevMestskeCastiObvodu":"Vítkovice","psc":70300,'
    '"textovaAdresa":"Vítkovice 3020, 70300 Ostrava"}}'
)


def _lookup(payload):
    return lookup_ico("12345679", fetch=lambda url: (200, payload))


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


class AresAddressPartsTests(unittest.TestCase):
    """Adresa po částech – ARES ji má strukturovaně, nerozebírá se řetězcem."""

    def test_street_with_orientation_number(self):
        result = _lookup(S_ORIENTACNIM)
        self.assertEqual(result.street, "Budějovická 778/3a")
        self.assertEqual(result.postal_code, "140 00")

    def test_city_is_the_municipality_not_the_district(self):
        """Praha, ne Praha 4 – s PSČ je to doručitelné a nikdy to není špatně."""
        self.assertEqual(_lookup(S_ORIENTACNIM).city, "Praha")
        self.assertEqual(_lookup(BEZ_ULICE).city, "Ostrava")

    def test_street_without_letter(self):
        result = _lookup(BEZ_PISMENE)
        self.assertEqual(result.street, "Nádražní 994/20")
        self.assertEqual(result.city, "Bruntál")
        self.assertEqual(result.postal_code, "792 01")

    def test_address_without_a_street_uses_the_part_of_town(self):
        """Malé obce a areály ulici nemají – ARES sám skládá „Vítkovice 3020"."""
        result = _lookup(BEZ_ULICE)
        self.assertEqual(result.street, "Vítkovice 3020")
        self.assertEqual(result.postal_code, "703 00")

    def test_text_address_stays_available(self):
        """Jednořádková adresa zůstává – konzumenti na ní můžou stát."""
        self.assertEqual(
            _lookup(S_ORIENTACNIM).address, "Budějovická 778/3a, Michle, 14000 Praha 4"
        )

    def test_missing_address_gives_empty_strings(self):
        result = _lookup('{"ico":"12345679","obchodniJmeno":"Bez sídla"}')
        self.assertTrue(result.found)
        self.assertEqual((result.street, result.city, result.postal_code), ("", "", ""))


if __name__ == "__main__":
    unittest.main()
