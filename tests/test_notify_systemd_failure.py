from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.notify_systemd_failure import build_message, send_failure_alert, telegram_enabled


def test_telegram_enabled_accepts_common_true_values() -> None:
    assert telegram_enabled("true")
    assert telegram_enabled("YES")
    assert not telegram_enabled("false")
    assert not telegram_enabled(None)


def test_build_message_contains_unit_and_host() -> None:
    message = build_message("crypto-agent-test.service", "prod-host")

    assert "prod-host" in message
    assert "crypto-agent-test.service" in message


def test_send_failure_alert_requires_enabled_config() -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        send_failure_alert(
            unit="test.service",
            hostname="prod-host",
            bot_token="token",
            chat_id="chat",
            enabled="false",
        )


def test_send_failure_alert_requires_complete_credentials() -> None:
    with pytest.raises(RuntimeError, match="incomplete"):
        send_failure_alert(
            unit="test.service",
            hostname="prod-host",
            bot_token=None,
            chat_id="chat",
            enabled="true",
        )


def test_send_failure_alert_posts_encoded_payload(monkeypatch) -> None:
    response = MagicMock()
    response.read.return_value = b'{"ok": true}'
    response.__enter__.return_value = response
    urlopen = MagicMock(return_value=response)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    send_failure_alert(
        unit="crypto-agent-test.service",
        hostname="prod-host",
        bot_token="secret-token",
        chat_id="12345",
        enabled="true",
    )

    request = urlopen.call_args.args[0]
    assert request.full_url.endswith("/botsecret-token/sendMessage")
    assert b"chat_id=12345" in request.data
    assert b"crypto-agent-test.service" in request.data
