from __future__ import annotations

import base64
import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.header import decode_header, make_header
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_QUERIES = {
    "bitopro": 'newer_than:14d "幣託科技" {發票 電子發票}',
    "max": 'newer_than:14d "電子發票開立通知" {MAX MaiCoin "現代財富科技"}',
}
EXCHANGE_MARKERS = {
    "bitopro": ("bitopro", "幣託科技", "幣託"),
    "max": ("max", "maicoin", "現代財富科技"),
}
INVOICE_NUMBER = re.compile(r"(?<![A-Z0-9])([A-Z]{2})[-\s]?(\d{8})(?!\d)", re.I)
DATE_VALUE = r"(?P<year>\d{2,4})\s*[年./-]\s*(?P<month>\d{1,2})\s*[月./-]\s*(?P<day>\d{1,2})\s*日?"


class GmailSyncError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GmailConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    queries: dict[str, str]

    @classmethod
    def from_env(cls) -> "GmailConfig":
        values = {
            "GMAIL_CLIENT_ID": os.getenv("GMAIL_CLIENT_ID", "").strip(),
            "GMAIL_CLIENT_SECRET": os.getenv("GMAIL_CLIENT_SECRET", "").strip(),
            "GMAIL_REFRESH_TOKEN": os.getenv("GMAIL_REFRESH_TOKEN", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise GmailSyncError(f"缺少 Gmail Secrets：{', '.join(missing)}")
        return cls(
            client_id=values["GMAIL_CLIENT_ID"],
            client_secret=values["GMAIL_CLIENT_SECRET"],
            refresh_token=values["GMAIL_REFRESH_TOKEN"],
            queries={
                "bitopro": (
                    os.getenv("GMAIL_BITOPRO_QUERY") or DEFAULT_QUERIES["bitopro"]
                ).strip(),
                "max": (
                    os.getenv("GMAIL_MAX_QUERY") or DEFAULT_QUERIES["max"]
                ).strip(),
            },
        )


class GmailClient:
    def __init__(self, config: GmailConfig, timeout: int = 20) -> None:
        self.config = config
        self.timeout = timeout
        self._access_token: str | None = None

    def _token(self) -> str:
        if self._access_token:
            return self._access_token
        body = urlencode(
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": self.config.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = Request(
            TOKEN_URL,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        payload = self._open_json(request, "Gmail OAuth 授權失敗")
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise GmailSyncError("Gmail OAuth 未回傳 access token")
        self._access_token = str(token)
        return self._access_token

    def _open_json(self, request: Request, context: str) -> Any:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raise GmailSyncError(f"{context}（HTTP {exc.code}）") from exc
        except (URLError, TimeoutError) as exc:
            raise GmailSyncError(f"{context}（連線失敗）") from exc
        except json.JSONDecodeError as exc:
            raise GmailSyncError(f"{context}（回應格式錯誤）") from exc

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{GMAIL_API}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token()}",
            },
            method="GET",
        )
        return self._open_json(request, "Gmail API 讀取失敗")

    def list_message_ids(self, query: str, maximum: int = 50) -> list[str]:
        payload = self._get("/messages", {"q": query, "maxResults": maximum})
        messages = payload.get("messages", []) if isinstance(payload, dict) else []
        return [str(item["id"]) for item in messages if isinstance(item, dict) and item.get("id")]

    def get_message(self, message_id: str) -> dict[str, Any]:
        payload = self._get(f"/messages/{quote(message_id)}", {"format": "full"})
        if not isinstance(payload, dict):
            raise GmailSyncError("Gmail 郵件回應格式錯誤")
        return payload

    def get_attachment(self, message_id: str, attachment_id: str) -> str:
        payload = self._get(
            f"/messages/{quote(message_id)}/attachments/{quote(attachment_id)}"
        )
        return str(payload.get("data", "")) if isinstance(payload, dict) else ""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


def _decode_base64url(value: str) -> str:
    if not value:
        return ""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding).decode("utf-8", errors="replace")
    except (ValueError, UnicodeError):
        return ""


def _decoded_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value


def message_text(message: dict[str, Any], client: GmailClient | None = None) -> str:
    message_id = str(message.get("id", ""))
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        return ""
    headers = payload.get("headers", [])
    header_text = " ".join(
        _decoded_header(str(header.get("value", "")))
        for header in headers
        if isinstance(header, dict)
        and str(header.get("name", "")).lower() in {"subject", "from"}
    )
    bodies: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        mime_type = str(part.get("mimeType", "")).lower()
        body = part.get("body", {})
        encoded = str(body.get("data", "")) if isinstance(body, dict) else ""
        attachment_id = str(body.get("attachmentId", "")) if isinstance(body, dict) else ""
        if not encoded and attachment_id and client and mime_type in {"text/plain", "text/html"}:
            encoded = client.get_attachment(message_id, attachment_id)
        if encoded and mime_type in {"text/plain", "text/html", ""}:
            decoded = _decode_base64url(encoded)
            if mime_type == "text/html":
                parser = _TextExtractor()
                parser.feed(decoded)
                decoded = parser.text()
            bodies.append(decoded)
        for child in part.get("parts", []):
            if isinstance(child, dict):
                visit(child)

    visit(payload)
    return re.sub(r"\s+", " ", " ".join([header_text, *bodies])).strip()


