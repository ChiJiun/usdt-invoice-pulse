from __future__ import annotations

import os
import sys

from bot.config import Settings
from bot.exchanges import BitoProAdapter, MaxAdapter


def safe_error(exc: Exception) -> str:
    return str(exc).replace(os.getcwd(), "<workspace>")[:220]


def main() -> int:
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"設定錯誤：{safe_error(exc)}", file=sys.stderr)
        return 2

    checks = []
    if settings.bitopro_enabled:
        checks.append(("BitoPro", BitoProAdapter(settings)))
    if settings.max_enabled:
        checks.append(("MAX", MaxAdapter(settings)))

    if not checks:
        print("沒有啟用可驗證的交易所；未送出任何訂單。")
        return 0

    failures = 0
    for name, adapter in checks:
        try:
            adapter.verify_credentials()
        except Exception as exc:
            failures += 1
            print(f"{name}：驗證失敗 — {safe_error(exc)}", file=sys.stderr)
        else:
            print(f"{name}：API 簽章與帳戶讀取權限正常")

    print("本次僅讀取帳戶資訊，未送出任何訂單。")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
