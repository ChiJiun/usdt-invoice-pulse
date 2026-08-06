from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import zlib
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from bot.trading import choose_trade_side, effective_target, quantity_step

from .base import ExchangeAdapter


class BitoProAdapter(ExchangeAdapter):
    id = "bitopro"
    name = "BitoPro"
    short_name = "BP"
    accent = "#2f6bff"
    api_status = "available"
    minimum_usdt = Decimal("1")
    minimum_twd: Decimal | None = None
    planned_usdt = Decimal("1")
    invoice_rule = "有成交即列為待確認；是否開立與金額以實際電子發票為準"
    base_url = "https://api.bitopro.com/v3"
    pair = "usdt_twd"

    @staticmethod
    def sign_payload(payload: str, secret: str) -> str:
        return hmac.new(secret.encode(), payload.encode(), hashlib.sha384).hexdigest()

    def _post_headers(self, body: dict[str, Any]) -> dict[str, str]:
        payload = base64.b64encode(
            json.dumps(body, separators=(",", ":")).encode()
        ).decode()
        return {
            "X-BITOPRO-APIKEY": self.settings.bitopro_api_key,
            "X-BITOPRO-PAYLOAD": payload,
            "X-BITOPRO-SIGNATURE": self.sign_payload(
                payload, self.settings.bitopro_api_secret
            ),
        }

    def _read_headers(self) -> dict[str, str]:
        auth = {
            "identity": self.settings.bitopro_email,
            "nonce": int(time.time() * 1000),
        }
        payload = base64.b64encode(
            json.dumps(auth, separators=(",", ":")).encode()
        ).decode()
        return {
            "X-BITOPRO-APIKEY": self.settings.bitopro_api_key,
            "X-BITOPRO-PAYLOAD": payload,
            "X-BITOPRO-SIGNATURE": self.sign_payload(
                payload, self.settings.bitopro_api_secret
            ),
        }

    def _market_snapshot(self) -> tuple[Decimal, Decimal, Decimal, int, bool]:
        order_book = self.http.request_json(
            "GET", f"{self.base_url}/order-book/{self.pair}", params={"limit": 1}
        )
        bid = Decimal(order_book["bids"][0]["price"])
        ask = Decimal(order_book["asks"][0]["price"])
        pairs = self.http.request_json(
            "GET", f"{self.base_url}/provisioning/trading-pairs"
        )
        pair_info = next(row for row in pairs["data"] if row["pair"] == self.pair)
        minimum = Decimal(pair_info["minLimitBaseAmount"])
        amount_precision = int(pair_info["amountPrecision"])
        maintain = bool(pair_info.get("maintain", False))
        return bid, ask, minimum, amount_precision, maintain

    def _validate_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("BITOPRO_EMAIL", self.settings.bitopro_email),
                ("BITOPRO_API_KEY", self.settings.bitopro_api_key),
                ("BITOPRO_API_SECRET", self.settings.bitopro_api_secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"BitoPro 缺少 GitHub Secrets：{', '.join(missing)}")

    def _account_balances(self) -> list[dict[str, Any]]:
        response = self.http.request_json(
            "GET",
            f"{self.base_url}/accounts/balance",
            headers=self._read_headers(),
        )
        if not isinstance(response, dict) or not isinstance(response.get("data"), list):
            raise RuntimeError("BitoPro 帳戶驗證回應格式不符預期")
        return response["data"]

    @staticmethod
    def _available_balance(balances: list[dict[str, Any]], currency: str) -> Decimal:
        return next(
            (
                Decimal(str(balance.get("available", "0")))
                for balance in balances
                if str(balance.get("currency", "")).lower() == currency
            ),
            Decimal("0"),
        )

    def verify_credentials(self) -> None:
        """只驗證簽章與帳戶讀取權限，不送出訂單。"""
        self._validate_credentials()
        self._account_balances()

    def run(self, *, live: bool):
        bid, ask, minimum, amount_precision, maintain = self._market_snapshot()
        self.minimum_usdt = minimum
        target = effective_target(
            self.settings.target_usdt,
            minimum,
            Decimal("0"),
            bid,
            quantity_step(amount_precision),
        )
        self.planned_usdt = target
        if maintain:
            return self.base_result(
                status="skipped",
                requested_usdt=target,
                message="USDT/TWD 交易對維護中，未送出訂單",
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
                message="已自動套用官方最低量；模擬優先買入，正式模式會依餘額改為賣出或略過",
                live=False,
            )

        self._validate_credentials()
        current = self.now()
        client_id = zlib.crc32(f"bitopro-{current.date()}".encode()) & 0x7FFFFFFF
        client_id = client_id or 1

        # The repository state prevents ordinary reruns. This exchange-side lookup
        # also covers the harder case where the order was accepted but the Action
        # stopped before it could commit the updated state file.
        start_of_day = current.replace(hour=0, minute=0, second=0, microsecond=0)
        existing = self.http.request_json(
            "GET",
            f"{self.base_url}/orders/all/{self.pair}",
            params={
                "startTimestamp": int(start_of_day.timestamp() * 1000),
                "endTimestamp": int(current.timestamp() * 1000),
                "statusKind": "ALL",
                "clientId": client_id,
                "limit": 10,
            },
            headers=self._read_headers(),
        ).get("data", [])
        if existing:
            detail = existing[0]
            detail_side = str(detail.get("action", "")).lower()
            side = detail_side if detail_side in {"buy", "sell"} else "none"
            order_id = str(detail.get("id", ""))
            executed = Decimal(detail.get("executedAmount", "0"))
            remaining = Decimal(detail.get("remainingAmount", str(target)))
            if order_id and remaining > 0:
                self.http.request_json(
                    "DELETE",
                    f"{self.base_url}/orders/{self.pair}/{order_id}",
                    params={"isAllStrategyCanceled": "true"},
                    headers=self._read_headers(),
                )
            avg_price = Decimal(detail.get("avgExecutionPrice", "0")) or ask
            status = "filled" if executed >= target else "partial" if executed > 0 else "failed"
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
                message="偵測到今日既有自動訂單，已沿用結果並阻止重複交易",
                live=True,
                reference_hash=(
                    hashlib.sha256(order_id.encode()).hexdigest()[:10]
                    if order_id
                    else None
                ),
            )

        buy_price = (ask * (Decimal("1") + self.settings.price_slippage)).quantize(
            Decimal("0.001"), rounding=ROUND_UP
        )
        sell_price = (bid * (Decimal("1") - self.settings.price_slippage)).quantize(
            Decimal("0.001"), rounding=ROUND_DOWN
        )
        balances = self._account_balances()
        available_twd = self._available_balance(balances, "twd")
        available_usdt = self._available_balance(balances, "usdt")
        decision = choose_trade_side(
            available_twd=available_twd,
            available_usdt=available_usdt,
            target_usdt=target,
            buy_price_twd=buy_price,
            buy_buffer_rate=self.settings.bitopro_taker_fee_rate,
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
        price = buy_price if side == "buy" else sell_price

        timestamp = int(time.time() * 1000)
        body = {
            "action": side.upper(),
            "amount": str(target),
            "price": str(price),
            "timestamp": timestamp,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "clientId": client_id,
        }
        created = self.http.request_json(
            "POST",
            f"{self.base_url}/orders/{self.pair}",
            body=body,
            headers=self._post_headers(body),
        )
        order_id = str(created.get("orderId", ""))
        if not order_id:
            raise RuntimeError("BitoPro 未回傳訂單編號")

        detail: dict[str, Any] = {}
        for _ in range(6):
            time.sleep(2)
            detail = self.http.request_json(
                "GET",
                f"{self.base_url}/orders/{self.pair}/{order_id}",
                headers=self._read_headers(),
            )
            if Decimal(detail.get("remainingAmount", str(target))) <= 0:
                break

        executed = Decimal(detail.get("executedAmount", "0"))
        remaining = Decimal(detail.get("remainingAmount", str(target)))
        if remaining > 0:
            self.http.request_json(
                "DELETE",
                f"{self.base_url}/orders/{self.pair}/{order_id}",
                params={"isAllStrategyCanceled": "true"},
                headers=self._read_headers(),
            )

        avg_price = Decimal(detail.get("avgExecutionPrice", "0")) or ask
        if executed >= target:
            status = "filled"
            message = "訂單已全數成交；等待電子發票開立通知"
        elif executed > 0:
            status = "partial"
            message = "訂單僅部分成交，未成交餘額已送出取消"
        else:
            status = "failed"
            message = "訂單未成交，已送出取消"

        reference_hash = hashlib.sha256(order_id.encode()).hexdigest()[:10]
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
            reference_hash=reference_hash,
        )

    def public_status(self, today_status: str = "waiting") -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "accent": self.accent,
            "api_status": self.api_status,
            "minimum_usdt": str(self.minimum_usdt),
            "minimum_twd": None,
            "planned_usdt": str(self.planned_usdt),
            "convert_supported": False,
            "target_eligible": self.planned_usdt >= self.minimum_usdt,
            "invoice_rule": self.invoice_rule,
            "today_status": today_status,
            "note": "只做 USDT/TWD 現貨；TWD 足夠時買入，否則在 USDT 足夠時賣出。官方 API 未提供閃兌執行端點。",
        }
