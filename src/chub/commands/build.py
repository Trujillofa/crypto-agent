from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import click

from ..lib.frontmatter import parse_frontmatter


@dataclass(slots=True)
class WarningItem:
    path: str
    message: str


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _entry_files(entry_dir: Path) -> list[str]:
    return [
        str(path.relative_to(entry_dir)).replace("\\", "/")
        for path in sorted(entry_dir.rglob("*"))
        if path.is_file()
    ]


def _entry_size(entry_dir: Path) -> int:
    return sum(path.stat().st_size for path in entry_dir.rglob("*") if path.is_file())


def _parse_file_frontmatter(file_path: Path) -> dict[str, object]:
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    metadata, _body = parse_frontmatter(content)
    return metadata


def _build_docs(content_dir: Path, warnings: list[WarningItem]) -> list[dict[str, object]]:
    doc_groups: dict[str, dict[str, object]] = {}
    versions_index: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    for author_dir in sorted(path for path in content_dir.iterdir() if path.is_dir()):
        docs_dir = author_dir / "docs"
        if not docs_dir.exists() or not docs_dir.is_dir():
            continue

        for entry_dir in sorted(path for path in docs_dir.iterdir() if path.is_dir()):
            doc_file = entry_dir / "DOC.md"
            if not doc_file.exists():
                warnings.append(WarningItem(path=str(entry_dir), message="Missing DOC.md"))
                continue

            metadata = _parse_file_frontmatter(doc_file)
            doc_id = str(metadata.get("id") or f"{author_dir.name}/{entry_dir.name}")
            name = str(metadata.get("name") or entry_dir.name)
            description = str(metadata.get("description") or "")
            source = str(metadata.get("source") or "community")
            tags_raw = metadata.get("tags")
            tags = [str(item) for item in tags_raw] if isinstance(tags_raw, list) else []

            language = str(metadata.get("language") or metadata.get("lang") or "default")
            version = str(metadata.get("version") or "latest")
            last_updated = str(
                metadata.get("lastUpdated") or datetime.now(tz=UTC).date().isoformat()
            )
            path = f"{author_dir.name}/docs/{entry_dir.name}"
            files = _entry_files(entry_dir)
            size = _entry_size(entry_dir)

            if doc_id not in doc_groups:
                doc_groups[doc_id] = {
                    "id": doc_id,
                    "name": name,
                    "description": description,
                    "source": source,
                    "tags": tags,
                    "languages": [],
                }

            versions_index[(doc_id, language)].append(
                {
                    "version": version,
                    "path": path,
                    "files": files,
                    "size": size,
                    "lastUpdated": last_updated,
                }
            )

    for doc_id, doc_entry in doc_groups.items():
        language_items: list[dict[str, object]] = []
        languages = sorted({lang for (entry_id, lang) in versions_index if entry_id == doc_id})
        for language in languages:
            versions = sorted(
                versions_index[(doc_id, language)],
                key=lambda item: str(item.get("version") or ""),
                reverse=True,
            )
            recommended = str(versions[0].get("version") or "latest") if versions else "latest"
            language_items.append(
                {
                    "language": language,
                    "versions": versions,
                    "recommendedVersion": recommended,
                }
            )
        doc_entry["languages"] = language_items

    return sorted(doc_groups.values(), key=lambda item: str(item.get("id") or ""))


def _build_skills(content_dir: Path, warnings: list[WarningItem]) -> list[dict[str, object]]:
    skills: list[dict[str, object]] = []

    for author_dir in sorted(path for path in content_dir.iterdir() if path.is_dir()):
        skills_dir = author_dir / "skills"
        if not skills_dir.exists() or not skills_dir.is_dir():
            continue

        for entry_dir in sorted(path for path in skills_dir.iterdir() if path.is_dir()):
            skill_file = entry_dir / "SKILL.md"
            if not skill_file.exists():
                warnings.append(WarningItem(path=str(entry_dir), message="Missing SKILL.md"))
                continue

            metadata = _parse_file_frontmatter(skill_file)
            tags_raw = metadata.get("tags")
            tags = [str(item) for item in tags_raw] if isinstance(tags_raw, list) else []
            skills.append(
                {
                    "id": str(metadata.get("id") or f"{author_dir.name}/{entry_dir.name}"),
                    "name": str(metadata.get("name") or entry_dir.name),
                    "description": str(metadata.get("description") or ""),
                    "source": str(metadata.get("source") or "community"),
                    "tags": tags,
                    "path": f"{author_dir.name}/skills/{entry_dir.name}",
                    "files": _entry_files(entry_dir),
                    "size": _entry_size(entry_dir),
                    "lastUpdated": str(
                        metadata.get("lastUpdated") or datetime.now(tz=UTC).date().isoformat()
                    ),
                }
            )

    return sorted(skills, key=lambda item: str(item.get("id") or ""))


def _copy_content(content_dir: Path, output_dir: Path) -> None:
    for path in content_dir.rglob("*"):
        if path.is_file():
            target = output_dir / path.relative_to(content_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


@click.command()
@click.argument("content_dir")
@click.option("-o", "--output")
@click.option("--base-url")
@click.option("--validate-only", is_flag=True)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def build(
    ctx: click.Context,
    content_dir: str,
    output: str | None,
    base_url: str | None,
    validate_only: bool,
    json_output: bool,
) -> None:
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    use_json = bool(json_output or obj.get("json_output"))

    source_dir = Path(content_dir).expanduser().resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise click.ClickException(
            f"content_dir '{source_dir}' does not exist or is not a directory."
        )

    warnings: list[WarningItem] = []
    docs = _build_docs(source_dir, warnings)
    skills = _build_skills(source_dir, warnings)

    registry: dict[str, object] = {
        "version": "1.0.0",
        "base_url": base_url or "https://cdn.aichub.org/v1",
        "generated": _now_iso(),
        "docs": docs,
        "skills": skills,
    }

    output_dir = Path(output).expanduser() if output else source_dir / "dist"
    if not validate_only:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "registry.json").write_text(
            json.dumps(registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _copy_content(source_dir, output_dir)

    summary = {
        "docs": len(docs),
        "skills": len(skills),
        "warnings": [{"path": item.path, "message": item.message} for item in warnings],
        "validate_only": validate_only,
        "output": str(output_dir),
    }

    if use_json:
        click.echo(json.dumps(summary, ensure_ascii=False))
        return

    click.echo(f"Build summary: {len(docs)} docs, {len(skills)} skills, {len(warnings)} warning(s)")
    for warning in warnings:
        click.echo(f"- {warning.path}: {warning.message}")
    if validate_only:
        click.echo("Validation completed (no files written).")
    else:
        click.echo(f"Wrote registry and content to {output_dir}")
