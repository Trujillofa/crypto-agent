from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import click
import httpx

from ..lib.config import ChubConfig
from ..lib.output import print_error, print_info, print_json

FEEDBACK_ENDPOINT = "https://feedback.aichub.org/v1/feedback"
VALID_RATINGS = {"up", "down"}


def _ctx_config(ctx: click.Context) -> ChubConfig:
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    config = obj.get("config")
    if not isinstance(config, ChubConfig):
        raise click.ClickException("Context Hub config not loaded.")
    return config


def _json_enabled(ctx: click.Context, json_output: bool) -> bool:
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    inherited = obj.get("json_output")
    return bool(json_output or inherited)


def _send_feedback(payload: dict[str, Any]) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=5, follow_redirects=True) as client:
            response = client.post(FEEDBACK_ENDPOINT, json=payload)
            response.raise_for_status()
        return True, "sent"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


@click.command()
@click.argument("id", required=False)
@click.argument("rating", required=False)
@click.option("--label", multiple=True)
@click.option("--lang")
@click.option("--file")
@click.option("--agent")
@click.option("--model")
@click.option("--status", is_flag=True)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def feedback(
    ctx: click.Context,
    id: str | None,
    rating: str | None,
    label: tuple[str, ...],
    lang: str | None,
    file: str | None,
    agent: str | None,
    model: str | None,
    status: bool,
    json_output: bool,
) -> None:
    use_json = _json_enabled(ctx, json_output)
    try:
        config = _ctx_config(ctx)
    except Exception as exc:  # noqa: BLE001
        print_error(str(exc), json_output=use_json)
        return

    if status:
        status_payload = {
            "telemetry": config.telemetry,
            "feedback": config.feedback,
            "endpoint": FEEDBACK_ENDPOINT,
        }
        if use_json:
            print_json(status_payload)
            return
        click.echo(f"Telemetry: {'enabled' if config.telemetry else 'disabled'}")
        click.echo(f"Feedback: {'enabled' if config.feedback else 'disabled'}")
        click.echo(f"Endpoint: {FEEDBACK_ENDPOINT}")
        return

    if not config.feedback:
        print_info("Feedback is disabled in config.", json_output=use_json)
        return

    if not id or not rating:
        print_error("Usage: chub feedback <id> <up|down> [options]", json_output=use_json)
        return

    normalized_rating = rating.strip().lower()
    if normalized_rating not in VALID_RATINGS:
        print_error("rating must be 'up' or 'down'.", json_output=use_json)
        return

    payload: dict[str, Any] = {
        "id": id,
        "rating": normalized_rating,
        "labels": [str(item) for item in label],
        "language": lang,
        "file": file,
        "agent": agent,
        "model": model,
        "telemetry": config.telemetry,
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }

    sent, message = _send_feedback(payload)
    if use_json:
        print_json({"sent": sent, "message": message, "payload": payload})
        return
    if sent:
        print_info(f"Feedback submitted for '{id}' ({normalized_rating}).", json_output=False)
    else:
        print_error(f"Feedback could not be submitted: {message}", json_output=False)
