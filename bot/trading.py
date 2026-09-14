from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Literal


TradeSide = Literal["buy", "sell", "none"]


def quantity_step(precision: int) -> Decimal:
    """Return the smallest valid quantity increment for an exchange precision."""
    if precision < 0:
        raise ValueError("下單精度不可為負數")
    return Decimal("1").scaleb(-precision)


def round_quantity_up(value: Decimal, step: Decimal) -> Decimal:
    if not step.is_finite() or step <= 0:
        raise ValueError("數量級距必須大於 0")
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def fee_quote_target(fee_twd: Decimal, fee_rate: Decimal) -> Decimal:
    """Estimate turnover, never claim that an invoice will actually be issued."""
    if not fee_twd.is_finite() or fee_twd <= 0:
        raise ValueError("目標手續費必須大於 0")
    if not fee_rate.is_finite() or not Decimal("0") < fee_rate < Decimal("1"):
        raise ValueError("有效手續費率必須介於 0 與 1")
    return fee_twd / fee_rate


def fee_target_quantity(
    *,
    fee_twd: Decimal,
    fee_rate: Decimal,
    minimum_base: Decimal,
    minimum_quote: Decimal,
    price_floor_twd: Decimal,
    step: Decimal,
) -> Decimal:
    """Size up to the fee target and official minima at a conservative price."""
    for value in (minimum_base, price_floor_twd):
        if not value.is_finite() or value <= 0:
            raise ValueError("交易數量與參考價格必須大於 0")
    if not minimum_quote.is_finite() or minimum_quote < 0:
        raise ValueError("最低成交金額不可為負數")
    quote_target = max(fee_quote_target(fee_twd, fee_rate), minimum_quote)
    return round_quantity_up(max(minimum_base, quote_target / price_floor_twd), step)


def choose_trade_side(
    *,
    available_twd: Decimal,
    available_usdt: Decimal,
    target_usdt: Decimal,
    buy_price_twd: Decimal,
    buy_buffer_rate: Decimal,
    allow_sell: bool = True,
) -> TradeSide:
    """BUY first; sell only when permitted and the whole lot is funded."""
    required_twd = target_usdt * buy_price_twd * (
        Decimal("1") + buy_buffer_rate
    )
    if available_twd >= required_twd:
        return "buy"
    if allow_sell and available_usdt >= target_usdt:
        return "sell"
    return "none"
