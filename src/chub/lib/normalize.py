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

SUPPORTED_LANGUAGES: list[str] = [
    "bash",
    "c",
    "c++",
    "c#",
    "go",
    "java",
    "javascript",
    "kotlin",
    "php",
    "python",
    "ruby",
    "rust",
    "swift",
    "typescript",
]


def normalize_language(lang: str) -> str:
    value = lang.strip().lower()
    return LANGUAGE_ALIASES.get(value, value)
