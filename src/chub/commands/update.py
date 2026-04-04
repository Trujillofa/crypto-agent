from __future__ import annotations

from pathlib import PurePosixPath

import click

from ..lib.cache import fetch_file, fetch_registry
from ..lib.config import ChubConfig
from ..lib.output import print_error, print_info


def _ctx_config(ctx: click.Context) -> ChubConfig:
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    config = obj.get("config")
    if not isinstance(config, ChubConfig):
        raise click.ClickException("Context Hub config not loaded.")
    return config


def _collect_registry_paths(registry: dict[str, object]) -> set[str]:
    paths: set[str] = set()

    docs = registry.get("docs")
    if isinstance(docs, list):
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            languages = doc.get("languages")
            if not isinstance(languages, list):
                continue
            for lang_block in languages:
                if not isinstance(lang_block, dict):
                    continue
                versions = lang_block.get("versions")
                if not isinstance(versions, list):
                    continue
                for version in versions:
                    if not isinstance(version, dict):
                        continue
                    base_path = version.get("path")
                    files = version.get("files")
                    if isinstance(base_path, str) and isinstance(files, list):
                        for item in files:
                            if isinstance(item, str):
                                paths.add(str(PurePosixPath(base_path) / PurePosixPath(item)))
                    elif isinstance(base_path, str):
                        paths.add(base_path)

    skills = registry.get("skills")
    if isinstance(skills, list):
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            base_path = skill.get("path")
            files = skill.get("files")
            if isinstance(base_path, str) and isinstance(files, list):
                for item in files:
                    if isinstance(item, str):
                        paths.add(str(PurePosixPath(base_path) / PurePosixPath(item)))
            elif isinstance(base_path, str):
                paths.add(base_path)

    return paths


@click.command()
@click.option("--force", is_flag=True)
@click.option("--full", is_flag=True)
@click.pass_context
def update(ctx: click.Context, force: bool, full: bool) -> None:
    try:
        config = _ctx_config(ctx)
    except Exception as exc:  # noqa: BLE001
        print_error(str(exc))
        return

    updated = 0
    skipped = 0
    full_files = 0

    for source in config.sources:
        try:
            before = fetch_registry(config, source, force=False)
            after = fetch_registry(config, source, force=force)
            if before == after and not force:
                skipped += 1
                print_info(f"{source.name}: registry unchanged")
            else:
                updated += 1
                print_info(f"{source.name}: registry refreshed")

            if full:
                all_paths = _collect_registry_paths(after)
                for path in sorted(all_paths):
                    fetch_file(config, source, path, force=force)
                full_files += len(all_paths)
                print_info(f"{source.name}: prefetched {len(all_paths)} files")
        except Exception as exc:  # noqa: BLE001
            print_error(f"{source.name}: {exc}")

    click.echo(
        f"Update complete: {updated} updated, {skipped} unchanged, full files fetched={full_files}"
    )
