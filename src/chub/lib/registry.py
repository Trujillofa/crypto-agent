from __future__ import annotations

import json

from .cache import fetch_registry, get_source_dir
from .config import ChubConfig, Source
from .normalize import normalize_language

JSONDict = dict[str, object]
JSONList = list[JSONDict]


def _entry_languages(entry: dict[str, object]) -> list[str]:
    languages = entry.get("languages")
    if isinstance(languages, dict):
        return [normalize_language(str(key)) for key in languages.keys()]
    if isinstance(languages, list):
        values: list[str] = []
        for item in languages:
            if isinstance(item, str):
                values.append(normalize_language(item))
            elif isinstance(item, dict):
                value = item.get("language") or item.get("lang") or item.get("id")
                if isinstance(value, str):
                    values.append(normalize_language(value))
        return values
    return []


def _normalize_entry(entry: dict[str, object], entry_type: str) -> dict[str, object]:
    tags_raw = entry.get("tags")
    tags = (
        [str(tag) for tag in tags_raw if isinstance(tag, str)] if isinstance(tags_raw, list) else []
    )
    return {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or ""),
        "description": str(entry.get("description") or ""),
        "source": str(entry.get("source") or ""),
        "type": entry_type,
        "tags": tags,
    }


def _has_lang(entry: dict[str, object], lang: str | None) -> bool:
    if lang is None:
        return True
    normalized = normalize_language(lang)
    languages = _entry_languages(entry)
    if not languages:
        return True
    return normalized in languages


def _has_tags(entry: dict[str, object], tags: list[str]) -> bool:
    if not tags:
        return True
    entry_tags_raw = entry.get("tags")
    entry_tags = (
        {str(tag).strip().lower() for tag in entry_tags_raw if isinstance(tag, str)}
        if isinstance(entry_tags_raw, list)
        else set()
    )
    normalized_tags = {tag.strip().lower() for tag in tags if tag.strip()}
    return normalized_tags.issubset(entry_tags)


def _score_query(entry: dict[str, object], query: str) -> int:
    if not query:
        return 1

    tokens = [token for token in query.lower().split() if token]
    tags_raw = entry.get("tags")
    tags_text = ""
    if isinstance(tags_raw, list):
        tags_text = " ".join(str(tag).lower() for tag in tags_raw if isinstance(tag, str))

    fields = [
        str(entry.get("id") or "").lower(),
        str(entry.get("name") or "").lower(),
        str(entry.get("description") or "").lower(),
        tags_text,
    ]

    score = 0
    for field in fields:
        if query in field:
            score += 8
        score += sum(2 for token in tokens if token in field)

    return score


def load_registry(config: ChubConfig) -> tuple[JSONDict, Source]:
    errors: list[str] = []

    for source in config.sources:
        registry_path = get_source_dir(source.name) / "registry.json"
        if registry_path.exists():
            try:
                cached = json.loads(registry_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict):
                    return cached, source
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{source.name}: invalid cached registry ({exc})")

        try:
            fetched = fetch_registry(config, source, force=False)
            if isinstance(fetched, dict):
                return fetched, source
        except Exception as exc:  # noqa: BLE001
            if registry_path.exists():
                try:
                    fallback = json.loads(registry_path.read_text(encoding="utf-8"))
                    if isinstance(fallback, dict):
                        return fallback, source
                except (OSError, json.JSONDecodeError):
                    pass
            errors.append(f"{source.name}: {exc}")

    joined_errors = "; ".join(errors) if errors else "no configured sources"
    raise FileNotFoundError(f"Unable to load a registry from any source ({joined_errors})")


def search_registry(
    registry: JSONDict,
    query: str | None,
    tags: list[str],
    lang: str | None,
    limit: int,
) -> JSONList:
    docs = registry.get("docs", [])
    skills = registry.get("skills", [])

    query_text = (query or "").strip().lower()
    candidates: list[tuple[int, dict[str, object]]] = []

    if isinstance(docs, list):
        for entry in docs:
            if not isinstance(entry, dict):
                continue
            if not _has_tags(entry, tags) or not _has_lang(entry, lang):
                continue
            score = _score_query(entry, query_text)
            if query_text and score <= 0:
                continue
            candidates.append((score, _normalize_entry(entry, "doc")))

    if isinstance(skills, list):
        for entry in skills:
            if not isinstance(entry, dict):
                continue
            if not _has_tags(entry, tags):
                continue
            score = _score_query(entry, query_text)
            if query_text and score <= 0:
                continue
            candidates.append((score, _normalize_entry(entry, "skill")))

    candidates.sort(key=lambda item: (-item[0], str(item[1]["name"]).lower(), str(item[1]["id"])))
    normalized_limit = max(limit, 0)
    return [entry for _, entry in candidates[:normalized_limit]]


