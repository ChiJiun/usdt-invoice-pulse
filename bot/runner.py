from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.exchanges import BitoProAdapter, MaxAdapter
from bot.models import RunResult, decimal_text


TAIPEI = ZoneInfo("Asia/Taipei")
MAX_EVENTS = 180
INVOICE_LOOKUP_URLS = {
    "bitopro": "https://support.bitopro.com/hc/zh-tw/articles/360018704812",
    "max": "https://support.maicoin.com/zh-TW/support/solutions/articles/32000026066",
}
INVOICE_STATUS_MAP = {
    "pending_confirmation": "pending_confirmation",
    "confirmed": "confirmed",
    "not_found": "not_found",
    "manual_check": "manual_check",
}
INVOICE_SYNC_STATUSES = {"disabled", "success", "partial", "failed"}


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


def existing_live_record(
    state: dict[str, Any], dashboard: dict[str, Any], date_text: str, exchange: str
) -> dict[str, Any] | None:
    record = state.get("live_runs", {}).get(date_text, {}).get(exchange)
    if record and record.get("status") in {"filled", "partial"}:
        matching_event = next(
            (
                event
                for event in dashboard.get("events", [])
                if event.get("date") == date_text
                and event.get("exchange") == exchange
                and event.get("mode") == "live"
                and event.get("status") in {"filled", "partial"}
            ),
            None,
        )
        return {**(matching_event or {}), **record}
    for event in dashboard.get("events", []):
        if (
            event.get("date") == date_text
            and event.get("exchange") == exchange
            and event.get("mode") == "live"
            and event.get("status") in {"filled", "partial"}
        ):
            return event
    return None


def make_duplicate_result(adapter: Any, record: dict[str, Any]) -> RunResult:
    side = str(record.get("side", "none"))
    if side not in {"buy", "sell", "none"}:
        side = "none"
    execution_type = str(record.get("execution_type", "spot"))
    if execution_type not in {"spot", "convert", "none"}:
        execution_type = "spot"
    status = "partial" if record.get("status") == "partial" else "filled"
    filled = Decimal(str(record.get("filled_usdt", "0")))
    average = record.get("avg_price_twd")
    return adapter.base_result(
        status=status,
        side=side,
        execution_type=execution_type,
        requested_usdt=filled or adapter.planned_usdt,
        filled_usdt=filled,
        avg_price_twd=Decimal(str(average)) if average else None,
        invoice_status="pending_confirmation",
        message="repository 已保存今日正式成交，重複防護已沿用紀錄且未再次呼叫下單 API",
        live=True,
    )


