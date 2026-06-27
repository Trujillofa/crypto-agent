"""Internal allowlist loader + checker (used only by capture.py + eligibility.py).

Config at config/incentive_ops/endpoint_allowlist.yaml
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .types import EndpointNotAllowed


def load_allowlist(
    path: Path | str = "config/incentive_ops/endpoint_allowlist.yaml",
) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        # minimal default empty (tests may override)
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data.get("allowed", [])


def is_allowed_url(url: str, allow: list[dict[str, Any]] | None = None) -> bool:
    if allow is None:
        allow = load_allowlist()
    try:
        u = urlparse(url)
        host = u.hostname or ""
        path = u.path or "/"
        if not host:
            return False
        for ent in allow:
            if ent.get("host") != host:
                continue
            for pref in ent.get("path_prefixes", ["/"]):
                if path.startswith(pref):
                    return True
    except Exception:
        return False
    return False


def assert_allowed(url: str) -> None:
    if not is_allowed_url(url):
        raise EndpointNotAllowed(f"URL not allowlisted: {url}")
