from __future__ import annotations

from decimal import Decimal

from .base import ExchangeAdapter


class HoyaBitAdapter(ExchangeAdapter):
    id = "hoyabit"
    name = "HOYA BIT"
    short_name = "HY"
    accent = "#ef693c"
    api_status = "unavailable"
    minimum_usdt = Decimal("10")
    invoice_rule = "交易成功後依實收費用開立；實際狀態需由平台或載具確認"

    def run(self, *, live: bool):
        if self.settings.target_usdt < self.minimum_usdt:
            reason = "官方最低下單量為 10 USDT／等值新台幣 300 元"
        else:
            reason = "未提供官方公開交易 API，不使用帳密模擬登入"
        return self.base_result(status="skipped", message=reason, live=live)

    def public_status(self, today_status: str = "waiting") -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "accent": self.accent,
            "api_status": self.api_status,
            "minimum_usdt": str(self.minimum_usdt),
            "target_eligible": False,
            "invoice_rule": self.invoice_rule,
            "today_status": today_status,
            "note": "最低 10 USDT，且目前沒有官方公開交易 API；為保護帳號而停用自動化。",
        }

