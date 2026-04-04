from __future__ import annotations

import json
from pathlib import Path


def get_annotations_path() -> Path:
    chub_dir = Path.home() / ".chub"
    chub_dir.mkdir(parents=True, exist_ok=True)
    return chub_dir / "annotations.json"


def load_annotations() -> dict[str, str]:
    annotations_path = get_annotations_path()
    if not annotations_path.exists():
        return {}

    raw = json.loads(annotations_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}

    annotations: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            annotations[key] = value
    return annotations


def save_annotations(annotations: dict[str, str]) -> None:
    annotations_path = get_annotations_path()
    annotations_path.write_text(
        json.dumps(annotations, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def get_annotation(entry_id: str) -> str | None:
    return load_annotations().get(entry_id)


def set_annotation(entry_id: str, note: str) -> None:
    annotations = load_annotations()
    annotations[entry_id] = note
    save_annotations(annotations)


def clear_annotation(entry_id: str) -> None:
    annotations = load_annotations()
    if entry_id in annotations:
        del annotations[entry_id]
        save_annotations(annotations)


def list_annotations() -> list[tuple[str, str]]:
    annotations = load_annotations()
    return sorted(annotations.items(), key=lambda item: item[0])
