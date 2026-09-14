from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bot.config import Settings
from bot.exchanges.bitopro import BitoProAdapter
from bot.exchanges.max_exchange import MaxAdapter
from bot.runner import (
    TAIPEI, existing_live_record, make_duplicate_result, normalize_invoice_records,
    parse_args, read_json, refreshed_exchange_status, run_all, safe_public_url, write_json,
)
from bot.trading import choose_trade_side, fee_quote_target, fee_target_quantity


def settings() -> Settings:
    with patch.dict(os.environ, {}, clear=True):
        return Settings.from_env()


def live_settings() -> Settings:
    return replace(
        settings(), live_trading=True,
        live_confirmation="I_UNDERSTAND_THIS_PLACES_REAL_ORDERS",
        bitopro_email="member@example.invalid", bitopro_api_key="key",
        bitopro_api_secret="secret", max_api_key="key", max_api_secret="secret",
    )


class FakeHttp:
    def __init__(
        self, *, bitopro_twd="1000", bitopro_usdt="100",
        max_twd="1000", max_usdt="100", bitopro_trades=None,
        max_trades=None, max_converts=None, filled=None,
        fee="0.51", fee_currency="twd", fee_error=False,
        bito_orders=None, market_status="active", minimum_base="8",
        minimum_quote="250", amount_precision=4, price_precision=3,
    ):
        self.calls = []
        self.requests = []
        self.bitopro_twd, self.bitopro_usdt = bitopro_twd, bitopro_usdt
        self.max_twd, self.max_usdt = max_twd, max_usdt
        self.bitopro_trades = [] if bitopro_trades is None else bitopro_trades
        self.max_trades = [] if max_trades is None else max_trades
        self.max_converts = [] if max_converts is None else max_converts
        self.filled, self.fee, self.fee_currency = filled, fee, fee_currency
        self.fee_error, self.bito_orders = fee_error, bito_orders or []
        self.market_status = market_status
        self.minimum_base, self.minimum_quote = minimum_base, minimum_quote
        self.amount_precision, self.price_precision = amount_precision, price_precision
        self.last_body = None

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url))
        self.requests.append((method, url, kwargs))
        if "order-book" in url:
            return {"asks": [{"price": "32.265"}], "bids": [{"price": "32.250"}]}
        if "provisioning/trading-pairs" in url:
            return {"data": [{
                "pair": "usdt_twd", "minLimitBaseAmount": "1",
                "amountPrecision": self.amount_precision,
                "quotePrecision": self.price_precision,
                "maintain": self.market_status != "active",
            }]}
        if url.endswith("/api/v3/markets"):
            return [{
                "id": "usdttwd", "status": self.market_status,
                "base_unit_precision": 2, "quote_unit_precision": self.price_precision,
                "min_base_amount": self.minimum_base, "min_quote_amount": self.minimum_quote,
            }]
        if url.endswith("/api/v3/ticker"):
            return {"buy": "32.250", "sell": "32.263"}
        if url.endswith("/accounts/balance"):
            return {"data": [
                {"currency": "twd", "available": self.bitopro_twd},
                {"currency": "usdt", "available": self.bitopro_usdt},
            ]}
        if url.endswith("/api/v3/wallet/spot/accounts"):
            return [
                {"currency": "twd", "balance": self.max_twd},
                {"currency": "usdt", "balance": self.max_usdt},
            ]
        if url.endswith("/orders/trades/usdt_twd"):
            return {"data": self.bitopro_trades}
        if url.endswith("/api/v3/wallet/spot/trades"):
            return self.max_trades
        if url.endswith("/api/v3/converts"):
            return self.max_converts
        if url.endswith("/orders/all/usdt_twd"):
            return {"data": self.bito_orders}
        if method == "POST" and url.endswith("/orders/usdt_twd"):
            self.last_body = kwargs["body"]
            return {"orderId": "bito-order-1"}
        if method == "GET" and url.endswith("/orders/usdt_twd/bito-order-1"):
            requested = Decimal(self.last_body["amount"])
            filled = requested if self.filled is None else Decimal(self.filled)
            return {
                "executedAmount": str(filled), "remainingAmount": str(requested - filled),
                "avgExecutionPrice": self.last_body["price"], "fee": self.fee,
                "feeSymbol": self.fee_currency,
            }
        if method == "DELETE" and "/orders/usdt_twd/" in url:
            return {}
        if method == "POST" and url.endswith("/api/v3/wallet/spot/order"):
            self.last_body = kwargs["body"]
            requested = Decimal(self.last_body["volume"])
            filled = requested if self.filled is None else Decimal(self.filled)
            return {
                "state": "done" if filled == requested else "cancel",
                "executed_volume": str(filled), "avg_price": "32.263",
            }
        if url.endswith("/api/v3/order/trades"):
            if self.fee_error:
                raise RuntimeError("fee endpoint unavailable")
            return [{"fee": self.fee, "fee_currency": self.fee_currency}]
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
        payload, signature = MaxAdapter.encode_signature({"nonce": 123}, "/api/v3/info", "secret")
        self.assertIn('"path":"/api/v3/info"', base64.b64decode(payload).decode())
        self.assertEqual(len(signature), 64)


