#!/usr/bin/env python3
"""Send a Telegram alert when a production systemd unit fails."""

from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.parse
import urllib.request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Notify Telegram about a failed systemd unit")
    parser.add_argument("--unit", required=True, help="Failed systemd unit name")
    return parser.parse_args()


def telegram_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_message(unit: str, hostname: str) -> str:
    return (
        "CRITICAL: crypto-agent production monitor failed\n\n"
        f"Host: {hostname}\n"
        f"Unit: {unit}\n\n"
        f"Inspect: journalctl -u {unit} --no-pager -n 100"
    )


def send_failure_alert(
    *,
    unit: str,
    hostname: str,
    bot_token: str | None,
    chat_id: str | None,
    enabled: str | None,
    timeout: int = 10,
) -> None:
    if not telegram_enabled(enabled):
        raise RuntimeError("Telegram notifications are disabled")
    if not bot_token or not chat_id:
        raise RuntimeError("Telegram notification credentials are incomplete")

    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": build_message(unit, hostname)}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError("Telegram API rejected the failure alert")


def main() -> int:
    args = parse_args()
    send_failure_alert(
        unit=args.unit,
        hostname=socket.gethostname(),
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        enabled=os.getenv("TELEGRAM_ENABLED"),
    )
    print(f"Sent Telegram failure alert for {args.unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
