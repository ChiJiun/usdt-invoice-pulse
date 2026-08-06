from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bot.config import Settings
from bot.exchanges.bitopro import BitoProAdapter
from bot.exchanges.max_exchange import MaxAdapter
from bot.gmail_invoices import (
    DEFAULT_QUERIES,
    TAIPEI,
    GmailConfig,
    choose_trade_date,
    message_text,
    parse_amount,
    parse_invoice_number,
    sync_gmail_invoices,
)
from bot.runner import existing_live_record, normalize_invoice_records, safe_public_url
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
        invoice_records_path=root / "invoice-records.json",
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
        bitopro_trades: list[dict] | None = None,
        max_trades: list[dict] | None = None,
    ):
        self.calls = []
        self.bitopro_twd = bitopro_twd
        self.bitopro_usdt = bitopro_usdt
        self.max_twd = max_twd
        self.max_usdt = max_usdt
        self.max_converts = max_converts or []
        self.bitopro_trades = bitopro_trades or []
        self.max_trades = max_trades or []
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
        if method == "GET" and url.endswith("/api/v3/wallet/spot/trades"):
            return self.max_trades
        if method == "GET" and url.endswith("/orders/trades/usdt_twd"):
            return {"data": self.bitopro_trades}
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
        self.assertEqual(buy, "buy")
        self.assertEqual(sell, "sell")
        self.assertEqual(skipped, "none")

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

    def test_bitopro_existing_today_trade_blocks_duplicate(self):
        configured = replace(
            settings(),
            bitopro_email="member@example.invalid",
            bitopro_api_key="key",
            bitopro_api_secret="secret",
        )
        existing = {
            "tradeId": "existing-bito-trade",
            "orderId": "existing-bito-order",
            "price": "32.25",
            "action": "BUY",
            "baseAmount": "1",
            "quoteAmount": "32.25",
            "createdTimestamp": int(time.time() * 1000),
        }
        http = FakeHttp(bitopro_trades=[existing])
        result = BitoProAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.side, "buy")
        self.assertIn("今日已有", result.message)
        self.assertFalse(any(method == "POST" for method, _ in http.calls))

    def test_max_live_prefers_buy_and_uses_eight_usdt(self):
        configured = replace(settings(), max_api_key="key", max_api_secret="secret")
        http = FakeHttp(max_twd="1000", max_usdt="100")
        result = MaxAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.side, "buy")
        self.assertEqual(result.execution_type, "spot")
        self.assertEqual(http.last_body["volume"], "8")

    def test_max_existing_today_spot_trade_blocks_duplicate(self):
        configured = replace(settings(), max_api_key="key", max_api_secret="secret")
        existing = {
            "id": 991,
            "order_id": 881,
            "wallet_type": "spot",
            "price": "32.25",
            "volume": "8",
            "funds": "258",
            "market": "usdttwd",
            "side": "bid",
            "created_at": int(time.time() * 1000),
        }
        http = FakeHttp(max_trades=[existing])
        result = MaxAdapter(configured, http).run(live=True)
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.side, "buy")
        self.assertIn("今日已有", result.message)
        self.assertFalse(any(method == "POST" for method, _ in http.calls))

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
    def test_repository_live_record_is_used_before_a_new_order(self):
        state = {
            "live_runs": {
                "2026-08-06": {
                    "max": {
                        "status": "filled",
                        "side": "buy",
                        "execution_type": "spot",
                        "filled_usdt": "8",
                    }
                }
            }
        }
        dashboard = {
            "events": [
                {
                    "date": "2026-08-06",
                    "exchange": "max",
                    "mode": "live",
                    "status": "filled",
                    "avg_price_twd": "32.25",
                }
            ]
        }
        record = existing_live_record(state, dashboard, "2026-08-06", "max")
        self.assertIsNotNone(record)
        self.assertEqual(record["filled_usdt"], "8")
        self.assertEqual(record["avg_price_twd"], "32.25")

    def test_invoice_record_sanitization_masks_identifiers_and_rejects_token_urls(self):
        records = normalize_invoice_records(
            [
                {
                    "id": "sample",
                    "exchange": "BitoPro",
                    "trade_date": "2026-08-05",
                    "status": "confirmed",
                    "masked_number": "AB12345678",
                    "detail_url": "https://example.com/invoice?token=secret",
                }
            ],
            {"bitopro": "bitopro"},
        )
        self.assertEqual(records[0]["masked_number"], "AB••••••78")
        self.assertEqual(records[0]["status"], "confirmed")
        self.assertIsNone(records[0]["detail_url"])
        self.assertEqual(
            safe_public_url("https://example.com/invoice"),
            "https://example.com/invoice",
        )

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
        self.assertEqual(
            {row["exchange"] for row in dashboard["daily_status"]["exchanges"]},
            supported,
        )
        self.assertIn("invoice_records", dashboard)
        self.assertIn("invoice_sync", dashboard)


