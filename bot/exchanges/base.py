from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.http import HttpClient
from bot.models import RunResult


TAIPEI = ZoneInfo("Asia/Taipei")


class ExchangeAdapter(ABC):
    id: str
    name: str
    short_name: str
    accent: str
    api_status: str
    minimum_usdt: Decimal
    invoice_rule: str

    def __init__(self, settings: Settings, http: HttpClient | None = None) -> None:
        self.settings = settings
        self.http = http or HttpClient()
        self.planned_usdt = max(settings.target_usdt, self.minimum_usdt)

    def now(self) -> datetime:
        return datetime.now(TAIPEI)

    def base_result(
        self,
        *,
        status: str,
        side: str = "none",
        requested_usdt: Decimal | None = None,
        filled_usdt: Decimal = Decimal("0"),
        avg_price_twd: Decimal | None = None,
        fee_twd: Decimal | None = None,
        invoice_status: str = "not_applicable",
        message: str,
        live: bool,
        reference_hash: str | None = None,
    ) -> RunResult:
        current = self.now()
        return RunResult(
            exchange=self.id,
            exchange_name=self.name,
            status=status,  # type: ignore[arg-type]
            side=side,  # type: ignore[arg-type]
            requested_usdt=requested_usdt or self.settings.target_usdt,
            filled_usdt=filled_usdt,
            avg_price_twd=avg_price_twd,
            fee_twd=fee_twd,
            invoice_status=invoice_status,  # type: ignore[arg-type]
            message=message,
            mode="live" if live else "dry_run",
            occurred_at=current.isoformat(timespec="seconds"),
            local_date=current.date().isoformat(),
            reference_hash=reference_hash,
        )

    @abstractmethod
    def run(self, *, live: bool) -> RunResult:
        raise NotImplementedError

    @abstractmethod
    def public_status(self, today_status: str = "waiting") -> dict[str, object]:
        raise NotImplementedError
