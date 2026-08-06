from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{name} 必須是數字") from exc
    if value <= 0:
        raise ValueError(f"{name} 必須大於 0")
    return value


def env_nonnegative_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{name} 必須是數字") from exc
    if value < 0:
        raise ValueError(f"{name} 不可小於 0")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    target_usdt: Decimal
    usdt_reserve: Decimal
    max_convert_enabled: bool
    max_convert_twd_amount: Decimal
    max_convert_usdt_amount: Decimal
    live_trading: bool
    live_confirmation: str
    bitopro_enabled: bool
    max_enabled: bool
    bitopro_email: str
    bitopro_api_key: str
    bitopro_api_secret: str
    max_api_key: str
    max_api_secret: str
    bitopro_taker_fee_rate: Decimal
    max_taker_fee_rate: Decimal
    price_slippage: Decimal
    dashboard_path: Path
    state_path: Path
    confirmed_invoices_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            target_usdt=env_decimal("ORDER_USDT", "1"),
            usdt_reserve=env_nonnegative_decimal("USDT_RESERVE", "0"),
            max_convert_enabled=env_bool("MAX_CONVERT_ENABLED", True),
            max_convert_twd_amount=env_decimal("MAX_CONVERT_TWD_AMOUNT", "10"),
            max_convert_usdt_amount=env_decimal("MAX_CONVERT_USDT_AMOUNT", "1"),
            live_trading=env_bool("LIVE_TRADING", False),
            live_confirmation=os.getenv("CONFIRM_LIVE_TRADING", ""),
            bitopro_enabled=env_bool("BITOPRO_ENABLED", True),
            max_enabled=env_bool("MAX_ENABLED", True),
            bitopro_email=os.getenv("BITOPRO_EMAIL", ""),
            bitopro_api_key=os.getenv("BITOPRO_API_KEY", ""),
            bitopro_api_secret=os.getenv("BITOPRO_API_SECRET", ""),
            max_api_key=os.getenv("MAX_API_KEY", ""),
            max_api_secret=os.getenv("MAX_API_SECRET", ""),
            bitopro_taker_fee_rate=env_decimal("BITOPRO_TAKER_FEE_RATE", "0.002"),
            max_taker_fee_rate=env_decimal("MAX_TAKER_FEE_RATE", "0.0016"),
            price_slippage=env_decimal("ORDER_PRICE_SLIPPAGE", "0.005"),
            dashboard_path=Path(os.getenv("DASHBOARD_PATH", "public/data/dashboard.json")),
            state_path=Path(os.getenv("STATE_PATH", "data/state.json")),
            confirmed_invoices_path=Path(
                os.getenv("CONFIRMED_INVOICES_PATH", "data/confirmed-invoices.json")
            ),
        )

    def assert_live_authorized(self) -> None:
        if not self.live_trading:
            raise ValueError("LIVE_TRADING 尚未開啟")
        if self.live_confirmation != "I_UNDERSTAND_THIS_PLACES_REAL_ORDERS":
            raise ValueError("缺少正式下單確認鎖")
