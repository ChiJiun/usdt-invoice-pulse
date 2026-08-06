from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal

from bot.trading import TradeSide


RunStatus = Literal["simulated", "filled", "partial", "skipped", "failed"]
InvoiceStatus = Literal[
    "pending_confirmation",
    "confirmed",
    "not_applicable",
    "manual_check",
]
ExecutionType = Literal["spot", "convert", "none"]


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    return format(normalized, "f")


@dataclass(slots=True)
class RunResult:
    exchange: str
    exchange_name: str
    status: RunStatus
    side: TradeSide
    execution_type: ExecutionType
    requested_usdt: Decimal
    filled_usdt: Decimal
    avg_price_twd: Decimal | None
    invoice_status: InvoiceStatus
    message: str
    mode: Literal["dry_run", "live"]
    occurred_at: str
    local_date: str

    def to_public_dict(self, event_id: str) -> dict[str, object]:
        payload = asdict(self)
        payload["id"] = event_id
        payload["date"] = payload.pop("local_date")
        for field in ("requested_usdt", "filled_usdt", "avg_price_twd"):
            payload[field] = decimal_text(payload[field])  # type: ignore[arg-type]
        return payload
