from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import quote, urljoin

import httpx

from .config import ChubConfig, Source

JSONDict = dict[str, object]


def get_chub_dir() -> Path:
    chub_dir = Path.home() / ".chub"
    chub_dir.mkdir(parents=True, exist_ok=True)
    return chub_dir


def get_source_dir(source_name: str) -> Path:
    source_dir = get_chub_dir() / "sources" / source_name
    source_dir.mkdir(parents=True, exist_ok=True)
    return source_dir


def _registry_path(source_name: str) -> Path:
    return get_source_dir(source_name) / "registry.json"


def _meta_path(source_name: str) -> Path:
    return get_source_dir(source_name) / "meta.json"


def _is_stale(path: Path, refresh_interval: int) -> bool:
    if not path.exists():
        return True
    if refresh_interval <= 0:
        return False
    age_seconds = datetime.now(tz=UTC).timestamp() - path.stat().st_mtime
    return age_seconds > refresh_interval


def _write_meta(source_name: str, registry: JSONDict) -> None:
    payload = {
        "lastUpdated": datetime.now(tz=UTC).isoformat(),
        "registryHash": str(hash(json.dumps(registry, sort_keys=True))),
    }
    _meta_path(source_name).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _safe_rel_path(path: str) -> Path:
    normalized = PurePosixPath(path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"Unsafe content path: {path}")
    return Path(*normalized.parts)


def _encoded_join(base_url: str, relative_path: str) -> str:
    quoted_path = quote(str(PurePosixPath(relative_path)), safe="/-_.~")
    return urljoin(base_url.rstrip("/") + "/", quoted_path)


def fetch_registry(config: ChubConfig, source: Source, force: bool = False) -> JSONDict:
    registry_path = _registry_path(source.name)
    if not force and not _is_stale(registry_path, config.refresh_interval):
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data

    if source.path:
        local_registry = Path(source.path) / "registry.json"
        if not local_registry.exists():
            raise FileNotFoundError(f"Registry not found for source '{source.name}'")
        data = json.loads(local_registry.read_text(encoding="utf-8"))
    elif source.url:
        url = _encoded_join(source.url, "registry.json")
        with httpx.Client(follow_redirects=True, timeout=20) as client:
            response = client.get(url)
            response.raise_for_status()
        data = response.json()
    else:
        raise ValueError(f"Source '{source.name}' must define either url or path")

    if not isinstance(data, dict):
        raise ValueError(f"Invalid registry payload for source '{source.name}'")

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_meta(source.name, cast(JSONDict, data))
    return data


def fetch_file(config: ChubConfig, source: Source, path: str, force: bool = False) -> str:
    rel_path = _safe_rel_path(path)
    cache_file_path = get_source_dir(source.name) / "data" / rel_path

    if (
        cache_file_path.exists()
        and not force
        and not _is_stale(cache_file_path, config.refresh_interval)
    ):
        return cache_file_path.read_text(encoding="utf-8")

    if source.path:
        source_file = Path(source.path) / rel_path
        if not source_file.exists():
            raise FileNotFoundError(f"File not found for source '{source.name}': {path}")
        content = source_file.read_text(encoding="utf-8")
    elif source.url:
        url = _encoded_join(source.url, str(rel_path).replace("\\", "/"))
        with httpx.Client(follow_redirects=True, timeout=20) as client:
            response = client.get(url)
            response.raise_for_status()
        content = response.text
    else:
        raise ValueError(f"Source '{source.name}' must define either url or path")

    cache_file_path.parent.mkdir(parents=True, exist_ok=True)
    cache_file_path.write_text(content, encoding="utf-8")
    return content


def cache_status(config: ChubConfig) -> JSONDict:
    sources_status: list[dict[str, object]] = []
    for source in config.sources:
        source_dir = get_source_dir(source.name)
        registry_path = source_dir / "registry.json"
        data_dir = source_dir / "data"

        file_count = 0
        if data_dir.exists():
            file_count = sum(1 for item in data_dir.rglob("*") if item.is_file())

        sources_status.append(
            {
                "name": source.name,
                "cached": registry_path.exists(),
                "registry_path": str(registry_path),
                "registry_last_updated": (
                    datetime.fromtimestamp(registry_path.stat().st_mtime, tz=UTC).isoformat()
                    if registry_path.exists()
                    else None
                ),
                "files_cached": file_count,
                "source_url": source.url,
                "source_path": source.path,
            }
        )

    return {
        "cache_dir": str(get_chub_dir()),
        "sources": sources_status,
    }


def clear_cache(config: ChubConfig, source_name: str | None = None, force: bool = False) -> None:
    del config
    del force

    if source_name:
        target = get_chub_dir() / "sources" / source_name
        if target.exists():
            shutil.rmtree(target)
        return

    sources_dir = get_chub_dir() / "sources"
    if sources_dir.exists():
        shutil.rmtree(sources_dir)
