"""Deterministic, immutable artifacts for backtest and experiment evidence."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.backtest.engine import BacktestConfig, BacktestResult


def _normalise(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity" if value < 0 else "NaN"
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _normalise(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _normalise(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple, set)):
        return [_normalise(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def canonical_json(value: Any) -> str:
    """Return stable JSON used for fingerprints and persisted manifests."""
    return json.dumps(_normalise(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint_rows(rows: Iterable[Mapping[str, object]]) -> str:
    """Fingerprint the exact ordered rows consumed by a run."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json(row).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def git_revision(repo_root: Path | None = None) -> str | None:
    """Return the checked-out revision without making a run depend on Git."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


@dataclass(frozen=True)
class BacktestManifest:
    """Machine-readable evidence required to reproduce a single simulator run."""

    run_id: str
    semantics_version: str
    git_revision: str | None
    config: dict[str, object]
    result: dict[str, object]
    data_fingerprint: str | None = None
    funding_fingerprint: str | None = None
    seed: int | None = None
    source_config: str | None = None
    trades_fingerprint: str | None = None


def create_manifest(
    *,
    config: BacktestConfig,
    result: BacktestResult,
    semantics_version: str = "legacy_v1",
    data_fingerprint: str | None = None,
    funding_fingerprint: str | None = None,
    seed: int | None = None,
    source_config: str | None = None,
    revision: str | None = None,
) -> BacktestManifest:
    """Create a deterministic manifest for a completed run."""
    config_payload = _normalise(config)
    result_payload = _normalise(result)
    trades_payload: list[object] = []
    if isinstance(result_payload, dict):
        raw_trades = result_payload.get("trades", [])
        if isinstance(raw_trades, list):
            trades_payload = raw_trades
        result_payload.pop("trades", None)
    trades_fingerprint = hashlib.sha256(canonical_json(trades_payload).encode("utf-8")).hexdigest()
    identity = {
        "semantics_version": semantics_version,
        "git_revision": revision,
        "config": config_payload,
        "data_fingerprint": data_fingerprint,
        "funding_fingerprint": funding_fingerprint,
        "seed": seed,
        "source_config": source_config,
    }
    run_id = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()[:16]
    return BacktestManifest(
        run_id=run_id,
        semantics_version=semantics_version,
        git_revision=revision,
        config=config_payload,
        result=result_payload,
        data_fingerprint=data_fingerprint,
        funding_fingerprint=funding_fingerprint,
        seed=seed,
        source_config=source_config,
        trades_fingerprint=trades_fingerprint,
    )


def write_manifest(output_dir: Path, manifest: BacktestManifest) -> Path:
    """Write one immutable JSON manifest, rejecting conflicting run evidence."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{manifest.run_id}.json"
    payload = canonical_json(manifest) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise FileExistsError(f"Refusing to overwrite conflicting backtest artifact: {path}")
        return path
    path.write_text(payload, encoding="utf-8")
    return path
