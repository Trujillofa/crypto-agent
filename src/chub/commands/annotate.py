from __future__ import annotations

import click

from ..lib import annotations as annotations_lib
from ..lib.output import print_error, print_info, print_json


def _json_enabled(ctx: click.Context, json_output: bool) -> bool:
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    inherited = obj.get("json_output")
    return bool(json_output or inherited)


@click.command()
@click.argument("id", required=False)
@click.argument("note", required=False)
@click.option("--clear", is_flag=True)
@click.option("--list", "list_all", is_flag=True)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def annotate(
    ctx: click.Context,
    id: str | None,
    note: str | None,
    clear: bool,
    list_all: bool,
    json_output: bool,
) -> None:
    use_json = _json_enabled(ctx, json_output)

    if list_all:
        records = annotations_lib.list_annotations()
        if use_json:
            print_json({"annotations": [{"id": key, "note": value} for key, value in records]})
            return
        if not records:
            print_info("No annotations found.", json_output=False)
            return
        for key, value in records:
            click.echo(f"{key}: {value}")
        return

    if clear:
        if not id:
            print_error("--clear requires an entry ID.", json_output=use_json)
            return
        before = annotations_lib.get_annotation(id)
        annotations_lib.clear_annotation(id)
        if use_json:
            print_json({"id": id, "cleared": before is not None})
            return
        if before is None:
            print_info(f"No annotation found for '{id}'.", json_output=False)
        else:
            print_info(f"Cleared annotation for '{id}'.", json_output=False)
        return

    if not id:
        print_error("Provide an ID, or use --list.", json_output=use_json)
        return

    if note is None:
        existing = annotations_lib.get_annotation(id)
        if use_json:
            print_json({"id": id, "note": existing})
            return
        if existing is None:
            print_info(f"No annotation found for '{id}'.", json_output=False)
        else:
            click.echo(existing)
        return

    annotations_lib.set_annotation(id, note)
    if use_json:
        print_json({"id": id, "note": note, "updated": True})
        return
    print_info(f"Saved annotation for '{id}'.", json_output=False)
