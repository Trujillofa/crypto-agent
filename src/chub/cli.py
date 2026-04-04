from __future__ import annotations

import click

from src.chub import __version__
from src.chub.commands import annotate, build, cache, feedback, get, search, update
from src.chub.lib import config as config_lib


@click.group()
@click.option("--json", "json_output", is_flag=True, hidden=True)
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx: click.Context, json_output: bool) -> None:
    """Context Hub — search and retrieve LLM-optimized docs and skills."""
    ctx.ensure_object(dict)
    try:
        cfg = config_lib.load_config()
        ctx.obj["config"] = cfg
        ctx.obj["json_output"] = json_output
    except Exception as e:
        click.echo(f"Error loading config: {e}", err=True)
        ctx.obj["config"] = None
        ctx.obj["json_output"] = json_output


cli.add_command(search.search)
cli.add_command(get.get)
cli.add_command(annotate.annotate)
cli.add_command(feedback.feedback)
cli.add_command(update.update)
cli.add_command(cache.cache)
cli.add_command(build.build)


if __name__ == "__main__":
    cli()
