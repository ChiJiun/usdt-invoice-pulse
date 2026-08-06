from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bot.config import Settings
from bot.exchanges.bitopro import BitoProAdapter
from bot.exchanges.max_exchange import MaxAdapter
from bot.trading import choose_trade_side, effective_target


def settings(target: str = "1") -> Settings:
    root = Path(tempfile.gettempdir()) / "usdt-invoice-pulse-tests"
    return Settings(
        target_usdt=Decimal(target),
        usdt_reserve=Decimal("0"),
        max_convert_enabled=True,
        max_convert_twd_amount=Decimal("10"),
        max_convert_usdt_amount=Decimal("1"),
        live_trading=False,
        live_confirmation="",
        bitopro_enabled=True,
        max_enabled=True,
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
    def __init__(
        self,
        *,
        bitopro_twd: str = "1000",
        bitopro_usdt: str = "100",
        max_twd: str = "1000",
        max_usdt: str = "100",
        max_converts: list[dict] | None = None,
    ):
        self.calls = []
        self.bitopro_twd = bitopro_twd
        self.bitopro_usdt = bitopro_usdt
        self.max_twd = max_twd
        self.max_usdt = max_usdt
        self.max_converts = max_converts or []
        self.last_body = None

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url))
        if "order-book" in url:
            return {
                "asks": [{"price": "32.265", "amount": "100"}],
                "bids": [{"price": "32.250", "amount": "100"}],
            }
        if "provisioning/trading-pairs" in url:
            return {
                "data": [
                    {
                        "pair": "usdt_twd",
                        "minLimitBaseAmount": "1",
                        "amountPrecision": "4",
                        "maintain": False,
                    }
                ]
            }
        if url.endswith("/api/v3/markets"):
            return [
                {
                    "id": "usdttwd",
                    "status": "active",
                    "base_unit_precision": 2,
                    "min_base_amount": "8",
                    "min_quote_amount": "250",
                }
            ]
        if "/api/v3/ticker" in url:
            return {"buy": "32.250", "sell": "32.263"}
        if url.endswith("/accounts/balance"):
            return {
                "data": [
                    {"currency": "twd", "available": self.bitopro_twd},
                    {"currency": "usdt", "available": self.bitopro_usdt},
                ]
            }
        if url.endswith("/api/v3/wallet/spot/accounts"):
            return [
                {"currency": "twd", "balance": self.max_twd},
                {"currency": "usdt", "balance": self.max_usdt},
            ]
        if method == "GET" and url.endswith("/api/v3/converts"):
            return self.max_converts
        if "/orders/all/usdt_twd" in url:
            return {"data": []}
        if method == "POST" and url.endswith("/orders/usdt_twd"):
            self.last_body = kwargs["body"]
            return {"orderId": "bito-order-1"}
        if method == "GET" and url.endswith("/orders/usdt_twd/bito-order-1"):
            return {
                "executedAmount": self.last_body["amount"],
                "remainingAmount": "0",
                "avgExecutionPrice": self.last_body["price"],
                "fee": "0",
                "feeSymbol": "twd",
            }
        if method == "POST" and url.endswith("/api/v3/wallet/spot/order"):
            self.last_body = kwargs["body"]
            return {
                "state": "done",
                "executed_volume": self.last_body["volume"],
                "avg_price": "32.263",
            }
        if method == "POST" and url.endswith("/api/v3/convert"):
            self.last_body = kwargs["body"]
            if self.last_body["from_currency"] == "twd":
                return {
                    "sn": "convert-twd-1",
                    "from_currency": "twd",
                    "from_amount": self.last_body["from_amount"],
                    "to_currency": "usdt",
                    "to_amount": "0.31",
                    "fee": "0",
                    "fee_currency": "usdt",
                    "fee_in_twd": "0",
                    "created_at": 1786024800,
                }
            return {
                "sn": "convert-usdt-1",
                "from_currency": "usdt",
                "from_amount": self.last_body["from_amount"],
                "to_currency": "twd",
                "to_amount": "32.25",
                "fee": "0",
                "fee_currency": "twd",
                "fee_in_twd": "0",
                "created_at": 1786024800,
            }
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
    def test_bitopro_one_usdt_is_simulated(self):
        result = BitoProAdapter(settings(), FakeHttp()).run(live=False)
        self.assertEqual(result.status, "simulated")
        self.assertEqual(result.side, "buy")
        self.assertEqual(result.requested_usdt, Decimal("1"))
        self.assertEqual(result.filled_usdt, Decimal("1"))
        self.assertEqual(result.invoice_status, "not_applicable")
        self.assertEqual(result.execution_type, "spot")

    def test_max_one_usdt_floor_is_raised_to_eight(self):
        result = MaxAdapter(settings(), FakeHttp()).run(live=False)
        self.assertEqual(result.status, "simulated")
        self.assertEqual(result.side, "buy")
        self.assertEqual(result.requested_usdt, Decimal("8"))
        self.assertEqual(result.filled_usdt, Decimal("8"))

    def test_quote_minimum_can_raise_target_above_base_minimum(self):
        target = effective_target(
            Decimal("1"),
            Decimal("8"),
            Decimal("250"),
            Decimal("30"),
            Decimal("0.01"),
        )
        self.assertEqual(target, Decimal("8.34"))

    def test_balance_decision_prefers_buy_then_falls_back_to_sell(self):
        buy = choose_trade_side(
            available_twd=Decimal("1000"),
            available_usdt=Decimal("100"),
            target_usdt=Decimal("8"),
            buy_price_twd=Decimal("32"),
            buy_buffer_rate=Decimal("0.01"),
            usdt_reserve=Decimal("0"),
        )
        sell = choose_trade_side(
            available_twd=Decimal("0"),
            available_usdt=Decimal("8"),
            target_usdt=Decimal("8"),
            buy_price_twd=Decimal("32"),
            buy_buffer_rate=Decimal("0.01"),
            usdt_reserve=Decimal("0"),
        )
        skipped = choose_trade_side(
            available_twd=Decimal("0"),
            available_usdt=Decimal("8"),
            target_usdt=Decimal("8"),
            buy_price_twd=Decimal("32"),
            buy_buffer_rate=Decimal("0.01"),
            usdt_reserve=Decimal("1"),
        )
        self.assertEqual(buy.side, "buy")
        self.assertEqual(sell.side, "sell")
        self.assertEqual(skipped.side, "none")

    def test_bitopro_live_falls_back_to_sell(self):
        configured = replace(
            settings(),
            bitopro_email="member@example.invalid",
            bitopro_api_key="key",
            bitopro_api_secret="secret",
        )
        http = FakeHttp(bitopro_twd="0", bitopro_usdt="1")
        with patch("bot.exchanges.bitopro.time.sleep", return_value=None):
            result = BitoProAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.side, "sell")
        self.assertEqual(http.last_body["action"], "SELL")

    def test_max_live_prefers_buy_and_uses_eight_usdt(self):
        configured = replace(settings(), max_api_key="key", max_api_secret="secret")
        http = FakeHttp(max_twd="1000", max_usdt="100")
        result = MaxAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.side, "buy")
        self.assertEqual(result.execution_type, "spot")
        self.assertEqual(http.last_body["volume"], "8")

    def test_max_live_falls_back_to_sell(self):
        configured = replace(settings(), max_api_key="key", max_api_secret="secret")
        http = FakeHttp(max_twd="0", max_usdt="8")
        result = MaxAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.side, "sell")
        self.assertEqual(http.last_body["side"], "sell")

    def test_insufficient_spot_balance_uses_low_twd_convert(self):
        configured = replace(settings(), max_api_key="key", max_api_secret="secret")
        http = FakeHttp(max_twd="100", max_usdt="0")
        result = MaxAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.side, "buy")
        self.assertEqual(result.execution_type, "convert")
        self.assertEqual(result.invoice_status, "pending_confirmation")
        self.assertEqual(http.last_body["from_currency"], "twd")
        self.assertEqual(http.last_body["from_amount"], "10")

    def test_insufficient_spot_balance_uses_low_usdt_convert(self):
        configured = replace(settings(), max_api_key="key", max_api_secret="secret")
        http = FakeHttp(max_twd="0", max_usdt="7")
        result = MaxAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.side, "sell")
        self.assertEqual(result.execution_type, "convert")
        self.assertEqual(http.last_body["from_currency"], "usdt")
        self.assertEqual(http.last_body["from_amount"], "1")

    def test_no_balance_is_skipped_without_convert(self):
        configured = replace(settings(), max_api_key="key", max_api_secret="secret")
        http = FakeHttp(max_twd="0", max_usdt="0")
        result = MaxAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.execution_type, "none")
        post_urls = [url for method, url in http.calls if method == "POST"]
        self.assertEqual(post_urls, [])

    def test_convert_can_be_disabled(self):
        configured = replace(
            settings(),
            max_api_key="key",
            max_api_secret="secret",
            max_convert_enabled=False,
        )
        http = FakeHttp(max_twd="100", max_usdt="0")
        result = MaxAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "skipped")
        self.assertIn("未啟用", result.message)

    def test_existing_today_convert_blocks_duplicate(self):
        configured = replace(settings(), max_api_key="key", max_api_secret="secret")
        existing = {
            "sn": "existing-convert",
            "from_currency": "twd",
            "from_amount": "10",
            "to_currency": "usdt",
            "to_amount": "0.31",
            "created_at": int(time.time()),
        }
        http = FakeHttp(
            max_twd="100",
            max_usdt="0",
            max_converts=[existing],
        )
        result = MaxAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.execution_type, "convert")
        self.assertIn("既有", result.message)
        post_urls = [url for method, url in http.calls if method == "POST"]
        self.assertEqual(post_urls, [])

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
        self.assertEqual(
            http.calls,
            [("GET", "https://max-api.maicoin.com/api/v3/wallet/spot/accounts")],
        )


class DashboardPolicyTests(unittest.TestCase):
    def test_public_dashboard_only_contains_programmatic_exchanges(self):
        dashboard = json.loads(
            Path("public/data/dashboard.json").read_text(encoding="utf-8")
        )
        supported = {"bitopro", "max"}
        self.assertEqual(
            {exchange["id"] for exchange in dashboard["exchanges"]}, supported
        )
        self.assertTrue(
            all(event["exchange"] in supported for event in dashboard["events"])
        )
        event_scopes = [
            (event.get("date"), event.get("exchange"), event.get("mode"))
            for event in dashboard["events"]
        ]
        self.assertEqual(len(event_scopes), len(set(event_scopes)))
        max_event = next(event for event in dashboard["events"] if event["exchange"] == "max")
        self.assertEqual(Decimal(max_event["requested_usdt"]), Decimal("8"))
        self.assertIn(max_event["side"], {"buy", "sell", "none"})
        self.assertIn(max_event["execution_type"], {"spot", "convert", "none"})
        self.assertNotIn("fee_twd", max_event)


if __name__ == "__main__":
    unittest.main()
