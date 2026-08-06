from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Literal


TradeSide = Literal["buy", "sell", "none"]


def quantity_step(precision: int) -> Decimal:
    """Return the smallest valid quantity increment for an exchange precision."""
    return Decimal("1").scaleb(-precision)


def round_quantity_up(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("數量級距必須大於 0")
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def effective_target(
    configured_floor: Decimal,
    minimum_base: Decimal,
    minimum_quote: Decimal,
    reference_price: Decimal,
    step: Decimal,
) -> Decimal:
    """Raise the configured floor until every official minimum is satisfied."""
    if configured_floor <= 0 or minimum_base <= 0 or reference_price <= 0:
        raise ValueError("交易數量與價格必須大於 0")
    quote_floor = minimum_quote / reference_price if minimum_quote > 0 else Decimal("0")
    return round_quantity_up(max(configured_floor, minimum_base, quote_floor), step)


@dataclass(frozen=True, slots=True)
class TradeDecision:
    side: TradeSide
    required_twd: Decimal
    sellable_usdt: Decimal


def choose_trade_side(
    *,
    available_twd: Decimal,
    available_usdt: Decimal,
    target_usdt: Decimal,
    buy_price_twd: Decimal,
    buy_buffer_rate: Decimal,
    usdt_reserve: Decimal,
) -> TradeDecision:
    """Prefer BUY when TWD is sufficient, otherwise SELL when USDT is sufficient."""
    required_twd = target_usdt * buy_price_twd * (
        Decimal("1") + buy_buffer_rate
    )
    sellable_usdt = max(available_usdt - usdt_reserve, Decimal("0"))
    if available_twd >= required_twd:
        side: TradeSide = "buy"
    elif sellable_usdt >= target_usdt:
        side = "sell"
    else:
        side = "none"
    return TradeDecision(
        side=side,
        required_twd=required_twd,
        sellable_usdt=sellable_usdt,
    )