def _labeled_date(text: str, labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(rf"(?:{label_pattern})\s*[:：]?\s*{DATE_VALUE}", re.I)
    match = pattern.search(text)
    if not match:
        return None
    year = int(match.group("year"))
    if year < 1911:
        year += 1911
    try:
        return date(year, int(match.group("month")), int(match.group("day"))).isoformat()
    except ValueError:
        return None


def parse_invoice_number(text: str) -> str | None:
    match = INVOICE_NUMBER.search(text.upper())
    return f"{match.group(1).upper()}{match.group(2)}" if match else None


def parse_amount(text: str) -> str | None:
    pattern = re.compile(
        r"(?:發票金額|消費金額|銷售額|應付金額|合計|總計)\s*[:：]?\s*"
        r"(?:NT\$|TWD|新台幣|\$)?\s*([\d,]+(?:\.\d+)?)",
        re.I,
    )
    match = pattern.search(text)
    if not match:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return format(amount.normalize(), "f")


def mask_invoice_number(value: str) -> str:
    return f"{value[:2]}••••••{value[-2:]}"


def received_date(message: dict[str, Any]) -> str:
    try:
        timestamp = int(str(message.get("internalDate", "0"))) / 1000
        return datetime.fromtimestamp(timestamp, TAIPEI).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return datetime.now(TAIPEI).date().isoformat()


def is_expected_invoice(exchange: str, text: str, invoice_number: str | None) -> bool:
    lowered = text.lower()
    has_marker = any(marker.lower() in lowered for marker in EXCHANGE_MARKERS[exchange])
    return has_marker and ("發票" in text or invoice_number is not None)


def live_trade_dates(dashboard: dict[str, Any], exchange: str) -> list[str]:
    dates = {
        str(event.get("date"))
        for event in dashboard.get("events", [])
        if isinstance(event, dict)
        and event.get("exchange") == exchange
        and event.get("mode") == "live"
        and event.get("status") in {"filled", "partial"}
        and event.get("date")
    }
    return sorted(dates)


def choose_trade_date(
    *,
    explicit_date: str | None,
    received: str,
    available_dates: list[str],
    already_matched: set[str],
) -> str | None:
    if explicit_date and explicit_date not in already_matched:
        return explicit_date
    received_day = date.fromisoformat(received)
    earliest = received_day - timedelta(days=7)
    eligible = [
        value
        for value in available_dates
        if value not in already_matched
        and earliest <= date.fromisoformat(value) <= received_day
    ]
    return eligible[0] if len(eligible) == 1 else None


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def sync_status_path(invoice_records_path: Path) -> Path:
    return invoice_records_path.with_name("invoice-sync-status.json")


def write_failure_status(path: Path, message: str, checked_at: str | None = None) -> None:
    _write_json(
        path,
        {
            "source": "gmail",
            "status": "failed",
            "checked_at": checked_at or datetime.now(TAIPEI).isoformat(timespec="seconds"),
            "messages_scanned": 0,
            "records_updated": 0,
            "unmatched_records": 0,
            "note": message[:160],
        },
    )


def write_disabled_status(path: Path) -> None:
    _write_json(
        path,
        {
            "source": "gmail",
            "status": "disabled",
            "checked_at": None,
            "messages_scanned": 0,
            "records_updated": 0,
            "unmatched_records": 0,
            "note": "尚未啟用 Gmail 唯讀發票核對",
        },
    )


def sync_gmail_invoices(
    config: GmailConfig,
    *,
    dashboard_path: Path,
    invoice_records_path: Path,
    client: GmailClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(TAIPEI)
    checked_at = current.isoformat(timespec="seconds")
    gmail = client or GmailClient(config)
    dashboard = _read_json(dashboard_path, {"events": []})
    existing = _read_json(invoice_records_path, [])
    if not isinstance(existing, list):
        existing = []
    existing_by_id = {
        str(record.get("id")): record
        for record in existing
        if isinstance(record, dict) and record.get("id")
    }

    gmail_ids_to_replace: set[str] = set()
    generated: list[dict[str, Any]] = []
    scanned = 0
    parse_failures = 0
    unmatched = 0
    matched_by_exchange = {
        exchange: {
            str(record.get("trade_date"))
            for record in existing
            if isinstance(record, dict)
            and record.get("exchange") == exchange
            and record.get("trade_date")
        }
        for exchange in DEFAULT_QUERIES
    }

    for exchange, query in config.queries.items():
        messages = [gmail.get_message(message_id) for message_id in gmail.list_message_ids(query)]
        messages.sort(key=lambda message: int(str(message.get("internalDate", "0"))))
        available_dates = live_trade_dates(dashboard, exchange)
        for message in messages:
            scanned += 1
            text = message_text(message, gmail)
            number = parse_invoice_number(text)
            if not is_expected_invoice(exchange, text, number):
                continue
            if not number:
                parse_failures += 1
                continue
            message_id = str(message.get("id", ""))
            if not message_id:
                continue
            record_id = f"gmail-{exchange}-{hashlib.sha256(message_id.encode()).hexdigest()[:16]}"
            received = received_date(message)
            explicit_trade_date = _labeled_date(
                text, ("交易日期", "成交日期", "消費日期", "消費時間", "訂單日期")
            )
            previous_trade_date = str(
                existing_by_id.get(record_id, {}).get("trade_date") or ""
            ) or None
            if previous_trade_date:
                matched_by_exchange[exchange].discard(previous_trade_date)
            trade_date = choose_trade_date(
                explicit_date=explicit_trade_date or previous_trade_date,
                received=received,
                available_dates=available_dates,
                already_matched=matched_by_exchange[exchange],
            )
            if trade_date:
                matched_by_exchange[exchange].add(trade_date)
                note = "Gmail 唯讀 OAuth 已確認發票並自動對應成交日"
            else:
                unmatched += 1
                note = "Gmail 已確認發票，但無法唯一對應成交日，請人工核對"
            generated.append(
                {
                    "id": record_id,
                    "exchange": exchange,
                    "trade_date": trade_date,
                    "status": "confirmed",
                    "checked_at": checked_at,
                    "issued_date": _labeled_date(
                        text, ("發票開立日期", "開立日期", "發票日期")
                    ),
                    "amount_twd": parse_amount(text),
                    "masked_number": mask_invoice_number(number),
                    "detail_url": None,
                    "note": note,
                }
            )
            gmail_ids_to_replace.add(record_id)

    merged = [
        record
        for record in existing
        if isinstance(record, dict) and str(record.get("id", "")) not in gmail_ids_to_replace
    ]
    merged.extend(generated)
    merged.sort(
        key=lambda record: str(
            record.get("trade_date") or record.get("issued_date") or record.get("checked_at") or ""
        ),
        reverse=True,
    )
    _write_json(invoice_records_path, merged)
    status = "partial" if unmatched or parse_failures else "success"
    if status == "success":
        note = f"Gmail 唯讀核對完成；本次辨識 {len(generated)} 封發票信"
    else:
        note = (
            f"Gmail 核對完成，但有 {unmatched} 筆無法對應成交日、"
            f"{parse_failures} 封未辨識出發票號碼"
        )
    sync_status = {
        "source": "gmail",
        "status": status,
        "checked_at": checked_at,
        "messages_scanned": scanned,
        "records_updated": len(generated),
        "unmatched_records": unmatched,
        "note": note,
    }
    _write_json(sync_status_path(invoice_records_path), sync_status)
    return sync_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="以 Gmail 唯讀 OAuth 核對電子發票")
    parser.add_argument(
        "--disabled", action="store_true", help="只把公開同步狀態標示為未啟用"
    )
    args = parser.parse_args(argv)
    invoice_records_path = Path(
        os.getenv("INVOICE_RECORDS_PATH", "data/invoice-records.json")
    )
    status_path = sync_status_path(invoice_records_path)
    if args.disabled:
        write_disabled_status(status_path)
        print("Gmail 發票核對未啟用")
        return 0
    try:
        status = sync_gmail_invoices(
            GmailConfig.from_env(),
            dashboard_path=Path(
                os.getenv("DASHBOARD_PATH", "public/data/dashboard.json")
            ),
            invoice_records_path=invoice_records_path,
        )
    except GmailSyncError as exc:
        safe_message = str(exc).replace(os.getcwd(), "<workspace>")[:160]
        write_failure_status(status_path, safe_message)
        print(f"Gmail 發票核對失敗：{safe_message}", file=sys.stderr)
        return 1
    except Exception:
        safe_message = "Gmail 發票核對發生未預期錯誤；請查看 Actions step"
        write_failure_status(status_path, safe_message)
        print(safe_message, file=sys.stderr)
        return 1
    print(
        f"Gmail 發票核對完成：掃描={status['messages_scanned']} "
        f"更新={status['records_updated']} 未配對={status['unmatched_records']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
