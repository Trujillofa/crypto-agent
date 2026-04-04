from __future__ import annotations

from pathlib import Path, PurePosixPath

import click

from ..lib import annotations as annotations_lib
from ..lib.cache import fetch_file
from ..lib.config import ChubConfig
from ..lib.output import print_entry, print_error
from ..lib.registry import get_entry, load_registry, resolve_entry_path


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


def _select_doc_block(entry: dict[str, object], lang: str | None) -> dict[str, object]:
    languages = entry.get("languages")
    if isinstance(languages, list):
        if lang:
            lowered = lang.strip().lower()
            for item in languages:
                if not isinstance(item, dict):
                    continue
                language = str(item.get("language") or item.get("lang") or "").lower()
                if language == lowered:
                    return item
        for item in languages:
            if isinstance(item, dict):
                return item
    return entry


def _select_version_block(doc_block: dict[str, object], version: str | None) -> dict[str, object]:
    versions = doc_block.get("versions")
    if isinstance(versions, list):
        if version:
            for item in versions:
                if isinstance(item, dict) and str(item.get("version") or "") == version:
                    return item
        recommended = doc_block.get("recommendedVersion")
        if isinstance(recommended, str):
            for item in versions:
                if isinstance(item, dict) and str(item.get("version") or "") == recommended:
                    return item
        for item in versions:
            if isinstance(item, dict):
                return item
    return doc_block


def _available_files(
    entry: dict[str, object],
    entry_type: str,
    lang: str | None,
    version: str | None,
) -> list[str]:
    files: list[str] = []
    if entry_type == "skill":
        raw = entry.get("files")
        if isinstance(raw, list):
            files = [str(item) for item in raw if isinstance(item, str)]
        return files

    doc_block = _select_doc_block(entry, lang)
    version_block = _select_version_block(doc_block, version)
    raw_files = version_block.get("files")
    if isinstance(raw_files, list):
        files = [str(item) for item in raw_files if isinstance(item, str)]
    if not files:
        raw_files = doc_block.get("files")
        if isinstance(raw_files, list):
            files = [str(item) for item in raw_files if isinstance(item, str)]
    return files


def _join_path(base_path: str, relative_path: str) -> str:
    base = PurePosixPath(base_path)
    if base.suffix:
        root = base.parent
    else:
        root = base
    joined = root / PurePosixPath(relative_path)
    return str(joined).replace("\\", "/")


def _normalize_output_entry(entry: dict[str, object], entry_type: str) -> dict[str, object]:
    tags = entry.get("tags")
    return {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or ""),
        "description": str(entry.get("description") or ""),
        "source": str(entry.get("source") or ""),
        "type": entry_type,
        "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
    }


def _prepend_annotation(content: str, note: str | None) -> str:
    if not note:
        return content
    return f"# Local annotation\n\n{note.strip()}\n\n{content}"


def _render_files_content(entry_id: str, fetched_files: list[tuple[str, str]]) -> str:
    if len(fetched_files) == 1:
        return fetched_files[0][1]

    parts: list[str] = []
    for file_name, file_body in fetched_files:
        parts.append(f"--- {entry_id}:{file_name} ---\n{file_body.rstrip()}\n")
    return "\n".join(parts).strip() + "\n"


def _write_output(path_value: str, contents: list[tuple[str, str]], many: bool) -> list[Path]:
    destination = Path(path_value).expanduser()
    written: list[Path] = []

    if many or destination.is_dir() or path_value.endswith("/"):
        destination.mkdir(parents=True, exist_ok=True)
        for entry_id, payload in contents:
            file_name = f"{entry_id.replace('/', '__')}.md"
            target = destination / file_name
            target.write_text(payload, encoding="utf-8")
            written.append(target)
        return written

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(contents[0][1], encoding="utf-8")
    return [destination]


@click.command()
@click.argument("ids", nargs=-1)
@click.option("--lang")
@click.option("--version")
@click.option("--full", is_flag=True)
@click.option("--file")
@click.option("-o", "--output")
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def get(
    ctx: click.Context,
    ids: tuple[str, ...],
    lang: str | None,
    version: str | None,
    full: bool,
    file: str | None,
    output: str | None,
    json_output: bool,
) -> None:
    use_json = _json_enabled(ctx, json_output)
    if not ids:
        print_error("Please provide at least one entry ID.", json_output=use_json)
        return

    try:
        config = _ctx_config(ctx)
        registry, source = load_registry(config)
    except Exception as exc:  # noqa: BLE001
        print_error(str(exc), json_output=use_json)
        return

    output_payload: list[tuple[dict[str, object], str, list[str]]] = []
    writes: list[tuple[str, str]] = []

    for entry_id in ids:
        entry = get_entry(registry, entry_id)
        if not isinstance(entry, dict):
            print_error(f"Entry '{entry_id}' not found.", json_output=use_json)
            return

        entry_type = "skill" if "path" in entry and "languages" not in entry else "doc"
        normalized_entry = _normalize_output_entry(entry, entry_type)

        try:
            resolved_path, _resolved_lang = resolve_entry_path(entry, lang=lang, version=version)
        except Exception as exc:  # noqa: BLE001
            print_error(str(exc), json_output=use_json)
            return

        available = _available_files(entry, entry_type, lang, version)
        if file:
            selected_files = [part.strip() for part in file.split(",") if part.strip()]
        elif full and available:
            selected_files = available
        else:
            selected_files = []

        fetch_paths: list[tuple[str, str]] = []
        if selected_files:
            for relative_file in selected_files:
                fetch_paths.append((relative_file, _join_path(resolved_path, relative_file)))
        else:
            resolved_name = PurePosixPath(resolved_path).name
            fetch_paths.append((resolved_name, resolved_path))

        fetched_files: list[tuple[str, str]] = []
        for display_name, fetch_path_value in fetch_paths:
            try:
                body = fetch_file(config, source, fetch_path_value, force=False)
            except Exception as exc:  # noqa: BLE001
                print_error(str(exc), json_output=use_json)
                return
            fetched_files.append((display_name, body))

        content = _render_files_content(entry_id, fetched_files)
        annotation = annotations_lib.get_annotation(entry_id)
        content = _prepend_annotation(content, annotation)

        remaining = [item for item in available if item not in [name for name, _ in fetched_files]]
        output_payload.append((normalized_entry, content, remaining))
        writes.append((entry_id, content))

    if output:
        written_paths = _write_output(output, writes, many=len(writes) > 1)
        if not use_json:
            for path in written_paths:
                click.echo(f"Wrote: {path}")

    if output and not use_json:
        for entry_data, _content, extras in output_payload:
            if extras:
                click.echo(
                    f"Additional files available for {entry_data['id']}: {', '.join(extras)}"
                )
        return

    for entry_data, content, extras in output_payload:
        print_entry(entry_data, content, json_output=use_json, additional_files=extras)
