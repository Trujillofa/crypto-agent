from __future__ import annotations

import json

import click

JSONDict = dict[str, object]


def print_json(data: object) -> None:
    click.echo(json.dumps(data, ensure_ascii=False))


def print_search_results(results: list[JSONDict], json_output: bool = False) -> None:
    if json_output:
        print_json({"results": results, "count": len(results)})
        return

    if not results:
        click.echo(click.style("No results found.", fg="yellow"))
        return

    click.echo(click.style(f"Found {len(results)} result(s):", fg="green", bold=True))
    for item in results:
        item_type = str(item.get("type") or "unknown")
        source = str(item.get("source") or "")
        tags_raw = item.get("tags")
        tags = tags_raw if isinstance(tags_raw, list) else []
        tags_display = ", ".join(str(tag) for tag in tags)

        id_label = click.style(str(item.get("id") or ""), bold=True)
        type_label = click.style(item_type, fg="cyan")
        click.echo(f"- {id_label} [{type_label}]")
        click.echo(f"  {str(item.get('name') or '')}")
        if item.get("description"):
            click.echo(click.style(f"  {str(item['description'])}", fg="bright_black"))
        if source:
            click.echo(click.style(f"  source: {source}", fg="blue"))
        if tags_display:
            click.echo(click.style(f"  tags: {tags_display}", fg="magenta"))


def print_entry(
    entry: JSONDict,
    content: str,
    json_output: bool = False,
    additional_files: list[str] | None = None,
) -> None:
    additional = additional_files or []
    if json_output:
        payload = {
            "entry": entry,
            "content": content,
            "additional_files": additional,
        }
        print_json(payload)
        return

    entry_id = str(entry.get("id") or "")
    entry_name = str(entry.get("name") or "")
    entry_type = str(entry.get("type") or "unknown")
    click.echo(click.style(f"{entry_name} ({entry_id})", fg="green", bold=True))
    click.echo(click.style(f"Type: {entry_type}", fg="cyan"))
    description = entry.get("description")
    if isinstance(description, str) and description.strip():
        click.echo(click.style(description, fg="bright_black"))

    click.echo("")
    click.echo(content)

    if additional:
        click.echo("")
        click.echo(click.style("Additional files:", fg="yellow", bold=True))
        for file_path in additional:
            click.echo(f"- {file_path}")


def print_error(msg: str, json_output: bool = False) -> None:
    if json_output:
        print_json({"error": msg})
        return
    click.echo(click.style(f"Error: {msg}", fg="red", bold=True), err=True)


def print_info(msg: str, json_output: bool = False) -> None:
    if json_output:
        print_json({"info": msg})
        return
    click.echo(click.style(msg, fg="blue"))
