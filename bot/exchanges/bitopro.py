from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import zlib
from decimal import Decimal, ROUND_UP
from typing import Any

from bot.models import estimated_invoice_status

from .base import ExchangeAdapter


class BitoProAdapter(ExchangeAdapter):
    id = "bitopro"
    name = "BitoPro"
    short_name = "BP"
    accent = "#2f6bff"
    api_status = "available"
    minimum_usdt = Decimal("1")
    invoice_rule = "依每日成交手續費彙總；未滿一元可能為零元發票"
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

    def _market_snapshot(self) -> tuple[Decimal, Decimal]:
        order_book = self.http.request_json(
            "GET", f"{self.base_url}/order-book/{self.pair}", params={"limit": 1}
        )
        ask = Decimal(order_book["asks"][0]["price"])
        limits = self.http.request_json(
            "GET", f"{self.base_url}/provisioning/limitations-and-fees"
        )
        pair_limit = next(
            row
            for row in limits["orderFeesAndLimitations"]
            if row["pair"].upper() == "USDT/TWD"
        )
        minimum = Decimal(pair_limit["minimumOrderAmount"])
        return ask, minimum

    def _estimate(self, price: Decimal, amount: Decimal) -> tuple[Decimal, str]:
        fee = price * amount * self.settings.bitopro_taker_fee_rate
        return fee, estimated_invoice_status(fee)

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

    def run(self, *, live: bool):
        ask, minimum = self._market_snapshot()
        self.minimum_usdt = minimum
        target = self.settings.target_usdt
        if target < minimum:
            return self.base_result(
                status="skipped",
                message=f"官方最低下單量為 {minimum} USDT",
                live=live,
            )

        fee, invoice_status = self._estimate(ask, target)
        if not live:
            return self.base_result(
                status="simulated",
                filled_usdt=target,
                avg_price_twd=ask,
                fee_twd=fee,
                invoice_status=invoice_status,
                message="符合最低門檻；模擬限價吃單，未送出真實訂單",
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
            raw_fee = Decimal(detail.get("fee", "0"))
            fee_symbol = str(detail.get("feeSymbol", "")).lower()
            actual_fee_twd = raw_fee if fee_symbol == "twd" else raw_fee * avg_price
            if actual_fee_twd == 0 and executed > 0:
                actual_fee_twd, _ = self._estimate(avg_price, executed)
            status = "filled" if executed >= target else "partial" if executed > 0 else "failed"
            return self.base_result(
                status=status,
                filled_usdt=executed,
                avg_price_twd=avg_price if executed else None,
                fee_twd=actual_fee_twd if executed else None,
                invoice_status=(
                    estimated_invoice_status(actual_fee_twd)
                    if executed
                    else "not_applicable"
                ),
                message="偵測到今日既有自動訂單，已沿用結果並阻止重複購買",
                live=True,
                reference_hash=(
                    hashlib.sha256(order_id.encode()).hexdigest()[:10]
                    if order_id
                    else None
                ),
            )

        price = (ask * (Decimal("1") + self.settings.price_slippage)).quantize(
            Decimal("0.001"), rounding=ROUND_UP
        )
        timestamp = int(time.time() * 1000)
        body = {
            "action": "BUY",
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
        raw_fee = Decimal(detail.get("fee", "0"))
        fee_symbol = str(detail.get("feeSymbol", "")).lower()
        actual_fee_twd = raw_fee if fee_symbol == "twd" else raw_fee * avg_price
        if actual_fee_twd == 0 and executed > 0:
            actual_fee_twd, _ = self._estimate(avg_price, executed)

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
            filled_usdt=executed,
            avg_price_twd=avg_price if executed else None,
            fee_twd=actual_fee_twd if executed else None,
            invoice_status=(
                estimated_invoice_status(actual_fee_twd)
                if executed
                else "not_applicable"
            ),
            message=message,
            live=True,
            reference_hash=reference_hash,
        )

    def public_status(self, today_status: str = "waiting") -> dict[str, object]:
        eligible = self.settings.target_usdt >= self.minimum_usdt
        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "accent": self.accent,
            "api_status": self.api_status,
            "minimum_usdt": str(self.minimum_usdt),
            "target_eligible": eligible,
            "invoice_rule": self.invoice_rule,
            "today_status": today_status,
            "note": "唯一可用 1 USDT 限價單執行的交易所；手續費仍可能不足一元。",
        }
