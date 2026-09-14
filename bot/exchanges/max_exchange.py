from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from bot.models import decimal_text
from bot.trading import choose_trade_side, fee_target_quantity, quantity_step

from .base import ExchangeAdapter


class MaxAdapter(ExchangeAdapter):
    id = "max"
    name = "MAX Exchange"
    short_name = "MAX"
    accent = "#1aa679"
    minimum_usdt = Decimal("8")
    minimum_twd = Decimal("250")
    fee_target_setting = "max_fee_twd_target"
    fee_rate_setting = "max_taker_fee_rate"
    price_precision = 3
    base_url = "https://max-api.maicoin.com"
    market = "usdttwd"

    @staticmethod
    def encode_signature(params: dict[str, Any], path: str, secret: str) -> tuple[str, str]:
        signed = {**params, "path": path}
        payload = base64.b64encode(
            json.dumps(signed, separators=(",", ":")).encode()
        ).decode()
        signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return payload, signature

    def _auth_headers(self, params: dict[str, Any], path: str) -> dict[str, str]:
        payload, signature = self.encode_signature(
            params, path, self.settings.max_api_secret
        )
        return {
            "X-MAX-ACCESSKEY": self.settings.max_api_key,
            "X-MAX-PAYLOAD": payload,
            "X-MAX-SIGNATURE": signature,
        }

    def _snapshot(self) -> tuple[Decimal, Decimal, Decimal, Decimal, int, str]:
        markets = self.http.request_json("GET", f"{self.base_url}/api/v3/markets")
        market = next(row for row in markets if row["id"] == self.market)
        ticker = self.http.request_json(
            "GET", f"{self.base_url}/api/v3/ticker", params={"market": self.market}
        )
        minimum_base = Decimal(str(market["min_base_amount"]))
        minimum_quote = Decimal(str(market["min_quote_amount"]))
        base_precision = int(market["base_unit_precision"])
        self.price_precision = int(market.get("quote_unit_precision", 3))
        market_status = str(market.get("status", "active"))
        bid = Decimal(ticker["buy"])
        ask = Decimal(ticker["sell"])
        self.minimum_usdt = minimum_base
        self.minimum_twd = minimum_quote
        return bid, ask, minimum_base, minimum_quote, base_precision, market_status

    def _validate_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("MAX_API_KEY", self.settings.max_api_key),
                ("MAX_API_SECRET", self.settings.max_api_secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"MAX 缺少 GitHub Secrets：{', '.join(missing)}")

    def verify_credentials(self) -> None:
        """只驗證簽章與帳戶讀取權限，不送出訂單。"""
        self._validate_credentials()
        self._account_balances()

    def _account_balances(self) -> list[dict[str, Any]]:
        path = "/api/v3/wallet/spot/accounts"
        params = {"nonce": int(time.time() * 1000)}
        response = self.http.request_json(
            "GET",
            f"{self.base_url}{path}",
            params=params,
            headers=self._auth_headers(params, path),
        )
        if not isinstance(response, list):
            raise RuntimeError("MAX 帳戶驗證回應格式不符預期")
        return response

    @staticmethod
    def _available_balance(balances: list[dict[str, Any]], currency: str) -> Decimal:
        return next(
            (
                Decimal(str(balance.get("balance", "0")))
                for balance in balances
                if str(balance.get("currency", "")).lower() == currency
            ),
            Decimal("0"),
        )

    def _find_today_convert(self) -> dict[str, Any] | None:
        path = "/api/v3/converts"
        params = {
            "nonce": int(time.time() * 1000),
            "order": "desc",
            "limit": 50,
        }
        history = self.http.request_json(
            "GET",
            f"{self.base_url}{path}",
            params=params,
            headers=self._auth_headers(params, path),
        )
        if not isinstance(history, list):
            raise RuntimeError("MAX 閃兌紀錄回應格式不符預期")
        start_of_day = self.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_timestamp = int(start_of_day.timestamp())
        for order in history:
            currencies = {
                str(order.get("from_currency", "")).lower(),
                str(order.get("to_currency", "")).lower(),
            }
            if (
                currencies == {"twd", "usdt"}
                and int(order.get("created_at", 0)) >= start_timestamp
            ):
                return order
        return None

    def _find_today_spot_trade(self) -> dict[str, Any] | None:
        path = "/api/v3/wallet/spot/trades"
        current = self.now()
        start_of_day = current.replace(hour=0, minute=0, second=0, microsecond=0)
        params = {
            "nonce": int(time.time() * 1000),
            "market": self.market,
            "timestamp": int(current.timestamp() * 1000),
            "order": "desc",
            "limit": 1000,
        }
        history = self.http.request_json(
            "GET",
            f"{self.base_url}{path}",
            params=params,
            headers=self._auth_headers(params, path),
        )
        if not isinstance(history, list):
            raise RuntimeError("MAX 現貨成交紀錄回應格式不符預期")
        start_timestamp = int(start_of_day.timestamp() * 1000)
        valid = [
            trade
            for trade in history
            if str(trade.get("market", "")).lower() == self.market
            and Decimal(str(trade.get("volume", "0"))) > 0
            and start_timestamp <= int(trade.get("created_at", 0))
            <= int(current.timestamp() * 1000)
        ]
        return max(valid, key=lambda trade: int(trade.get("created_at", 0)), default=None)

    def _spot_trade_result(self, trade: dict[str, Any]):
        raw_side = str(trade.get("side", "")).lower()
        side = "buy" if raw_side == "bid" else "sell" if raw_side == "ask" else "none"
        filled = Decimal(str(trade.get("volume", "0")))
        funds = Decimal(str(trade.get("funds", "0")))
        price = Decimal(str(trade.get("price", "0")))
        if not price and filled > 0:
            price = funds / filled
        return self.base_result(
            status="filled",
            side=side,
            execution_type="spot",
            requested_usdt=filled,
            filled_usdt=filled,
            avg_price_twd=price or None,
            invoice_status="pending_confirmation",
            actual_fee=(Decimal(str(trade["fee"]))
                        if trade.get("fee") is not None and trade.get("fee_currency") else None),
            fee_currency=trade.get("fee_currency"),
            message="官方 API 偵測到今日已有 USDT/TWD 現貨成交，已沿用紀錄並停止新增訂單",
            live=True,
        )

    def _convert_result(self, order: dict[str, Any], *, message: str):
        from_currency = str(order.get("from_currency", "")).lower()
        to_currency = str(order.get("to_currency", "")).lower()
        from_amount = Decimal(str(order.get("from_amount", "0")))
        to_amount = Decimal(str(order.get("to_amount", "0")))
        if from_currency == "twd" and to_currency == "usdt":
            side = "buy"
            filled_usdt = to_amount
            avg_price = from_amount / to_amount if to_amount > 0 else None
        elif from_currency == "usdt" and to_currency == "twd":
            side = "sell"
            filled_usdt = from_amount
            avg_price = to_amount / from_amount if from_amount > 0 else None
        else:
            raise RuntimeError("MAX 閃兌結果不是 USDT/TWD")

        return self.base_result(
            status="filled" if filled_usdt > 0 else "failed",
            side=side,
            execution_type="convert",
            requested_usdt=filled_usdt or self.planned_usdt,
            filled_usdt=filled_usdt,
            avg_price_twd=avg_price,
            invoice_status=(
                "pending_confirmation" if filled_usdt > 0 else "not_applicable"
            ),
            message=message,
            live=True,
        )

    def _order_fees(self, client_oid: str) -> tuple[Decimal | None, str | None]:
        path = "/api/v3/order/trades"
        params = {"nonce": int(time.time() * 1000), "client_oid": client_oid}
        trades = self.http.request_json(
            "GET", f"{self.base_url}{path}", params=params,
            headers=self._auth_headers(params, path),
        )
        if not isinstance(trades, list):
            raise RuntimeError("MAX 訂單費用回應格式不符預期")
        if not trades or not all(
            isinstance(row, dict) and row.get("fee") is not None and row.get("fee_currency")
            for row in trades
        ):
            return None, None
        currencies = {str(row["fee_currency"]).lower() for row in trades}
        if len(currencies) != 1:
            return None, None
        fee = sum((Decimal(str(row["fee"])) for row in trades), Decimal("0"))
        return fee, currencies.pop()

    def run(self, *, live: bool):
        _, ask, minimum_base, minimum_quote, base_precision, market_status = self._snapshot()
        price_step = quantity_step(self.price_precision)
        buy_limit = (ask * (Decimal("1") + self.settings.price_slippage)).quantize(
            price_step, rounding=ROUND_UP
        )
        price_floor = (ask * (Decimal("1") - self.settings.price_slippage)).quantize(
            price_step, rounding=ROUND_DOWN
        )
        target = fee_target_quantity(
            fee_twd=self.fee_target_twd,
            fee_rate=self.fee_rate,
            minimum_base=minimum_base,
            minimum_quote=minimum_quote,
            price_floor_twd=price_floor,
            step=quantity_step(base_precision),
        )
        self.planned_usdt = target
        if market_status != "active":
            return self.base_result(
                status="skipped", requested_usdt=target, avg_price_twd=ask,
                message=f"USDT/TWD 市場狀態為 {market_status}，本日略過", live=live,
            )

        if not live:
            return self.base_result(
                status="simulated", side="buy", execution_type="spot",
                requested_usdt=target, filled_usdt=target, avg_price_twd=ask,
                estimated_fee_twd=target * ask * self.fee_rate,
                invoice_status="not_applicable",
                message=(
                    f"依 NT$ {self.fee_target_twd.normalize():f} 手續費目標換算買入量；"
                    "正式模式只在 TWD 足夠時買入，不賣出、不閃兌；不保證開票"
                ),
                live=False,
            )

        self._validate_credentials()
        existing_spot = self._find_today_spot_trade()
        if existing_spot:
            return self._spot_trade_result(existing_spot)
        # Read historical converts for duplicate protection only. This adapter
        # must never submit a convert or sell under the new buy-only policy.
        existing_convert = self._find_today_convert()
        if existing_convert:
            return self._convert_result(
                existing_convert,
                message="官方 API 偵測到今日既有 USDT/TWD 閃兌，已沿用成交並停止新增交易",
            )

        balances = self._account_balances()
        available_twd = self._available_balance(balances, "twd")
        side = choose_trade_side(
            available_twd=available_twd, available_usdt=Decimal("0"),
            target_usdt=target, buy_price_twd=buy_limit,
            buy_buffer_rate=self.fee_rate, allow_sell=False,
        )
        if side == "none":
            required = target * buy_limit * (Decimal("1") + self.fee_rate)
            return self.base_result(
                status="skipped", requested_usdt=target,
                message=(
                    f"MAX 只買入：可用 TWD {available_twd.normalize():f} 元，"
                    f"計畫需約 {required.quantize(Decimal('0.01'), rounding=ROUND_UP):f} 元"
                    "（含價格與費用緩衝）；本日略過，不賣 USDT、不閃兌"
                ),
                live=True,
            )

        path = "/api/v3/wallet/spot/order"
        client_oid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"max-{self.now().date()}"))
        body = {
            "nonce": int(time.time() * 1000), "market": self.market,
            "side": "buy", "volume": str(target), "price": str(buy_limit),
            "ord_type": "ioc_limit", "client_oid": client_oid,
        }
        detail = self.http.request_json(
            "POST", f"{self.base_url}{path}", body=body,
            headers=self._auth_headers(body, path),
        )
        detail_path = "/api/v3/order"
        for _ in range(6):
            if detail.get("state") in {"done", "cancel"}:
                break
            time.sleep(2)
            params = {"nonce": int(time.time() * 1000), "client_oid": client_oid}
            detail = self.http.request_json(
                "GET", f"{self.base_url}{detail_path}", params=params,
                headers=self._auth_headers(params, detail_path),
            )

        executed = Decimal(detail.get("executed_volume", "0"))
        average = Decimal(detail.get("avg_price", "0")) or ask
        status = "filled" if executed >= target else "partial" if executed > 0 else "failed"
        message = (
            "買入已全數成交；發票待實際開立確認"
            if status == "filled"
            else "IOC 買單未完整成交；未成交部分不留掛單、不自動補單，請至 MAX 核對"
        )
        estimated_fee = executed * average * self.fee_rate
        if executed > 0 and estimated_fee < self.fee_target_twd:
            message += "；預估費用未達目標"
        actual_fee = None
        fee_currency = None
        if executed > 0:
            # Fees live on the trade endpoint, not the order response. A fee
            # read failure must not hide a successful fill or trigger a retry.
            try:
                actual_fee, fee_currency = self._order_fees(client_oid)
            except Exception:
                message += "；實收費用讀取未完成，請至官方成交紀錄確認"
        return self.base_result(
            status=status, side="buy", execution_type="spot", requested_usdt=target,
            filled_usdt=executed, avg_price_twd=average if executed else None,
            estimated_fee_twd=estimated_fee if executed else None,
            actual_fee=actual_fee, fee_currency=fee_currency,
            invoice_status="pending_confirmation" if executed else "not_applicable",
            message=message, live=True,
        )

    def public_status(self, today_status: str = "waiting") -> dict[str, object]:
        return {
            "id": self.id, "name": self.name, "short_name": self.short_name,
            "accent": self.accent, "minimum_usdt": str(self.minimum_usdt),
            "minimum_twd": str(self.minimum_twd),
            "fee_target_twd": decimal_text(self.fee_target_twd),
            "fee_rate": decimal_text(self.fee_rate),
            "turnover_target_twd": decimal_text(self.turnover_target_twd),
            "trade_policy": "buy_only",
            "planned_usdt": decimal_text(self.planned_usdt),
            "convert_supported": False,
            "target_eligible": bool(self.planned_usdt and self.planned_usdt >= self.minimum_usdt),
            "today_status": today_status,
            "note": (
                f"只買 USDT/TWD；以 NT$ {self.fee_target_twd.normalize():f} 預估手續費為目標，"
                "TWD 不足就略過；不賣 USDT、不閃兌、不保證開票。"
            ),
        }