class FakeGmailClient:
    def __init__(self, messages: dict[str, dict]):
        self.messages = messages

    def list_message_ids(self, query: str, maximum: int = 50):
        return list(self.messages)

    def get_message(self, message_id: str):
        return self.messages[message_id]

    def get_attachment(self, message_id: str, attachment_id: str):
        raise AssertionError("inline test email should not request an attachment")


def gmail_message(message_id: str, html: str, received_at: datetime) -> dict:
    encoded = base64.urlsafe_b64encode(html.encode()).decode().rstrip("=")
    return {
        "id": message_id,
        "internalDate": str(int(received_at.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "Subject", "value": "電子發票開立通知"},
                {"name": "From", "value": "invoice@example.invalid"},
            ],
            "body": {"data": encoded},
        },
    }


class GmailInvoiceTests(unittest.TestCase):
    def test_parser_extracts_taiwan_invoice_fields_without_html(self):
        message = gmail_message(
            "message-1",
            "<h1>幣託科技 電子發票</h1><p>發票號碼 AB-12345678</p>"
            "<p>發票金額：NT$ 1</p>",
            datetime(2026, 8, 7, 9, 0, tzinfo=TAIPEI),
        )
        text = message_text(message)
        self.assertNotIn("<h1>", text)
        self.assertEqual(parse_invoice_number(text), "AB12345678")
        self.assertEqual(parse_amount(text), "1")

    def test_trade_date_is_not_guessed_when_multiple_dates_are_possible(self):
        selected = choose_trade_date(
            explicit_date=None,
            received="2026-08-07",
            available_dates=["2026-08-05", "2026-08-06"],
            already_matched=set(),
        )
        self.assertIsNone(selected)

    def test_sync_writes_only_masked_record_and_matches_explicit_trade_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard_path = root / "dashboard.json"
            records_path = root / "invoice-records.json"
            dashboard_path.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "exchange": "bitopro",
                                "date": "2026-08-05",
                                "mode": "live",
                                "status": "filled",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            records_path.write_text("[]", encoding="utf-8")
            received_at = datetime(2026, 8, 7, 9, 0, tzinfo=TAIPEI)
            message = gmail_message(
                "private-gmail-message-id",
                "<p>幣託科技 電子發票</p><p>發票號碼 AB12345678</p>"
                "<p>交易日期：2026/08/05</p><p>發票開立日期：2026/08/07</p>"
                "<p>發票金額：1</p>",
                received_at,
            )
            config = GmailConfig("client", "secret", "refresh", DEFAULT_QUERIES)
            status = sync_gmail_invoices(
                config,
                dashboard_path=dashboard_path,
                invoice_records_path=records_path,
                client=FakeGmailClient({"private-gmail-message-id": message}),
                now=received_at,
            )
            records = json.loads(records_path.read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "success")
            self.assertEqual(records[0]["trade_date"], "2026-08-05")
            self.assertEqual(records[0]["issued_date"], "2026-08-07")
            self.assertEqual(records[0]["masked_number"], "AB••••••78")
            serialized = json.dumps(records, ensure_ascii=False)
            self.assertNotIn("AB12345678", serialized)
            self.assertNotIn("private-gmail-message-id", serialized)

    def test_sync_recognizes_max_notice_with_roc_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard_path = root / "dashboard.json"
            records_path = root / "invoice-records.json"
            dashboard_path.write_text('{"events": []}', encoding="utf-8")
            records_path.write_text("[]", encoding="utf-8")
            received_at = datetime(2026, 8, 7, 10, 0, tzinfo=TAIPEI)
            message = gmail_message(
                "max-message-id",
                "<p>現代財富科技 電子發票開立通知</p>"
                "<p>發票號碼 CD87654321</p><p>開立日期：115年08月07日</p>"
                "<p>消費日期：115年08月06日</p><p>總計：新台幣 1 元</p>",
                received_at,
            )
            status = sync_gmail_invoices(
                GmailConfig("client", "secret", "refresh", DEFAULT_QUERIES),
                dashboard_path=dashboard_path,
                invoice_records_path=records_path,
                client=FakeGmailClient({"max-message-id": message}),
                now=received_at,
            )
            records = json.loads(records_path.read_text(encoding="utf-8"))
            self.assertEqual(status["records_updated"], 1)
            self.assertEqual(records[0]["exchange"], "max")
            self.assertEqual(records[0]["trade_date"], "2026-08-06")
            self.assertEqual(records[0]["issued_date"], "2026-08-07")
            self.assertEqual(records[0]["amount_twd"], "1")


if __name__ == "__main__":
    unittest.main()
