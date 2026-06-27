"""capture.py — source capture (read-only GET, raw bytes snapshot).

- Allowlist + refuse UNVERIFIED
- Fetch raw bytes (no JS, no auth)
- sha256 over raw bytes
- Write snapshot raw + sidecar yaml (captures/<id>.yaml)
- Never mutates starter-registry-v0.yaml
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from src.utils.logger import get_logger

from .allowlist import assert_allowed, is_allowed_url
from .registry import load_registry
from .types import CaptureError, CaptureRecord, ProgramRecord

logger = get_logger(__name__)

SNAPSHOTS_ROOT = Path("research/a1-incentive-farming/snapshots")
CAPTURES_ROOT = Path("research/a1-incentive-farming/captures")


def _ensure_dirs() -> None:
    SNAPSHOTS_ROOT.mkdir(parents=True, exist_ok=True)
    CAPTURES_ROOT.mkdir(parents=True, exist_ok=True)


def _is_unverified(url: str) -> bool:
    u = url.upper()
    return "UNVERIFIED" in u or url.strip() == ""


def fetch_raw(url: str, timeout: float = 30.0) -> bytes:
    assert_allowed(url)
    if _is_unverified(url):
        raise CaptureError(f"Refusing UNVERIFIED source: {url}")
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError as e:
        raise CaptureError(f"GET failed for {url}: {e}") from e


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_raw_snapshot(pid: str, captured_at: datetime, raw: bytes) -> Path:
    _ensure_dirs()
    ts = captured_at.strftime("%Y%m%dT%H%M%SZ")
    pid_dir = SNAPSHOTS_ROOT / pid
    pid_dir.mkdir(parents=True, exist_ok=True)
    out = pid_dir / f"{ts}.raw"
    out.write_bytes(raw)
    return out


def _write_sidecar(pid: str, cap: CaptureRecord) -> Path:
    _ensure_dirs()
    out = CAPTURES_ROOT / f"{pid}.yaml"
    payload = {
        "id": cap.id,
        "snapshot_sha256": cap.snapshot_sha256,
        "captured_at": cap.captured_at.isoformat(),
        "raw_path": cap.raw_path,
        "source_url": cap.source_url,
    }
    out.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return out


def capture_program(rec: ProgramRecord, *, force: bool = False) -> CaptureRecord:
    """Capture one record. Returns the sidecar record. Idempotent if not force."""
    if _is_unverified(rec.official_source_url):
        raise CaptureError(f"{rec.id}: official_source_url is UNVERIFIED")
    if not is_allowed_url(rec.official_source_url):
        raise CaptureError(f"{rec.id}: source not allowlisted: {rec.official_source_url}")

    # Check existing sidecar
    side = CAPTURES_ROOT / f"{rec.id}.yaml"
    if side.exists() and not force:
        data = yaml.safe_load(side.read_text(encoding="utf-8")) or {}
        return CaptureRecord(
            id=rec.id,
            snapshot_sha256=data["snapshot_sha256"],
            captured_at=datetime.fromisoformat(str(data["captured_at"])),
            raw_path=data.get("raw_path", ""),
            source_url=data.get("source_url", rec.official_source_url),
        )

    raw = fetch_raw(rec.official_source_url)
    sha = _compute_sha256(raw)
    now = datetime.now(UTC)
    raw_path = _write_raw_snapshot(rec.id, now, raw)
    try:
        rel = str(raw_path.relative_to(Path(".").resolve()))
    except ValueError:
        rel = str(raw_path)
    cap = CaptureRecord(
        id=rec.id,
        snapshot_sha256=sha,
        captured_at=now,
        raw_path=rel,
        source_url=rec.official_source_url,
    )
    _write_sidecar(rec.id, cap)
    logger.info("captured %s sha=%s", rec.id, sha[:16])
    return cap


def capture_all(
    records: list[ProgramRecord] | None = None, *, force: bool = False, only_id: str | None = None
) -> list[CaptureRecord]:
    if records is None:
        records, _ = load_registry(warn=False)
    out: list[CaptureRecord] = []
    for rec in records:
        if only_id and rec.id != only_id:
            continue
        try:
            out.append(capture_program(rec, force=force))
        except CaptureError as e:
            logger.warning("capture skip %s: %s", rec.id, e)
    return out


def load_captures(
    captures_dir: str = "research/a1-incentive-farming/captures",
) -> dict[str, CaptureRecord]:
    """Load sidecar yamls. Shared helper."""
    from pathlib import Path

    import yaml

    pdir = Path(captures_dir)
    caps: dict[str, CaptureRecord] = {}
    if not pdir.exists():
        return caps
    for yf in pdir.glob("*.yaml"):
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8"))
            if not data or "id" not in data:
                continue
            caps[str(data["id"])] = CaptureRecord(
                id=str(data["id"]),
                snapshot_sha256=str(data["snapshot_sha256"]),
                captured_at=datetime.fromisoformat(str(data["captured_at"]).replace("Z", "+00:00")),
                raw_path=data.get("raw_path", ""),
                source_url=data.get("source_url", ""),
            )
        except Exception:
            logger.warning("bad capture sidecar %s", yf)
    return caps
