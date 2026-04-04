from __future__ import annotations

import click

from ..lib.cache import cache_status, clear_cache
from ..lib.config import ChubConfig
from ..lib.output import print_error


def _ctx_config(ctx: click.Context) -> ChubConfig:
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    config = obj.get("config")
    if not isinstance(config, ChubConfig):
        raise click.ClickException("Context Hub config not loaded.")
    return config


@click.command("cache")
@click.argument("action", type=click.Choice(["status", "clear"]))
@click.option("--force", is_flag=True)
@click.pass_context
def cache(ctx: click.Context, action: str, force: bool) -> None:
    try:
        config = _ctx_config(ctx)
    except Exception as exc:  # noqa: BLE001
        print_error(str(exc))
        return

    if action == "status":
        payload = cache_status(config)
        click.echo(f"Cache root: {payload.get('cache_dir')}")
        sources = payload.get("sources")
        if not isinstance(sources, list) or not sources:
            click.echo("No configured sources.")
            return
        for item in sources:
            if not isinstance(item, dict):
                continue
            click.echo(
                f"- {item.get('name')}: cached={item.get('cached')}, "
                f"files={item.get('files_cached')}, "
                f"last_updated={item.get('registry_last_updated')}"
            )
        return

    if action == "clear":
        if not force and not click.confirm("Clear all chub cached data?", default=False):
            click.echo("Cache clear cancelled.")
            return
        clear_cache(config, source_name=None, force=force)
        click.echo("Cache cleared.")
