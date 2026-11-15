import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wallet_alert_bot as bot  # noqa: E402

ADDRESS = "0x94792f62Ef04A31c0c0c9E0d3Dd7b089c8fcE9B8"


class AlertFormattingTests(unittest.TestCase):
    def test_to_text_contains_core_fields(self):
        alert = bot.Alert("native", 8453, 123, "0xabc", ADDRESS, "incoming 0.5 native")
        text = alert.to_text()
        self.assertIn("native", text)
        self.assertIn("0xabc", text)
        self.assertIn(ADDRESS, text)
        self.assertIn("8453", text)


class NativeScanTests(unittest.TestCase):
    def test_detects_incoming_and_outgoing(self):
        block = {
            "number": "0x10",
            "transactions": [
                {"hash": "0xin1", "from": "0x1111111111111111111111111111111111111111",
                 "to": ADDRESS.lower(), "value": hex(10 ** 18)},
                {"hash": "0xout1", "from": ADDRESS.lower(),
                 "to": "0x2222222222222222222222222222222222222222", "value": hex(2 * 10 ** 17)},
                {"hash": "0xother", "from": "0x3333333333333333333333333333333333333333",
                 "to": "0x4444444444444444444444444444444444444444", "value": hex(5)},
            ],
        }
        alerts = bot.scan_native("http://rpc", ADDRESS, block)
        self.assertEqual(len(alerts), 2)
        kinds = {a.tx_hash: a.detail.split()[0] for a in alerts}
        self.assertEqual(kinds["0xin1"], "incoming")
        self.assertEqual(kinds["0xout1"], "outgoing")

    def test_ignores_zero_value(self):
        block = {
            "number": "0x10",
            "transactions": [
                {"hash": "0xzero", "from": ADDRESS.lower(), "to": ADDRESS.lower(), "value": "0x0"},
            ],
        }
        self.assertEqual(bot.scan_native("http://rpc", ADDRESS, block), [])


class TokenScanTests(unittest.TestCase):
    def test_parses_transfer_topics(self):
        padded = "0x" + "0" * 24 + ADDRESS.lower()[2:]
        logs = [{
            "blockNumber": "0x10",
            "transactionHash": "0xtok",
            "topics": [
                bot.TRANSFER_TOPIC,
                padded,
                "0x" + "0" * 24 + "2222222222222222222222222222222222222222",
            ],
            "data": hex(1_000_000),
        }]
        with mock.patch.object(bot, "rpc", return_value=logs):
            alerts = bot.scan_token("http://rpc", ADDRESS, "0xtoken", 16)
        self.assertEqual(len(alerts), 1)
        self.assertIn("outgoing", alerts[0].detail)
        self.assertIn("1000000", alerts[0].detail)


class BalanceTests(unittest.TestCase):
    def test_fires_below_threshold(self):
        with mock.patch.object(bot, "rpc", return_value=hex(10 ** 17)):
            alerts = bot.balance_alert("http://rpc", ADDRESS, 10 ** 18, 16)
        self.assertEqual(len(alerts), 1)

    def test_silent_above_threshold(self):
        with mock.patch.object(bot, "rpc", return_value=hex(10 ** 18)):
            self.assertEqual(bot.balance_alert("http://rpc", ADDRESS, 10 ** 18, 16), [])


if __name__ == "__main__":
    unittest.main()
