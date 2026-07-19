"""Testy parsování Fio výpisu (čistý Python, bez sítě/Djanga)."""

import unittest

from uctokit.payments.fio_statement import parse_transactions


class ParseTransactionsTests(unittest.TestCase):
    def test_extracts_fields(self):
        data = {
            "accountStatement": {
                "transactionList": {
                    "transaction": [
                        {
                            "column22": {"value": 111},
                            "column0": {"value": "2025-09-06+0200"},
                            "column1": {"value": 500.0},
                            "column5": {"value": "12345678"},
                            "column10": {"value": "Novák Jan"},
                            "column2": {"value": "123456/0800"},
                            "column16": {"value": "prispevek"},
                        }
                    ]
                }
            }
        }
        parsed = parse_transactions(data)
        self.assertEqual(len(parsed), 1)
        item = parsed[0]
        self.assertEqual(item["fio_id"], "111")
        self.assertEqual(item["date"], "2025-09-06")
        self.assertEqual(item["amount"], 500.0)
        self.assertEqual(item["vs"], "12345678")
        self.assertEqual(item["counterparty_name"], "Novák Jan")
        self.assertEqual(item["message"], "prispevek")

    def test_handles_missing_optional_columns(self):
        data = {
            "accountStatement": {
                "transactionList": {
                    "transaction": [
                        {"column22": {"value": 222}, "column0": {"value": "2025-09-01"}, "column1": {"value": -100.0}}
                    ]
                }
            }
        }
        parsed = parse_transactions(data)
        self.assertEqual(parsed[0]["vs"], "")
        self.assertEqual(parsed[0]["counterparty_name"], "")

    def test_empty_statement(self):
        self.assertEqual(parse_transactions({}), [])
        self.assertEqual(parse_transactions({"accountStatement": {}}), [])


if __name__ == "__main__":
    unittest.main()