def get_entry(registry: JSONDict, id: str) -> JSONDict | None:
    target = id.strip()
    if not target:
        return None

    for section in ("docs", "skills"):
        entries = registry.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("id") or "") == target:
                return entry
    return None


def _resolve_doc_language_block(
    entry: dict[str, object], lang: str | None
) -> tuple[dict[str, object], str]:
    languages = entry.get("languages")
    requested = normalize_language(lang) if lang else None

    if isinstance(languages, dict):
        mapping: dict[str, dict[str, object]] = {}
        for key, value in languages.items():
            if isinstance(value, str):
                mapping[normalize_language(str(key))] = {"path": value}
            elif isinstance(value, dict):
                mapping[normalize_language(str(key))] = value

        if requested and requested in mapping:
            return mapping[requested], requested
        if mapping:
            first_lang = next(iter(mapping.keys()))
            return mapping[first_lang], first_lang

    if isinstance(languages, list):
        blocks: list[tuple[str, dict[str, object]]] = []
        for item in languages:
            if not isinstance(item, dict):
                continue
            key = item.get("language") or item.get("lang") or item.get("id")
            if not isinstance(key, str):
                continue
            blocks.append((normalize_language(key), item))

        if requested:
            for key, item in blocks:
                if key == requested:
                    return item, key
        if blocks:
            return blocks[0][1], blocks[0][0]

    fallback_lang = requested or "default"
    return entry, fallback_lang


def _extract_path_from_version_block(version_block: object, version: str | None) -> str | None:
    if isinstance(version_block, str):
        return version_block

    if isinstance(version_block, dict):
        if version:
            selected = version_block.get(version)
            if isinstance(selected, str):
                return selected
            if isinstance(selected, dict):
                selected_path = selected.get("path")
                if isinstance(selected_path, str):
                    return selected_path

        default_version = version_block.get("default") or version_block.get("latest")
        if isinstance(default_version, str):
            return default_version

    if isinstance(version_block, list):
        if version:
            for item in version_block:
                if not isinstance(item, dict):
                    continue
                item_version = item.get("version")
                item_path = item.get("path")
                if item_version == version and isinstance(item_path, str):
                    return item_path
        for item in version_block:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                return str(item["path"])

    return None


def resolve_entry_path(entry: JSONDict, lang: str | None, version: str | None) -> tuple[str, str]:
    entry_type = str(entry.get("type") or "")
    if not entry_type:
        entry_type = "skill" if "path" in entry and "languages" not in entry else "doc"

    if entry_type == "skill":
        skill_path = entry.get("path")
        if isinstance(skill_path, str) and skill_path.strip():
            return skill_path, "agnostic"
        files = entry.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, str) and item.strip():
                    return item, "agnostic"
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    return str(item["path"]), "agnostic"
        raise ValueError(f"Skill entry '{entry.get('id')}' does not define a fetchable path")

    lang_block, resolved_lang = _resolve_doc_language_block(entry, lang)
    if version:
        path_from_versions = _extract_path_from_version_block(lang_block.get("versions"), version)
        if path_from_versions:
            return path_from_versions, resolved_lang

    block_path = lang_block.get("path")
    if isinstance(block_path, str) and block_path.strip():
        return block_path, resolved_lang

    files = lang_block.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, str) and item.strip():
                return item, resolved_lang
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                return str(item["path"]), resolved_lang

    root_path = entry.get("path")
    if isinstance(root_path, str) and root_path.strip():
        return root_path, resolved_lang

    raise ValueError(f"Doc entry '{entry.get('id')}' does not define a fetchable path")


def list_entries(registry: JSONDict, tags: list[str], lang: str, limit: int) -> JSONList:
    effective_lang = lang.strip() or None
    return search_registry(
        registry=registry, query=None, tags=tags, lang=effective_lang, limit=limit
    )
