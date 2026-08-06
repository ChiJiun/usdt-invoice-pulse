from __future__ import annotations

import base64
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from bot.config import Settings
from bot.exchanges.bitopro import BitoProAdapter
from bot.exchanges.hoyabit import HoyaBitAdapter
from bot.exchanges.max_exchange import MaxAdapter
from bot.models import estimated_invoice_status


def settings(target: str = "1") -> Settings:
    root = Path(tempfile.gettempdir()) / "usdt-invoice-pulse-tests"
    return Settings(
        target_usdt=Decimal(target),
        live_trading=False,
        live_confirmation="",
        bitopro_enabled=True,
        max_enabled=True,
        hoyabit_enabled=True,
        bitopro_email="",
        bitopro_api_key="",
        bitopro_api_secret="",
        max_api_key="",
        max_api_secret="",
        bitopro_taker_fee_rate=Decimal("0.002"),
        max_taker_fee_rate=Decimal("0.0016"),
        price_slippage=Decimal("0.005"),
        dashboard_path=root / "dashboard.json",
        state_path=root / "state.json",
        confirmed_invoices_path=root / "invoices.json",
    )


class FakeHttp:
    def __init__(self):
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url))
        if "order-book" in url:
            return {"asks": [{"price": "32.265", "amount": "100"}], "bids": []}
        if "limitations-and-fees" in url:
            return {
                "orderFeesAndLimitations": [
                    {"pair": "USDT/TWD", "minimumOrderAmount": "1"}
                ]
            }
        if url.endswith("/api/v3/markets"):
            return [
                {
                    "id": "usdttwd",
                    "min_base_amount": 8,
                    "min_quote_amount": 250,
                }
            ]
        if "/api/v3/ticker" in url:
            return {"sell": "32.263"}
        if url.endswith("/accounts/balance"):
            return {"data": [{"currency": "twd", "available": "100"}]}
        if url.endswith("/api/v3/info"):
            return {"email": "masked@example.invalid"}
        raise AssertionError(f"Unexpected request: {method} {url}")


class SignatureTests(unittest.TestCase):
    def test_bitopro_signature_matches_official_example(self):
        payload = (
            "eyJpZGVudGl0eSI6ImhjbWxpbmpAZ21haWwuY29tIiwibm9uY2Ui"
            "OjE1NTQzODA5MDkxMzF9"
        )
        expected = (
            "01a85a9083db47c20da7196380598f3feacd3c76a9077aaf7ffaf08ce0091abf6"
            "5b61778792607b010921adfe1c2941a"
        )
        self.assertEqual(BitoProAdapter.sign_payload(payload, "bitopro"), expected)

    def test_max_payload_contains_path(self):
        params = {"nonce": 123, "market": "usdttwd"}
        payload, signature = MaxAdapter.encode_signature(params, "/api/v3/info", "secret")
        decoded = base64.b64decode(payload).decode()
        self.assertIn('"path":"/api/v3/info"', decoded)
        self.assertEqual(len(signature), 64)


class RuleTests(unittest.TestCase):
    def test_invoice_rounding(self):
        self.assertEqual(estimated_invoice_status(Decimal("0.49")), "estimated_zero")
        self.assertEqual(
            estimated_invoice_status(Decimal("0.50")), "estimated_eligible"
        )

    def test_bitopro_one_usdt_is_simulated(self):
        result = BitoProAdapter(settings(), FakeHttp()).run(live=False)
        self.assertEqual(result.status, "simulated")
        self.assertEqual(result.filled_usdt, Decimal("1"))
        self.assertEqual(result.invoice_status, "estimated_zero")

    def test_max_one_usdt_is_skipped(self):
        result = MaxAdapter(settings(), FakeHttp()).run(live=False)
        self.assertEqual(result.status, "skipped")
        self.assertIn("8 USDT", result.message)

    def test_hoyabit_is_skipped(self):
        result = HoyaBitAdapter(settings()).run(live=False)
        self.assertEqual(result.status, "skipped")
        self.assertIn("10 USDT", result.message)

    def test_bitopro_credential_check_is_read_only(self):
        configured = replace(
            settings(),
            bitopro_email="member@example.invalid",
            bitopro_api_key="key",
            bitopro_api_secret="secret",
        )
        http = FakeHttp()
        BitoProAdapter(configured, http).verify_credentials()
        self.assertEqual(http.calls, [("GET", "https://api.bitopro.com/v3/accounts/balance")])

    def test_max_credential_check_is_read_only(self):
        configured = replace(
            settings(), max_api_key="key", max_api_secret="secret"
        )
        http = FakeHttp()
        MaxAdapter(configured, http).verify_credentials()
        self.assertEqual(http.calls, [("GET", "https://max-api.maicoin.com/api/v3/info")])


if __name__ == "__main__":
    unittest.main()
