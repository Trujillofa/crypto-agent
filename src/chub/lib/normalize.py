from __future__ import annotations

LANGUAGE_ALIASES: dict[str, str] = {
    "js": "javascript",
    "javascript": "javascript",
    "node": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    "py": "python",
    "python": "python",
    "golang": "go",
    "csharp": "c#",
    "cs": "c#",
}


def normalize_language(lang: str) -> str:
    value = lang.strip().lower()
    return LANGUAGE_ALIASES.get(value, value)
