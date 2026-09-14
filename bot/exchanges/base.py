from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.http import HttpClient
from bot.models import RunResult
from bot.trading import fee_quote_target


TAIPEI = ZoneInfo("Asia/Taipei")


class ExchangeAdapter(ABC):
    id: str
    name: str
    short_name: str
    accent: str
    minimum_usdt: Decimal
    fee_target_setting: str
    fee_rate_setting: str

    def __init__(self, settings: Settings, http: HttpClient | None = None) -> None:
        self.settings = settings
        self.http = http or HttpClient()
        self.planned_usdt: Decimal | None = None
        self.fee_target_twd: Decimal = getattr(settings, self.fee_target_setting)
        self.fee_rate: Decimal = getattr(settings, self.fee_rate_setting)
        self.turnover_target_twd = fee_quote_target(self.fee_target_twd, self.fee_rate)

    def now(self) -> datetime:
        return datetime.now(TAIPEI)

    def base_result(
        self,
        *,
        status: str,
        side: str = "none",
        execution_type: str = "none",
        requested_usdt: Decimal | None = None,
        filled_usdt: Decimal = Decimal("0"),
        avg_price_twd: Decimal | None = None,
        invoice_status: str = "not_applicable",
        estimated_fee_twd: Decimal | None = None,
        actual_fee: Decimal | None = None,
        fee_currency: str | None = None,
        actual_fees: list[dict[str, str]] | None = None,
        fee_complete: bool = False,
        fee_source: str | None = None,
        actual_fee_twd: Decimal | None = None,
        message: str,
        live: bool,
    ) -> RunResult:
        current = self.now()
        return RunResult(
            exchange=self.id,
            exchange_name=self.name,
            status=status,  # type: ignore[arg-type]
            side=side,  # type: ignore[arg-type]
            execution_type=execution_type,  # type: ignore[arg-type]
            requested_usdt=(requested_usdt if requested_usdt is not None
                            else self.planned_usdt or self.minimum_usdt),
            filled_usdt=filled_usdt,
            avg_price_twd=avg_price_twd,
            invoice_status=invoice_status,  # type: ignore[arg-type]
            message=message,
            mode="live" if live else "dry_run",
            occurred_at=current.isoformat(timespec="seconds"),
            local_date=current.date().isoformat(),
            fee_target_twd=self.fee_target_twd,
            estimated_fee_twd=estimated_fee_twd,
            actual_fee=actual_fee,
            fee_currency=fee_currency.lower() if fee_currency else None,
            actual_fees=actual_fees or [],
            fee_complete=fee_complete,
            fee_source=fee_source,
            actual_fee_twd=actual_fee_twd,
        )

    @abstractmethod
    def run(self, *, live: bool) -> RunResult:
        raise NotImplementedError

    @abstractmethod
    def public_status(self, today_status: str = "waiting") -> dict[str, object]:
        raise NotImplementedError