def safe_public_url(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(str(value).strip())
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return str(value).strip()


def masked_identifier(value: Any) -> str | None:
    if not value:
        return None
    display = str(value).strip()
    raw = "".join(character for character in display if character.isalnum())
    if not raw:
        return None
    if "•" in display or "*" in display:
        return f"{raw[:2]}••••••{raw[-2:]}" if len(raw) >= 4 else "••••"
    if len(raw) <= 4:
        return "••••"
    return f"{raw[:2]}••••••{raw[-2:]}"


def valid_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        return None


def normalize_invoice_records(
    raw_records: list[Any], exchange_aliases: dict[str, str]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            continue
        exchange = exchange_aliases.get(str(raw.get("exchange", "")).lower())
        status = INVOICE_STATUS_MAP.get(str(raw.get("status", "")).lower())
        if not exchange or not status:
            continue
        record_id = str(raw.get("id") or f"invoice-{exchange}-{index}")[:80]
        if record_id in seen:
            continue
        seen.add(record_id)
        amount = raw.get("amount_twd")
        amount_text = None
        if amount is not None and amount != "":
            try:
                parsed_amount = Decimal(str(amount))
                amount_text = decimal_text(parsed_amount) if parsed_amount >= 0 else None
            except Exception:
                amount_text = None
        records.append(
            {
                "id": record_id,
                "exchange": exchange,
                "trade_date": valid_date(raw.get("trade_date")),
                "status": status,
                "checked_at": str(raw.get("checked_at") or "")[:40] or None,
                "issued_date": valid_date(raw.get("issued_date")),
                "amount_twd": amount_text,
                "masked_number": masked_identifier(raw.get("masked_number")),
                "detail_url": safe_public_url(raw.get("detail_url")),
                "note": str(raw.get("note") or "").replace("\n", " ")[:180] or None,
            }
        )
    return records


def normalize_invoice_sync_status(raw: Any) -> dict[str, Any]:
    fallback = {
        "source": "gmail",
        "status": "disabled",
        "checked_at": None,
        "messages_scanned": 0,
        "records_updated": 0,
        "unmatched_records": 0,
        "note": "尚未啟用 Gmail 唯讀發票核對",
    }
    if not isinstance(raw, dict):
        return fallback
    status = str(raw.get("status", "disabled"))
    if status not in INVOICE_SYNC_STATUSES:
        status = "failed"

    def count(name: str) -> int:
        try:
            return max(int(raw.get(name, 0)), 0)
        except (TypeError, ValueError):
            return 0

    return {
        "source": "gmail",
        "status": status,
        "checked_at": str(raw.get("checked_at") or "")[:40] or None,
        "messages_scanned": count("messages_scanned"),
        "records_updated": count("records_updated"),
        "unmatched_records": count("unmatched_records"),
        "note": str(raw.get("note") or fallback["note"]).replace("\n", " ")[:160],
    }


def preferred_event(
    events: list[dict[str, Any]], date_text: str, exchange: str
) -> dict[str, Any] | None:
    candidates = [
        event
        for event in events
        if event.get("date") == date_text and event.get("exchange") == exchange
    ]
    if not candidates:
        return None
    priority = {
        ("live", "filled"): 60,
        ("live", "partial"): 55,
        ("live", "failed"): 50,
        ("live", "skipped"): 45,
        ("dry_run", "simulated"): 20,
    }
    return max(
        candidates,
        key=lambda event: (
            priority.get((event.get("mode"), event.get("status")), 0),
            str(event.get("occurred_at", "")),
        ),
    )


def refreshed_exchange_status(adapter: Any, existing: dict[str, Any]) -> dict[str, object]:
    """Rebuild the public shape while retaining the last market-derived values."""
    status = adapter.public_status(str(existing.get("today_status", "waiting")))
    for field in (
        "minimum_usdt",
        "minimum_twd",
        "planned_usdt",
        "target_eligible",
        "today_status",
        "note",
    ):
        if field in existing:
            status[field] = existing[field]
    return status


def invoice_for_trade_date(
    records: list[dict[str, Any]], date_text: str, exchange: str
) -> dict[str, Any] | None:
    matches = [
        record
        for record in records
        if record.get("trade_date") == date_text and record.get("exchange") == exchange
    ]
    return max(matches, key=lambda record: str(record.get("checked_at") or ""), default=None)


def build_daily_status(
    adapters: list[Any],
    events: list[dict[str, Any]],
    invoice_records: list[dict[str, Any]],
    today: str,
) -> dict[str, Any]:
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    rows = []
    for adapter in adapters:
        today_event = preferred_event(events, today, adapter.id)
        yesterday_event = preferred_event(events, yesterday, adapter.id)
        invoice = invoice_for_trade_date(invoice_records, yesterday, adapter.id)
        if invoice:
            invoice_status = invoice["status"]
            invoice_note = invoice.get("note") or "已依人工／載具查詢紀錄更新"
        elif yesterday_event and yesterday_event.get("mode") == "live" and yesterday_event.get(
            "status"
        ) in {"filled", "partial"}:
            invoice_status = "pending_confirmation"
            invoice_note = "昨日有正式成交，但交易所發票 API 不可用，等待 Email／載具確認"
        else:
            invoice_status = "not_applicable"
            invoice_note = "昨日沒有保存到正式成交紀錄"
        rows.append(
            {
                "exchange": adapter.id,
                "exchange_name": adapter.name,
                "short_name": adapter.short_name,
                "accent": adapter.accent,
                "today_trade": {
                    "status": today_event.get("status") if today_event else "no_record",
                    "side": today_event.get("side", "none") if today_event else "none",
                    "execution_type": (
                        today_event.get("execution_type", "none")
                        if today_event
                        else "none"
                    ),
                    "filled_usdt": today_event.get("filled_usdt", "0") if today_event else "0",
                    "message": (
                        today_event.get("message")
                        if today_event
                        else "今日尚未執行或保存成交檢查"
                    ),
                    "source": (
                        "live_record"
                        if today_event and today_event.get("mode") == "live"
                        else "dry_run"
                        if today_event
                        else "none"
                    ),
                },
                "yesterday_invoice": {
                    "status": invoice_status,
                    "checked_at": invoice.get("checked_at") if invoice else None,
                    "issued_date": invoice.get("issued_date") if invoice else None,
                    "amount_twd": invoice.get("amount_twd") if invoice else None,
                    "masked_number": invoice.get("masked_number") if invoice else None,
                    "detail_url": invoice.get("detail_url") if invoice else None,
                    "lookup_url": INVOICE_LOOKUP_URLS[adapter.id],
                    "note": invoice_note,
                },
            }
        )
    return {"today_date": today, "yesterday_date": yesterday, "exchanges": rows}


def exception_result(adapter: Any, live: bool, exc: Exception) -> RunResult:
    safe = str(exc).replace(os.getcwd(), "<workspace>")[:220]
    return adapter.base_result(
        status="failed",
        message=f"執行失敗：{safe}",
        live=live,
    )


def run_all(
    settings: Settings, *, live: bool, refresh_only: bool = False
) -> dict[str, Any]:
    current = datetime.now(TAIPEI)
    today = current.date().isoformat()
    state = read_json(settings.state_path, {"version": 1, "live_runs": {}})
    existing_dashboard = read_json(settings.dashboard_path, {"events": []})
    raw_invoice_records = read_json(settings.invoice_records_path, [])
    invoice_sync = normalize_invoice_sync_status(
        read_json(settings.invoice_records_path.with_name("invoice-sync-status.json"), {})
    )
    if not isinstance(raw_invoice_records, list):
        raw_invoice_records = []

    adapters = []
    if settings.bitopro_enabled:
        adapters.append(BitoProAdapter(settings))
    if settings.max_enabled:
        adapters.append(MaxAdapter(settings))
    supported_exchange_ids = {adapter.id for adapter in adapters}
    exchange_aliases = {
        value.lower(): adapter.id
        for adapter in adapters
        for value in (adapter.id, adapter.name, adapter.short_name)
    }
    invoice_records = normalize_invoice_records(raw_invoice_records, exchange_aliases)
    confirmed_trade_keys = {
        (record["exchange"], record["trade_date"])
        for record in invoice_records
        if record.get("status") == "confirmed" and record.get("trade_date")
    }

    results: list[RunResult] = []
    if not refresh_only:
        for adapter in adapters:
            known_record = existing_live_record(
                state, existing_dashboard, today, adapter.id
            )
            if live and known_record:
                result = make_duplicate_result(adapter, known_record)
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
                    "avg_price_twd": decimal_text(result.avg_price_twd),
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
    existing_exchange_statuses = {
        exchange.get("id"): exchange
        for exchange in existing_dashboard.get("exchanges", [])
        if exchange.get("id") in supported_exchange_ids
    }
    exchange_statuses = [
        refreshed_exchange_status(adapter, existing_exchange_statuses[adapter.id])
        if refresh_only and adapter.id in existing_exchange_statuses
        else adapter.public_status(today_status.get(adapter.id, "waiting"))
        for adapter in adapters
    ]
    daily_status = build_daily_status(adapters, events, invoice_records, today)

    filled_events = [
        event
        for event in events
        if event.get("status") in {"filled", "partial", "simulated"}
    ]
    total_filled = sum(
        (Decimal(str(event.get("filled_usdt", "0"))) for event in filled_events),
        Decimal("0"),
    )
    dashboard = {
        "generated_at": current.isoformat(timespec="seconds"),
        "local_date": today,
        "timezone": "Asia/Taipei",
        "mode": (
            existing_dashboard.get("mode", "dry_run")
            if refresh_only
            else "live"
            if live
            else "dry_run"
        ),
        "target_usdt": decimal_text(settings.target_usdt),
        "summary": {
            "exchanges_total": len(exchange_statuses),
            "target_eligible": sum(
                1 for exchange in exchange_statuses if exchange["target_eligible"]
            ),
            "pending_invoice_checks": sum(
                1
                for event in events
                if event.get("invoice_status") == "pending_confirmation"
                and (event.get("exchange"), event.get("date"))
                not in confirmed_trade_keys
            ),
            "total_filled_usdt": decimal_text(total_filled),
            "today_trades": sum(
                1
                for row in daily_status["exchanges"]
                if row["today_trade"]["status"] in {"filled", "partial"}
            ),
            "yesterday_invoices_issued": sum(
                1
                for row in daily_status["exchanges"]
                if row["yesterday_invoice"]["status"] == "confirmed"
            ),
        },
        "exchanges": exchange_statuses,
        "events": events,
        "daily_status": daily_status,
        "invoice_records": invoice_records,
        "invoice_sync": invoice_sync,
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
    mode.add_argument(
        "--refresh", action="store_true", help="只重建公開資料，不呼叫交易所 API"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = Settings.from_env()
        if args.live:
            settings.assert_live_authorized()
        dashboard = run_all(
            settings, live=args.live, refresh_only=args.refresh
        )
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
