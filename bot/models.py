from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from bot.trading import TradeSide


RunStatus = Literal["simulated", "filled", "partial", "skipped", "failed"]
InvoiceStatus = Literal[
    "estimated_zero",
    "estimated_eligible",
    "confirmed",
    "not_applicable",
    "manual_check",
]


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    return format(normalized, "f")


def estimated_invoice_status(fee_twd: Decimal | None) -> InvoiceStatus:
    """Estimate whether the daily rounded fee can produce a non-zero invoice."""
    if fee_twd is None:
        return "manual_check"
    rounded = fee_twd.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return "estimated_eligible" if rounded >= 1 else "estimated_zero"


@dataclass(slots=True)
class RunResult:
    exchange: str
    exchange_name: str
    status: RunStatus
    side: TradeSide
    requested_usdt: Decimal
    filled_usdt: Decimal
    avg_price_twd: Decimal | None
    fee_twd: Decimal | None
    invoice_status: InvoiceStatus
    message: str
    mode: Literal["dry_run", "live"]
    occurred_at: str
    local_date: str
    reference_hash: str | None = None

    def to_public_dict(self, event_id: str) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("reference_hash", None)
        payload["id"] = event_id
        payload["date"] = payload.pop("local_date")
        for field in ("requested_usdt", "filled_usdt", "avg_price_twd", "fee_twd"):
            payload[field] = decimal_text(payload[field])  # type: ignore[arg-type]
        return payload
