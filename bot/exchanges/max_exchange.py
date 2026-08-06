from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
from typing import Any

from bot.http import ApiError
from bot.trading import choose_trade_side, effective_target, quantity_step

from .base import ExchangeAdapter


class MaxAdapter(ExchangeAdapter):
    id = "max"
    name = "MAX Exchange"
    short_name = "MAX"
    accent = "#1aa679"
    api_status = "available"
    minimum_usdt = Decimal("8")
    minimum_twd = Decimal("250")
    planned_usdt = Decimal("8")
    invoice_rule = "現貨或閃兌有成交即列為待確認；以實際電子發票為準"
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

        serial = str(order.get("sn", ""))
        return self.base_result(
            status="filled" if filled_usdt > 0 else "failed",
            side=side,
            execution_type="convert",
            requested_usdt=filled_usdt or self.settings.max_convert_usdt_amount,
            filled_usdt=filled_usdt,
            avg_price_twd=avg_price,
            invoice_status=(
                "pending_confirmation" if filled_usdt > 0 else "not_applicable"
            ),
            message=message,
            live=True,
            reference_hash=(
                hashlib.sha256(serial.encode()).hexdigest()[:10] if serial else None
            ),
        )

    def _run_convert_fallback(
        self,
        balances: list[dict[str, Any]],
        *,
        target: Decimal,
        reference_price: Decimal,
    ):
        if not self.settings.max_convert_enabled:
            return self.base_result(
                status="skipped",
                requested_usdt=target,
                message="現貨資金不足，MAX 閃兌 fallback 未啟用；本日略過",
                live=True,
            )

        existing = self._find_today_convert()
        if existing:
            return self._convert_result(
                existing,
                message="偵測到今日既有 USDT/TWD 閃兌，已沿用成交並阻止重複交易",
            )

        available_twd = self._available_balance(balances, "twd")
        available_usdt = self._available_balance(balances, "usdt")
        sellable_usdt = max(
            available_usdt - self.settings.usdt_reserve, Decimal("0")
        )
        if available_twd > 0:
            from_currency = "twd"
            to_currency = "usdt"
            amount = min(available_twd, self.settings.max_convert_twd_amount)
        elif sellable_usdt > 0:
            from_currency = "usdt"
            to_currency = "twd"
            amount = min(sellable_usdt, self.settings.max_convert_usdt_amount)
        else:
            return self.base_result(
                status="skipped",
                requested_usdt=target,
                message="現貨與閃兌都沒有可用的 TWD／USDT；本日略過",
                live=True,
            )

        path = "/api/v3/convert"
        body = {
            "nonce": int(time.time() * 1000),
            "from_currency": from_currency,
            "to_currency": to_currency,
            "from_amount": str(amount),
        }
        try:
            order = self.http.request_json(
                "POST",
                f"{self.base_url}{path}",
                body=body,
                headers=self._auth_headers(body, path),
            )
        except ApiError as exc:
            return self.base_result(
                status="failed",
                side="buy" if from_currency == "twd" else "sell",
                execution_type="convert",
                requested_usdt=(
                    amount / reference_price
                    if from_currency == "twd"
                    else amount
                ),
                message=f"現貨資金不足，MAX 低額閃兌嘗試未成功：{exc}",
                live=True,
            )
        if not isinstance(order, dict):
            raise RuntimeError("MAX 閃兌回應格式不符預期")
        return self._convert_result(
            order,
            message="現貨資金不足，已改用 MAX 低額閃兌成交；發票待實際開立確認",
        )

    def run(self, *, live: bool):
        bid, ask, minimum_base, minimum_quote, base_precision, market_status = (
            self._snapshot()
        )
        target = effective_target(
            self.settings.target_usdt,
            minimum_base,
            minimum_quote,
            bid,
            quantity_step(base_precision),
        )
        self.planned_usdt = target
        if market_status != "active":
            return self.base_result(
                status="skipped",
                requested_usdt=target,
                avg_price_twd=ask,
                message=f"USDT/TWD 市場狀態為 {market_status}，本日略過",
                live=live,
            )

        if not live:
            return self.base_result(
                status="simulated",
                side="buy",
                execution_type="spot",
                requested_usdt=target,
                filled_usdt=target,
                avg_price_twd=ask,
                invoice_status="not_applicable",
                message="已自動提高至 MAX 最低量；正式模式優先現貨，資金不足時可改試低額閃兌",
                live=False,
            )

        self._validate_credentials()
        balances = self._account_balances()
        available_twd = self._available_balance(balances, "twd")
        available_usdt = self._available_balance(balances, "usdt")
        decision = choose_trade_side(
            available_twd=available_twd,
            available_usdt=available_usdt,
            target_usdt=target,
            buy_price_twd=ask,
            buy_buffer_rate=(
                self.settings.price_slippage + self.settings.max_taker_fee_rate
            ),
            usdt_reserve=self.settings.usdt_reserve,
        )
        if decision.side == "none":
            return self._run_convert_fallback(
                balances,
                target=target,
                reference_price=ask,
            )

        side = decision.side
        path = "/api/v3/wallet/spot/order"
        client_oid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"max-{self.now().date()}"))
        body = {
            "nonce": int(time.time() * 1000),
            "market": self.market,
            "side": side,
            "volume": str(target),
            "ord_type": "market",
            "client_oid": client_oid,
        }
        created = self.http.request_json(
            "POST",
            f"{self.base_url}{path}",
            body=body,
            headers=self._auth_headers(body, path),
        )
        detail = created
        detail_path = "/api/v3/order"
        for _ in range(6):
            if detail.get("state") == "done":
                break
            time.sleep(2)
            params = {"nonce": int(time.time() * 1000), "client_oid": client_oid}
            detail = self.http.request_json(
                "GET",
                f"{self.base_url}{detail_path}",
                params=params,
                headers=self._auth_headers(params, detail_path),
            )

        executed = Decimal(detail.get("executed_volume", "0"))
        avg_price = Decimal(detail.get("avg_price", "0")) or ask
        status = "filled" if executed >= target else "partial" if executed > 0 else "failed"
        message = (
            "訂單已全數成交；等待電子發票開立通知"
            if status == "filled"
            else "訂單未完整成交，請至 MAX 檢查訂單狀態"
        )
        return self.base_result(
            status=status,
            side=side,
            execution_type="spot",
            requested_usdt=target,
            filled_usdt=executed,
            avg_price_twd=avg_price if executed else None,
            invoice_status=(
                "pending_confirmation"
                if executed
                else "not_applicable"
            ),
            message=f"{'買入' if side == 'buy' else '賣出'}：{message}",
            live=True,
            reference_hash=hashlib.sha256(client_oid.encode()).hexdigest()[:10],
        )

    def public_status(self, today_status: str = "waiting") -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "accent": self.accent,
            "api_status": self.api_status,
            "minimum_usdt": str(self.minimum_usdt),
            "minimum_twd": str(self.minimum_twd),
            "planned_usdt": str(self.planned_usdt),
            "convert_supported": True,
            "target_eligible": (
                self.planned_usdt >= self.minimum_usdt
                and self.planned_usdt > Decimal("0")
            ),
            "invoice_rule": self.invoice_rule,
            "today_status": today_status,
            "note": (
                "只做 USDT/TWD；目前最低 "
                f"{self.minimum_usdt.normalize():f} USDT／"
                f"新台幣 {self.minimum_twd.normalize():f} 元；現貨資金不足時可改試低額閃兌。"
            ),
        }
