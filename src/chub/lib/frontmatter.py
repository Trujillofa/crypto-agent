from __future__ import annotations

import re

import yaml

_FRONTMATTER_PATTERN = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
JSONDict = dict[str, object]


def parse_frontmatter(content: str) -> tuple[JSONDict, str]:
    match = _FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content

    raw_metadata = match.group(1)
    body_start = match.end()
    body = content[body_start:]

    loaded = yaml.safe_load(raw_metadata)
    metadata = loaded if isinstance(loaded, dict) else {}
    return metadata, body
