from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
from typing import Any

from bot.models import estimated_invoice_status
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
    invoice_rule = "依每日實收交易手續費彙總，四捨五入滿一元才開立"
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

        fee = target * ask * self.settings.max_taker_fee_rate
        if not live:
            return self.base_result(
                status="simulated",
                side="buy",
                requested_usdt=target,
                filled_usdt=target,
                avg_price_twd=ask,
                fee_twd=fee,
                invoice_status=estimated_invoice_status(fee),
                message="已自動提高至 MAX 最低量；模擬優先買入，正式模式會依餘額改為賣出或略過",
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
            return self.base_result(
                status="skipped",
                requested_usdt=target,
                message="TWD 不足以買入，扣除保留量後的 USDT 也不足以賣出；本日略過",
                live=True,
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
        estimated_fee = executed * avg_price * self.settings.max_taker_fee_rate
        status = "filled" if executed >= target else "partial" if executed > 0 else "failed"
        message = (
            "訂單已全數成交；等待電子發票開立通知"
            if status == "filled"
            else "訂單未完整成交，請至 MAX 檢查訂單狀態"
        )
        return self.base_result(
            status=status,
            side=side,
            requested_usdt=target,
            filled_usdt=executed,
            avg_price_twd=avg_price if executed else None,
            fee_twd=estimated_fee if executed else None,
            invoice_status=(
                estimated_invoice_status(estimated_fee)
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
            "target_eligible": (
                self.planned_usdt >= self.minimum_usdt
                and self.planned_usdt > Decimal("0")
            ),
            "invoice_rule": self.invoice_rule,
            "today_status": today_status,
            "note": (
                "只做 USDT/TWD；目前最低 "
                f"{self.minimum_usdt.normalize():f} USDT／"
                f"新台幣 {self.minimum_twd.normalize():f} 元，程式會自動提高計畫量。"
            ),
        }
