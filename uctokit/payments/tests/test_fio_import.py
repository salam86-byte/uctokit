"""Testy Fio importu příkazů (čistý Python, bez sítě/Djanga)."""

import unittest
from datetime import date
from decimal import Decimal

from uctokit.payments.fio_import import (
    DomesticOrder,
    build_import_xml,
    iban_to_domestic,
    parse_import_response,
)


class BuildImportXmlTests(unittest.TestCase):
    def _order(self, **kw):
        base = dict(
            account_from="1234562", account_to="1111111111", bank_code="0800",
            amount=Decimal("1500"), date=date(2025, 9, 30), vs="20251234",
            message="Faktura 2025-1234",
        )
        base.update(kw)
        return DomesticOrder(**base)

    def test_contains_core_elements(self):
        xml = build_import_xml([self._order()])
        self.assertIn("<DomesticTransaction>", xml)
        self.assertIn("<accountFrom>1234562</accountFrom>", xml)
        self.assertIn("<accountTo>1111111111</accountTo>", xml)
        self.assertIn("<bankCode>0800</bankCode>", xml)
        self.assertIn("<amount>1500.00</amount>", xml)  # 2 desetinná, tečka
        self.assertIn("<date>2025-09-30</date>", xml)
        self.assertIn("<vs>20251234</vs>", xml)
        self.assertIn("<paymentType>431001</paymentType>", xml)
        self.assertIn('xsi:noNamespaceSchemaLocation="http://www.fio.cz/schema/importIB.xsd"', xml)

    def test_empty_optional_fields_skipped(self):
        xml = build_import_xml([self._order(ks="", ss="")])
        self.assertNotIn("<ks>", xml)
        self.assertNotIn("<ss>", xml)

    def test_escapes_message(self):
        xml = build_import_xml([self._order(message="A & B <x>")])
        self.assertIn("A &amp; B &lt;x&gt;", xml)
        self.assertNotIn("<x>", xml.split("messageForRecipient")[1][:40])

    def test_multiple_orders(self):
        xml = build_import_xml([self._order(), self._order(amount=Decimal("200"))])
        self.assertEqual(xml.count("<DomesticTransaction>"), 2)


class IbanToDomesticTests(unittest.TestCase):
    def test_cz_iban(self):
        self.assertEqual(
            iban_to_domestic("CZ9708000000191111111111"), ("19-1111111111", "0800")
        )

    def test_cz_iban_with_spaces(self):
        self.assertEqual(
            iban_to_domestic("CZ97 0800 0000 1911 1111 1111"), ("19-1111111111", "0800")
        )

    def test_non_cz_returns_none(self):
        self.assertIsNone(iban_to_domestic("SK9708000000191111111111"))
        self.assertIsNone(iban_to_domestic("nonsense"))


class ParseImportResponseTests(unittest.TestCase):
    RESPONSE = """<?xml version="1.0"?>
    <responseImport xmlns="http://www.fio.cz/schema/responseImportIB.xsd">
      <result>
        <errorCode>0</errorCode>
        <idInstruction>1234567</idInstruction>
        <status>ok</status>
        <sumDebet>1500.00</sumDebet>
      </result>
      <ordersDetails>
        <detail id="1"><messages><message status="ok">Prikaz prijat</message></messages></detail>
      </ordersDetails>
    </responseImport>"""

    def test_parses_ok(self):
        result = parse_import_response(self.RESPONSE)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.error_code, 0)
        self.assertEqual(result.instruction_id, "1234567")
        self.assertTrue(result.accepted)
        self.assertIn("Prikaz prijat", result.messages)

    def test_error_not_accepted(self):
        xml = self.RESPONSE.replace("<status>ok</status>", "<status>error</status>")
        self.assertFalse(parse_import_response(xml).accepted)

    def test_invalid_xml(self):
        result = parse_import_response("not xml")
        self.assertEqual(result.status, "fatal")
        self.assertFalse(result.accepted)


if __name__ == "__main__":
    unittest.main()
