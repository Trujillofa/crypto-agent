from __future__ import annotations

from typing import Any

import click

from ..lib.config import ChubConfig
from ..lib.output import print_entry, print_error, print_search_results
from ..lib.registry import get_entry, list_entries, load_registry, search_registry


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


def _tags_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _entry_to_output(entry: dict[str, Any], entry_type: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or ""),
        "description": str(entry.get("description") or ""),
        "source": str(entry.get("source") or ""),
        "type": entry_type,
    }
    tags = entry.get("tags")
    payload["tags"] = [str(tag) for tag in tags] if isinstance(tags, list) else []
    return payload


@click.command()
@click.argument("query", required=False)
@click.option("--tags", help="Filter by comma-separated tags")
@click.option("--lang", help="Filter by language")
@click.option("--limit", default=20, type=int, help="Max results")
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def search(
    ctx: click.Context,
    query: str | None,
    tags: str | None,
    lang: str | None,
    limit: int,
    json_output: bool,
) -> None:
    use_json = _json_enabled(ctx, json_output)

    try:
        config = _ctx_config(ctx)
        registry, source = load_registry(config)
    except Exception as exc:  # noqa: BLE001
        print_error(str(exc), json_output=use_json)
        return

    query_text = (query or "").strip()
    parsed_tags = _tags_list(tags)

    if query_text:
        exact = get_entry(registry, query_text)
        if isinstance(exact, dict):
            exact_type = "skill" if "path" in exact and "languages" not in exact else "doc"
            output_entry = _entry_to_output(exact, exact_type)
            output_entry["source_name"] = source.name
            print_entry(output_entry, content="", json_output=use_json, additional_files=[])
            return

    if not query_text:
        results = list_entries(registry, parsed_tags, lang or "", max(limit, 0))
    else:
        results = search_registry(
            registry=registry,
            query=query_text,
            tags=parsed_tags,
            lang=lang,
            limit=max(limit, 0),
        )

    print_search_results(results, json_output=use_json)
