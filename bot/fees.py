from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from bot.models import decimal_text


def fee_amount(value: Any) -> Decimal | None:
    """Missing/malformed fees are unknown, never an inferred zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() else None
    except InvalidOperation:
        return None


def fee_fields(
    rows: list[dict[str, Any]], *, currency_key: str, source: str,
    complete: bool = True,
) -> dict[str, Any]:
    totals: dict[str, Decimal] = {}
    complete = complete and bool(rows)
    for row in rows:
        amount = fee_amount(row.get("fee"))
        currency = str(row.get(currency_key) or "").strip().lower()
        if amount is None or not currency:
            complete = False
            continue
        totals[currency] = totals.get(currency, Decimal("0")) + amount
    fees = [{"amount": decimal_text(amount), "currency": currency}
            for currency, amount in sorted(totals.items())]
    # Retain the legacy scalar only when it represents a complete single currency.
    currency = next(iter(totals)) if complete and len(totals) == 1 else None
    return {
        "actual_fee": totals[currency] if currency else None,
        "fee_currency": currency,
        "actual_fees": fees,
        "fee_complete": complete,
        "fee_source": source,
    }
