from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if params:
            query = urlencode(params)
            url = f"{url}{'&' if '?' in url else '?'}{query}"

        encoded_body = None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            encoded_body = json.dumps(body, separators=(",", ":")).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")

        request = Request(url, data=encoded_body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")[:500]
            try:
                detail = json.loads(raw)
                if isinstance(detail, dict):
                    error = detail.get("error", detail)
                    message = error.get("message") if isinstance(error, dict) else None
                else:
                    message = None
            except json.JSONDecodeError:
                message = None
            safe_message = message or f"HTTP {exc.code}"
            raise ApiError(f"交易所 API 拒絕請求：{safe_message}") from exc
        except (URLError, TimeoutError) as exc:
            raise ApiError("交易所 API 連線失敗") from exc

