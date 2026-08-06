from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.exchanges import BitoProAdapter, MaxAdapter
from bot.models import RunResult, decimal_text


TAIPEI = ZoneInfo("Asia/Taipei")
MAX_EVENTS = 180


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def event_id(result: RunResult) -> str:
    raw = f"{result.local_date}:{result.exchange}:{result.mode}:{result.occurred_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def already_executed(state: dict[str, Any], date: str, exchange: str) -> bool:
    record = state.get("live_runs", {}).get(date, {}).get(exchange)
    return bool(record and record.get("status") in {"filled", "partial"})


def make_duplicate_result(adapter: Any, live: bool) -> RunResult:
    return adapter.base_result(
        status="skipped",
        requested_usdt=adapter.planned_usdt,
        message="今日已有正式成交紀錄，重複防護已略過本次執行",
        live=live,
    )


def exception_result(adapter: Any, live: bool, exc: Exception) -> RunResult:
    safe = str(exc).replace(os.getcwd(), "<workspace>")[:220]
    return adapter.base_result(
        status="failed",
        message=f"執行失敗：{safe}",
        live=live,
    )


def run_all(settings: Settings, *, live: bool) -> dict[str, Any]:
    current = datetime.now(TAIPEI)
    today = current.date().isoformat()
    state = read_json(settings.state_path, {"version": 1, "live_runs": {}})
    existing_dashboard = read_json(settings.dashboard_path, {"events": []})
    confirmed = read_json(settings.confirmed_invoices_path, [])

    adapters = []
    if settings.bitopro_enabled:
        adapters.append(BitoProAdapter(settings))
    if settings.max_enabled:
        adapters.append(MaxAdapter(settings))
    supported_exchange_ids = {adapter.id for adapter in adapters}
    supported_invoice_names = {
        value.lower()
        for adapter in adapters
        for value in (adapter.id, adapter.name, adapter.short_name)
    }
    confirmed = [
        invoice
        for invoice in confirmed
        if str(invoice.get("exchange", "")).lower() in supported_invoice_names
    ]

    results: list[RunResult] = []
    for adapter in adapters:
        if live and already_executed(state, today, adapter.id):
            result = make_duplicate_result(adapter, live)
        else:
            try:
                result = adapter.run(live=live)
            except Exception as exc:  # keep one exchange failure from hiding other results
                result = exception_result(adapter, live, exc)
        results.append(result)

        if live and result.status in {"filled", "partial"}:
            state.setdefault("live_runs", {}).setdefault(today, {})[adapter.id] = {
                "status": result.status,
                "side": result.side,
                "execution_type": result.execution_type,
                "filled_usdt": decimal_text(result.filled_usdt),
                "reference_hash": result.reference_hash,
            }

    new_events = [result.to_public_dict(event_id(result)) for result in results]
    old_events = [
        event
        for event in existing_dashboard.get("events", [])
        if event.get("exchange") in supported_exchange_ids
    ]
    old_keys = {event["id"] for event in new_events}
    new_scopes = {
        (event.get("date"), event.get("exchange"), event.get("mode"))
        for event in new_events
    }
    events = new_events + [
        event
        for event in old_events
        if event.get("id") not in old_keys
        and (event.get("date"), event.get("exchange"), event.get("mode"))
        not in new_scopes
    ]
    events = events[:MAX_EVENTS]

    today_status = {result.exchange: result.status for result in results}
    exchange_statuses = [
        adapter.public_status(today_status.get(adapter.id, "waiting")) for adapter in adapters
    ]

    filled_events = [
        event
        for event in events
        if event.get("status") in {"filled", "partial", "simulated"}
    ]
    total_filled = sum(
        (Decimal(str(event.get("filled_usdt", "0"))) for event in filled_events),
        Decimal("0"),
    )
    total_notional = sum(
        (
            Decimal(str(event.get("filled_usdt", "0")))
            * Decimal(str(event.get("avg_price_twd") or "0"))
            for event in filled_events
        ),
        Decimal("0"),
    )

    dashboard = {
        "generated_at": current.isoformat(timespec="seconds"),
        "local_date": today,
        "timezone": "Asia/Taipei",
        "mode": "live" if live else "dry_run",
        "target_usdt": decimal_text(settings.target_usdt),
        "summary": {
            "exchanges_total": len(exchange_statuses),
            "target_eligible": sum(
                1 for exchange in exchange_statuses if exchange["target_eligible"]
            ),
            "filled_runs": sum(
                1 for event in events if event.get("status") in {"filled", "partial"}
            ),
            "skipped_runs": sum(
                1 for event in events if event.get("status") == "skipped"
            ),
            "confirmed_invoices": len(confirmed),
            "pending_invoice_checks": sum(
                1
                for event in events
                if event.get("invoice_status") == "pending_confirmation"
            ),
            "total_filled_usdt": decimal_text(total_filled),
            "total_notional_twd": decimal_text(total_notional),
        },
        "exchanges": exchange_statuses,
        "events": events,
        "confirmed_invoices": confirmed,
    }
    write_json(settings.dashboard_path, dashboard)
    if live:
        write_json(settings.state_path, state)
    return dashboard


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每日 USDT 下單與 dashboard 更新")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只模擬，不送單")
    mode.add_argument("--live", action="store_true", help="允許正式下單")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = Settings.from_env()
        if args.live:
            settings.assert_live_authorized()
        dashboard = run_all(settings, live=args.live)
    except ValueError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        return 2

    summary = dashboard["summary"]
    print(
        f"完成：模式={dashboard['mode']} 可執行={summary['target_eligible']}/"
        f"{summary['exchanges_total']} 今日={dashboard['local_date']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
