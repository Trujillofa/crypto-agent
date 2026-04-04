from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_SOURCE_URL = "https://cdn.aichub.org/v1"
DEFAULT_SOURCE_FILTER = "official,maintainer,community"
DEFAULT_REFRESH_INTERVAL = 86400
ConfigDict = dict[str, object]


@dataclass(slots=True)
class Source:
    name: str
    url: str | None = None
    path: str | None = None


@dataclass(slots=True)
class ChubConfig:
    sources: list[Source]
    source_filter: str = DEFAULT_SOURCE_FILTER
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL
    telemetry: bool = True
    feedback: bool = True


def get_config_path() -> Path:
    return Path.home() / ".chub" / "config.yaml"


def _parse_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _select_fallback_source_url(config_dict: ConfigDict) -> str:
    return (
        os.getenv("CHUB_BUNDLE_URL")
        or os.getenv("CHUB_CDN_URL")
        or os.getenv("CDN_URL")
        or os.getenv("cdn_url")
        or str(config_dict.get("cdn_url") or "").strip()
        or DEFAULT_SOURCE_URL
    )


def _parse_sources(raw_sources: object) -> list[Source]:
    if not isinstance(raw_sources, list):
        return []

    parsed: list[Source] = []
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        item_dict = item
        name = str(item_dict.get("name") or "").strip()
        if not name:
            continue
        url = item_dict.get("url")
        path = item_dict.get("path")
        parsed.append(
            Source(
                name=name,
                url=str(url).strip() if isinstance(url, str) and url.strip() else None,
                path=str(path).strip() if isinstance(path, str) and path.strip() else None,
            )
        )
    return parsed


def load_config() -> ChubConfig:
    config_path = get_config_path()
    config_dict: ConfigDict = {}

    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            config_dict = {str(key): value for key, value in raw.items()}

    sources = _parse_sources(config_dict.get("sources"))
    if not sources:
        fallback_url = _select_fallback_source_url(config_dict)
        sources = [Source(name="community", url=fallback_url)]

    source_filter = str(config_dict.get("source") or DEFAULT_SOURCE_FILTER)

    refresh_interval_raw = config_dict.get("refresh_interval", DEFAULT_REFRESH_INTERVAL)
    try:
        if isinstance(refresh_interval_raw, (int, float, str)):
            refresh_interval = int(refresh_interval_raw)
        else:
            refresh_interval = DEFAULT_REFRESH_INTERVAL
    except (TypeError, ValueError):
        refresh_interval = DEFAULT_REFRESH_INTERVAL

    telemetry = _parse_bool(config_dict.get("telemetry"), True)
    feedback = _parse_bool(config_dict.get("feedback"), True)

    telemetry = _parse_bool(os.getenv("CHUB_TELEMETRY"), telemetry)
    feedback = _parse_bool(os.getenv("CHUB_FEEDBACK"), feedback)

    bundle_override = os.getenv("CHUB_BUNDLE_URL")
    if bundle_override:
        sources = [
            Source(name=source.name, url=bundle_override, path=source.path)
            if source.url is not None
            else source
            for source in sources
        ]

    return ChubConfig(
        sources=sources,
        source_filter=source_filter,
        refresh_interval=max(refresh_interval, 0),
        telemetry=telemetry,
        feedback=feedback,
    )