class ConfigurationTests(unittest.TestCase):
    def test_defaults_and_bom_normalization(self):
        environment = {
            "BITOPRO_FEE_TWD_TARGET": " \ufeff0.5 ",
            "LIVE_TRADING": "\ufeff true ",
            "CONFIRM_LIVE_TRADING": "\ufeffI_UNDERSTAND_THIS_PLACES_REAL_ORDERS\n",
            "BITOPRO_EMAIL": " \ufeffowner@example.com ",
            "BITOPRO_API_KEY": "\ufeffbito-key\n",
            "BITOPRO_API_SECRET": " \ufeffbito-secret ",
            "MAX_API_KEY": "\ufeffmax-key ",
            "MAX_API_SECRET": " \ufeffmax-secret\n",
        }
        with patch.dict(os.environ, environment, clear=True):
            loaded = Settings.from_env()
        self.assertEqual(loaded.bitopro_fee_twd_target, Decimal("0.5"))
        self.assertEqual(loaded.max_fee_twd_target, Decimal("1"))
        self.assertFalse(loaded.max_enabled)
        loaded.assert_live_authorized()
        self.assertEqual(loaded.bitopro_email, "owner@example.com")
        self.assertEqual(loaded.bitopro_api_key, "bito-key")
        self.assertEqual(loaded.bitopro_api_secret, "bito-secret")
        self.assertEqual(loaded.max_api_key, "max-key")
        self.assertEqual(loaded.max_api_secret, "max-secret")

    def test_fee_rate_overrides_are_loaded(self):
        with patch.dict(os.environ, {"BITOPRO_TAKER_FEE_RATE": "0.0016",
                                     "MAX_TAKER_FEE_RATE": "0.001"}, clear=True):
            loaded = Settings.from_env()
        self.assertEqual(fee_quote_target(loaded.bitopro_fee_twd_target,
                                         loaded.bitopro_taker_fee_rate), Decimal("312.5"))
        self.assertEqual(fee_quote_target(loaded.max_fee_twd_target,
                                         loaded.max_taker_fee_rate), Decimal("1000"))

    def test_invalid_configuration_is_rejected(self):
        for name in ("BITOPRO_FEE_TWD_TARGET", "MAX_FEE_TWD_TARGET",
                     "BITOPRO_TAKER_FEE_RATE", "MAX_TAKER_FEE_RATE", "ORDER_PRICE_SLIPPAGE"):
            for value in ("0", "-1", "NaN", "Infinity", "not-a-number"):
                with self.subTest(name=name, value=value), patch.dict(
                    os.environ, {name: value}, clear=True
                ), self.assertRaises(ValueError):
                    Settings.from_env()
        for value in ("1", "2"):
            with patch.dict(os.environ, {"MAX_TAKER_FEE_RATE": value}, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env()

    def test_legacy_variables_cannot_change_new_strategy(self):
        with patch.dict(os.environ, {
            "ORDER_USDT": "10000", "USDT_RESERVE": "10000",
            "MAX_INVOICE_TWD_TARGET": "10", "MAX_CONVERT_ENABLED": "true",
            "MAX_TEST_DATE": "2999-01-01",
        }, clear=True):
            loaded = Settings.from_env()
        self.assertEqual(loaded.bitopro_fee_twd_target, Decimal("0.5"))
        self.assertEqual(loaded.max_fee_twd_target, Decimal("1"))
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            parse_args(["--max-test-625"])


class FeeSizingTests(unittest.TestCase):
    def test_turnover_targets(self):
        self.assertEqual(fee_quote_target(Decimal("0.5"), Decimal("0.002")), Decimal("250"))
        self.assertEqual(fee_quote_target(Decimal("1"), Decimal("0.0016")), Decimal("625"))

    def test_quantity_rounds_up_and_respects_official_minima(self):
        target = fee_target_quantity(
            fee_twd=Decimal("0.5"), fee_rate=Decimal("0.002"),
            minimum_base=Decimal("8"), minimum_quote=Decimal("300"),
            price_floor_twd=Decimal("32"), step=Decimal("0.01"),
        )
        self.assertEqual(target, Decimal("9.38"))
        self.assertGreaterEqual(target * Decimal("32"), Decimal("300"))

    def test_base_minimum_can_exceed_fee_target(self):
        target = fee_target_quantity(
            fee_twd=Decimal("0.5"), fee_rate=Decimal("0.002"),
            minimum_base=Decimal("20"), minimum_quote=Decimal("0"),
            price_floor_twd=Decimal("32"), step=Decimal("0.01"),
        )
        self.assertEqual(target, Decimal("20"))

    def test_side_buy_sell_or_skip_and_buy_only(self):
        def side(twd, usdt, allow_sell=True):
            return choose_trade_side(
                available_twd=Decimal(twd), available_usdt=Decimal(usdt),
                target_usdt=Decimal("8"), buy_price_twd=Decimal("32"),
                buy_buffer_rate=Decimal("0.01"), allow_sell=allow_sell,
            )
        self.assertEqual(side("1000", "100"), "buy")
        self.assertEqual(side("0", "8"), "sell")
        self.assertEqual(side("0", "7.99"), "none")
        self.assertEqual(side("0", "10000", False), "none")
        self.assertEqual(side("258.56", "0", False), "buy")
        self.assertEqual(side("258.559", "100", False), "none")


class AdapterTests(unittest.TestCase):
    def run_bito(self, http, configured=None, live=True):
        with patch("bot.exchanges.bitopro.time.sleep", return_value=None):
            return BitoProAdapter(configured or live_settings(), http).run(live=live)

    def test_dry_runs_only_read_public_endpoints_and_estimate_fees(self):
        for adapter_type, target in ((BitoProAdapter, Decimal("0.5")), (MaxAdapter, Decimal("1"))):
            http = FakeHttp()
            result = adapter_type(settings(), http).run(live=False)
            self.assertEqual(result.status, "simulated")
            self.assertEqual(result.invoice_status, "not_applicable")
            self.assertGreaterEqual(result.estimated_fee_twd, target)
            self.assertIsNone(result.actual_fee)
            self.assertTrue(all(method == "GET" for method, _ in http.calls))
            self.assertTrue(all("accounts" not in url and "/orders/" not in url
                                and "/wallet/" not in url for _, url in http.calls))

    def test_bitopro_buy_sizes_for_half_twd_fee(self):
        http = FakeHttp()
        result = self.run_bito(http)
        self.assertEqual(result.side, "buy")
        self.assertGreater(result.requested_usdt, Decimal("1"))
        self.assertGreaterEqual(result.estimated_fee_twd, Decimal("0.5"))
        self.assertEqual(http.last_body["action"], "BUY")
        self.assertEqual(http.last_body["type"], "LIMIT")
        self.assertEqual(result.invoice_status, "pending_confirmation")
        self.assertEqual(result.actual_fee, Decimal("0.51"))

    def test_bitopro_sell_sizes_at_conservative_limit(self):
        http = FakeHttp(bitopro_twd="0", bitopro_usdt="100")
        result = self.run_bito(http)
        self.assertEqual(result.side, "sell")
        self.assertEqual(http.last_body["action"], "SELL")
        self.assertGreaterEqual(
            Decimal(http.last_body["amount"]) * Decimal(http.last_body["price"]) * Decimal("0.002"),
            Decimal("0.5"),
        )

    def test_bitopro_insufficient_balances_skip_without_small_order(self):
        http = FakeHttp(bitopro_twd="249", bitopro_usdt="1")
        result = self.run_bito(http)
        self.assertEqual(result.status, "skipped")
        self.assertFalse(any(method == "POST" for method, _ in http.calls))

    def test_discounted_rate_increases_size(self):
        normal = self.run_bito(FakeHttp(), live=False)
        discounted = self.run_bito(
            FakeHttp(), replace(live_settings(), bitopro_taker_fee_rate=Decimal("0.0016")),
            live=False,
        )
        self.assertGreater(discounted.requested_usdt, normal.requested_usdt)
        self.assertGreaterEqual(discounted.estimated_fee_twd, Decimal("0.5"))

    def test_bitopro_partial_is_cancelled_without_topup(self):
        http = FakeHttp(filled="1")
        result = self.run_bito(http)
        self.assertEqual(result.status, "partial")
        self.assertIn("不自動補單", result.message)
        self.assertEqual(sum(method == "POST" for method, _ in http.calls), 1)
        self.assertTrue(any(method == "DELETE" for method, _ in http.calls))

    def test_native_fee_is_not_claimed_to_be_twd(self):
        result = self.run_bito(FakeHttp(fee="0.015", fee_currency="usdt"))
        self.assertEqual(result.actual_fee, Decimal("0.015"))
        self.assertEqual(result.fee_currency, "usdt")
        self.assertNotEqual(result.actual_fee, result.estimated_fee_twd)
        self.assertEqual(result.invoice_status, "pending_confirmation")
        self.assertEqual(BitoProAdapter._fee_fields(
            {"fee": "0", "feeSymbol": "usdt", "bitoFee": "0.02"}
        ), {"actual_fee": Decimal("0.02"), "fee_currency": "bito"})

    def test_bitopro_existing_small_fill_blocks_topup(self):
        http = FakeHttp(bitopro_trades=[{
            "baseAmount": "1", "quoteAmount": "32.25", "action": "BUY",
            "createdTimestamp": int(time.time() * 1000),
        }])
        result = self.run_bito(http)
        self.assertEqual(result.filled_usdt, Decimal("1"))
        self.assertFalse(any(method == "POST" for method, _ in http.calls))

    def test_bitopro_invalid_fee_does_not_hide_fill(self):
        for fee in ("not-a-number", "NaN", "Infinity"):
            with self.subTest(fee=fee):
                result = self.run_bito(FakeHttp(fee=fee))
                self.assertEqual(result.status, "filled")
                self.assertIsNone(result.actual_fee)

    def test_bitopro_empty_history_variants_and_unknown_shape(self):
        for payload in (None, {}):
            http = FakeHttp(bitopro_trades=payload)
            adapter = BitoProAdapter(live_settings(), http)
            self.assertIsNone(adapter._find_today_trade(adapter.now()))
        with self.assertRaises(RuntimeError):
            adapter = BitoProAdapter(live_settings(), FakeHttp(bitopro_trades={"trades": []}))
            adapter._find_today_trade(adapter.now())

    def test_bitopro_existing_bot_order_blocks_duplicate(self):
        http = FakeHttp(bito_orders=[{
            "id": "old-order", "action": "SELL", "executedAmount": "1",
            "remainingAmount": "0", "avgExecutionPrice": "32.25",
        }])
        result = self.run_bito(http)
        self.assertEqual(result.side, "sell")
        self.assertFalse(any(method == "POST" for method, _ in http.calls))

    def test_max_funded_buy_is_ioc_with_price_cap_and_one_twd_fee_target(self):
        http = FakeHttp()
        result = MaxAdapter(live_settings(), http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.side, "buy")
        self.assertEqual(result.execution_type, "spot")
        self.assertGreaterEqual(result.estimated_fee_twd, Decimal("1"))
        self.assertEqual(http.last_body["side"], "buy")
        self.assertEqual(http.last_body["ord_type"], "ioc_limit")
        self.assertIn("price", http.last_body)
        self.assertEqual(sum(method == "POST" for method, _ in http.calls), 1)

    def test_max_never_sells_or_converts_when_twd_is_short(self):
        for twd in ("0", "313", "625"):
            with self.subTest(twd=twd):
                http = FakeHttp(max_twd=twd, max_usdt="10000")
                result = MaxAdapter(live_settings(), http).run(live=True)
                self.assertEqual(result.status, "skipped")
                self.assertIn("不賣 USDT、不閃兌", result.message)
                self.assertFalse(any(method == "POST" for method, _ in http.calls))

    def test_max_existing_spot_fill_uses_current_history_cursor(self):
        http = FakeHttp(max_trades=[{
            "volume": "8", "funds": "258", "market": "usdttwd", "side": "ask",
            "created_at": int(time.time() * 1000), "fee": "0.4", "fee_currency": "twd",
        }])
        result = MaxAdapter(live_settings(), http).run(live=True)
        self.assertEqual(result.side, "sell")  # historical sell, not a new sell
        self.assertEqual(result.actual_fee, Decimal("0.4"))
        self.assertFalse(any(method == "POST" for method, _ in http.calls))
        params = next(kwargs["params"] for _, url, kwargs in http.requests
                      if url.endswith("/wallet/spot/trades"))
        midnight = int(datetime.now(TAIPEI).replace(hour=0, minute=0, second=0,
                                                  microsecond=0).timestamp() * 1000)
        self.assertGreaterEqual(params["timestamp"], midnight)

    def test_max_yesterday_fill_does_not_block_today(self):
        http = FakeHttp(max_trades=[{
            "volume": "8", "market": "usdttwd",
            "created_at": int((datetime.now(TAIPEI) - timedelta(days=1)).timestamp() * 1000),
        }])
        result = MaxAdapter(live_settings(), http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(sum(method == "POST" for method, _ in http.calls), 1)

    def test_max_existing_convert_is_read_only_duplicate_protection(self):
        http = FakeHttp(max_converts=[{
            "from_currency": "usdt", "to_currency": "twd", "from_amount": "1",
            "to_amount": "32", "created_at": int(time.time()),
        }])
        result = MaxAdapter(live_settings(), http).run(live=True)
        self.assertEqual(result.execution_type, "convert")
        self.assertFalse(any(method == "POST" for method, _ in http.calls))

    def test_max_partial_or_zero_fill_never_tops_up(self):
        for filled, expected in (("1", "partial"), ("0", "failed")):
            with self.subTest(filled=filled):
                http = FakeHttp(filled=filled)
                result = MaxAdapter(live_settings(), http).run(live=True)
                self.assertEqual(result.status, expected)
                self.assertEqual(sum(method == "POST" for method, _ in http.calls), 1)

    def test_max_fee_read_failure_preserves_success(self):
        result = MaxAdapter(live_settings(), FakeHttp(fee_error=True)).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertIsNone(result.actual_fee)
        self.assertIn("實收費用讀取未完成", result.message)

    def test_credentials_validation_is_read_only(self):
        for adapter_type in (BitoProAdapter, MaxAdapter):
            http = FakeHttp()
            adapter_type(live_settings(), http).verify_credentials()
            self.assertTrue(all(method == "GET" for method, _ in http.calls))

    def test_missing_credentials_and_maintenance_never_submit(self):
        for adapter_type in (BitoProAdapter, MaxAdapter):
            http = FakeHttp()
            with self.assertRaises(ValueError):
                adapter_type(settings(), http).run(live=True)
            self.assertFalse(any(method == "POST" for method, _ in http.calls))
            http = FakeHttp(market_status="maintenance")
            result = adapter_type(live_settings(), http).run(live=True)
            self.assertEqual(result.status, "skipped")
            self.assertFalse(any(method == "POST" for method, _ in http.calls))

    def test_price_and_quantity_precision_are_applied(self):
        http = FakeHttp(amount_precision=2, price_precision=2)
        self.run_bito(http)
        self.assertEqual(Decimal(http.last_body["amount"]).as_tuple().exponent, -2)
        self.assertEqual(Decimal(http.last_body["price"]).as_tuple().exponent, -2)


class DashboardPolicyTests(unittest.TestCase):
    def configured(self, directory, **kwargs):
        root = Path(directory)
        return replace(
            live_settings(), dashboard_path=root / "dashboard.json",
            state_path=root / "state.json", invoice_records_path=root / "invoices.json",
            **kwargs,
        )

    def test_live_lock_is_enforced_before_any_adapter(self):
        with patch("bot.runner.BitoProAdapter") as constructor, self.assertRaises(ValueError):
            run_all(settings(), live=True)
        constructor.assert_not_called()

    def test_disabled_max_keeps_history_and_never_calls_private_api(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = self.configured(directory, bitopro_enabled=False)
            write_json(configured.dashboard_path, {"events": [{
                "id": "old-max", "date": "2026-09-10", "exchange": "max",
                "mode": "live", "status": "filled", "filled_usdt": "19.7",
            }]})
            write_json(configured.invoice_records_path, [{
                "id": "old-invoice", "exchange": "max", "trade_date": "2026-09-10",
                "status": "confirmed", "amount_twd": "0",
            }])
            http = FakeHttp()
            with patch("bot.runner.MaxAdapter", return_value=MaxAdapter(configured, http)), patch(
                "bot.runner.BitoProAdapter", return_value=BitoProAdapter(configured, http)
            ):
                report = run_all(configured, live=True)
            self.assertEqual(http.calls, [])
            self.assertEqual(report["events"][0]["id"], "old-max")
            self.assertEqual(report["invoice_records"][0]["amount_twd"], "0")
            self.assertFalse(next(row for row in report["exchanges"] if row["id"] == "max")[
                "trading_enabled"
            ])
            self.assertEqual({row["id"] for row in report["exchanges"]}, {"bitopro", "max"})

    def test_fee_data_is_persisted_and_same_day_rerun_never_orders(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = self.configured(directory, max_enabled=True)
            http = FakeHttp()
            with patch("bot.runner.MaxAdapter", return_value=MaxAdapter(configured, http)), patch(
                "bot.runner.BitoProAdapter", return_value=BitoProAdapter(configured, http)
            ), patch("bot.exchanges.bitopro.time.sleep", return_value=None):
                report = run_all(configured, live=True)
                first_calls = len(http.calls)
                second = run_all(configured, live=True)
            self.assertEqual(len(http.calls), first_calls)
            self.assertEqual(len(second["events"]), 2)
            for event in report["events"]:
                saved = read_json(configured.state_path, {})["live_runs"][event["date"]][event["exchange"]]
                self.assertEqual(saved["actual_fee"], event["actual_fee"])
                self.assertEqual(saved["estimated_fee_twd"], event["estimated_fee_twd"])

    def test_old_fee_values_are_not_reestimated_under_new_settings(self):
        result = make_duplicate_result(BitoProAdapter(settings()), {
            "status": "filled", "side": "buy", "filled_usdt": "1", "avg_price_twd": "32",
        })
        self.assertIsNone(result.actual_fee)
        self.assertIsNone(result.estimated_fee_twd)
        self.assertIsNone(result.fee_target_twd)

    def test_refresh_only_makes_no_network_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = self.configured(directory)
            http = FakeHttp()
            with patch("bot.runner.MaxAdapter", return_value=MaxAdapter(configured, http)), patch(
                "bot.runner.BitoProAdapter", return_value=BitoProAdapter(configured, http)
            ):
                report = run_all(configured, live=False, refresh_only=True)
            self.assertEqual(http.calls, [])
            self.assertEqual(report["strategy"], "fee_target")

    def test_refresh_invalidates_legacy_or_changed_quantity_plans(self):
        adapter = BitoProAdapter(settings())
        status = refreshed_exchange_status(adapter, {
            "planned_usdt": "1", "target_eligible": True, "minimum_usdt": "1",
        })
        self.assertIsNone(status["planned_usdt"])
        self.assertFalse(status["target_eligible"])

    def test_invoice_privacy_and_zero_amount_preserved(self):
        records = normalize_invoice_records([{
            "exchange": "BitoPro", "trade_date": "2026-08-05", "status": "confirmed",
            "amount_twd": "0", "masked_number": "AB12345678",
            "detail_url": "https://example.com/invoice?token=secret",
        }], {"bitopro": "bitopro"})
        self.assertEqual(records[0]["masked_number"], "AB••••••78")
        self.assertEqual(records[0]["amount_twd"], "0")
        self.assertIsNone(records[0]["detail_url"])
        self.assertEqual(safe_public_url("https://example.com/invoice"), "https://example.com/invoice")

    def test_repository_record_merges_old_dashboard_details(self):
        record = existing_live_record(
            {"live_runs": {"2026-08-06": {"max": {"status": "filled", "filled_usdt": "8"}}}},
            {"events": [{
                "date": "2026-08-06", "exchange": "max", "mode": "live", "status": "filled",
                "avg_price_twd": "32.25",
            }]}, "2026-08-06", "max",
        )
        self.assertEqual(record["filled_usdt"], "8")
        self.assertEqual(record["avg_price_twd"], "32.25")

    def test_checked_in_dashboard_excludes_nonprogrammatic_exchanges(self):
        dashboard = read_json(Path("public/data/dashboard.json"), {})
        supported = {"bitopro", "max"}
        self.assertEqual({row["id"] for row in dashboard["exchanges"]}, supported)
        self.assertTrue(all(event["exchange"] in supported for event in dashboard["events"]))
        scopes = [(e.get("date"), e.get("exchange"), e.get("mode")) for e in dashboard["events"]]
        self.assertEqual(len(scopes), len(set(scopes)))

    def test_workflow_env_wires_fee_settings_and_cannot_submit_convert(self):
        workflow = Path(".github/workflows/dashboard.yml").read_text(encoding="utf-8")
        for name in ("BITOPRO_FEE_TWD_TARGET", "BITOPRO_TAKER_FEE_RATE",
                     "MAX_FEE_TWD_TARGET", "MAX_TAKER_FEE_RATE", "ORDER_PRICE_SLIPPAGE"):
            self.assertIn("vars." + name, workflow)
        self.assertNotIn("max-test-625", workflow)
        self.assertNotIn("MAX_CONVERT_ENABLED", workflow)
        self.assertNotIn("ORDER_USDT", workflow)


if __name__ == "__main__":
    unittest.main()
