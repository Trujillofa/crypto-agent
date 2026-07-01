"""capture.py — source capture (read-only GET, raw bytes snapshot).

- Allowlist + refuse UNVERIFIED
- Fetch raw bytes (no JS, no auth)
- sha256 over raw bytes
- Write snapshot raw + sidecar yaml (captures/<id>.yaml)
- Never mutates starter-registry-v0.yaml
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import yaml

from src.utils.logger import get_logger

from .allowlist import is_allowed_url
from .http import allowlisted_get
from .registry import load_registry
from .types import (
    CaptureError,
    CaptureRecord,
    EVInputsRecord,
    EVReadiness,
    EVScenarioInputs,
    JurisdictionStatus,
    ProgramRecord,
    ReviewerDecision,
    RewardType,
    VerificationRecord,
)


def _parse_optional_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return date.fromisoformat(str(val).split("T")[0])
    except Exception:
        return None


logger = get_logger(__name__)

SNAPSHOTS_ROOT = Path("research/a1-incentive-farming/snapshots")
CAPTURES_ROOT = Path("research/a1-incentive-farming/captures")
VERIFICATIONS_ROOT = Path("research/a1-incentive-farming/verifications")
EV_INPUTS_ROOT = Path("research/a1-incentive-farming/ev_inputs")


def _ensure_dirs() -> None:
    SNAPSHOTS_ROOT.mkdir(parents=True, exist_ok=True)
    CAPTURES_ROOT.mkdir(parents=True, exist_ok=True)


def _is_unverified(url: str) -> bool:
    u = url.upper()
    return "UNVERIFIED" in u or url.strip() == ""


def fetch_raw(url: str, timeout: float = 30.0) -> bytes:
    if _is_unverified(url):
        raise CaptureError(f"Refusing UNVERIFIED source: {url}")
    # allowlisted_get enforces the allowlist (EndpointNotAllowed, propagated) and
    # never follows redirects; it is the single sanctioned GET path.
    try:
        resp = allowlisted_get(url, timeout=timeout)
    except httpx.HTTPError as e:
        raise CaptureError(f"GET failed for {url}: {e}") from e
    # Only a 200 with a non-empty body is a real snapshot. A 202/204/redirect-with-no-body
    # would otherwise be hashed as the empty-string digest and recorded as a valid capture.
    if resp.status_code != 200:
        raise CaptureError(f"non-200 ({resp.status_code}) for {url}; not a usable snapshot")
    if not resp.content:
        raise CaptureError(f"empty body for {url}; not a usable snapshot")
    return resp.content


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_raw_evidence(cap: CaptureRecord) -> bytes:
    """Durable evidence: load the retained raw bytes and confirm sha. Hash alone is insufficient."""
    p = Path(cap.raw_path)
    if not p.exists():
        # try relative to project root
        p = Path.cwd() / cap.raw_path
    if not p.exists():
        raise CaptureError(f"raw evidence missing for {cap.id}: {cap.raw_path}")
    raw = p.read_bytes()
    if _compute_sha256(raw) != cap.snapshot_sha256:
        raise CaptureError(f"raw sha mismatch for {cap.id} (tamper or loss of bytes)")
    return raw


def _write_raw_snapshot(pid: str, captured_at: datetime, raw: bytes) -> Path:
    _ensure_dirs()
    ts = captured_at.strftime("%Y%m%dT%H%M%SZ")
    sha_short = hashlib.sha256(raw).hexdigest()[:16]
    pid_dir = SNAPSHOTS_ROOT / pid
    pid_dir.mkdir(parents=True, exist_ok=True)
    out = pid_dir / f"{ts}_{sha_short}.raw"
    if out.exists():
        return out
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


def _ensure_verif_dirs() -> None:
    VERIFICATIONS_ROOT.mkdir(parents=True, exist_ok=True)
    EV_INPUTS_ROOT.mkdir(parents=True, exist_ok=True)


def write_verification_sidecar(v: VerificationRecord) -> Path:
    """Write typed verification sidecar. reviewer_decision PENDING by design for Day-0."""
    _ensure_verif_dirs()
    out = VERIFICATIONS_ROOT / f"{v.id}.yaml"
    payload = {
        "id": v.id,
        "verified_at": v.verified_at.isoformat() if v.verified_at else None,
        "snapshot_sha256": v.snapshot_sha256,
        "terms_match_snapshot": v.terms_match_snapshot,
        "live_round_open": v.live_round_open,
        "reviewer_decision": str(v.reviewer_decision),
        "raw_evidence_path": v.raw_evidence_path,
        "official_round_terms_url": v.official_round_terms_url,
        "captured_source_url": v.captured_source_url,
        "jurisdiction_status": v.jurisdiction_status.value
        if hasattr(v.jurisdiction_status, "value")
        else str(v.jurisdiction_status),
        "eligibility_open": v.eligibility_open.isoformat() if v.eligibility_open else None,
        "eligibility_close": v.eligibility_close.isoformat() if v.eligibility_close else None,
        "claim_date": v.claim_date.isoformat() if v.claim_date else None,
        "vesting_end": v.vesting_end.isoformat() if v.vesting_end else None,
        "notes": v.notes,
    }
    out.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    logger.info("wrote verification sidecar %s decision=%s", v.id, v.reviewer_decision)
    return out


def load_verifications(
    verif_dir: str = "research/a1-incentive-farming/verifications",
) -> dict[str, VerificationRecord]:
    """Load verification sidecars. Missing or PENDING => not verified (non-actionable)."""
    from pathlib import Path

    import yaml

    pdir = Path(verif_dir)
    out: dict[str, VerificationRecord] = {}
    if not pdir.exists():
        return out
    for yf in pdir.glob("*.yaml"):
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
            if not data or "id" not in data:
                continue
            dec = ReviewerDecision(str(data.get("reviewer_decision", "PENDING")).upper())
            va = data.get("verified_at")
            verified_at = None
            if va:
                try:
                    verified_at = datetime.fromisoformat(str(va).replace("Z", "+00:00"))
                except Exception:
                    verified_at = None
            jstr = data.get("jurisdiction_status")
            try:
                js = JurisdictionStatus(str(jstr).upper()) if jstr else JurisdictionStatus.UNKNOWN
            except Exception:
                js = JurisdictionStatus.UNKNOWN
            out[str(data["id"])] = VerificationRecord(
                id=str(data["id"]),
                verified_at=verified_at,
                snapshot_sha256=str(data["snapshot_sha256"]),
                terms_match_snapshot=bool(data.get("terms_match_snapshot", False)),
                live_round_open=bool(data.get("live_round_open", False)),
                reviewer_decision=dec,
                raw_evidence_path=data.get("raw_evidence_path"),
                official_round_terms_url=data.get("official_round_terms_url"),
                captured_source_url=data.get("captured_source_url"),
                jurisdiction_status=js,
                eligibility_open=_parse_optional_date(data.get("eligibility_open")),
                eligibility_close=_parse_optional_date(data.get("eligibility_close")),
                claim_date=_parse_optional_date(data.get("claim_date")),
                vesting_end=_parse_optional_date(data.get("vesting_end")),
                notes=data.get("notes"),
            )
        except Exception:
            logger.warning("bad verification sidecar %s", yf)
    return out


def write_ev_inputs_sidecar(rec: EVInputsRecord) -> Path:
    _ensure_verif_dirs()
    out = EV_INPUTS_ROOT / f"{rec.id}.yaml"
    # serialize inputs fields + reward_type
    inp = rec.inputs
    payload = {
        "id": rec.id,
        "p_eligibility": inp.p_eligibility,
        "p_distribution": inp.p_distribution,
        "reward_qty": inp.reward_qty,
        "realizable_price": inp.realizable_price,
        "liquidity_vesting_haircut": inp.liquidity_vesting_haircut,
        "base_yield": inp.base_yield,
        "gas_bridge_fees": inp.gas_bridge_fees,
        "capital": inp.capital,
        "days": inp.days,
        "benchmark_apy": inp.benchmark_apy,
        "expected_loss_reserve": inp.expected_loss_reserve,
        "manual_hours": inp.manual_hours,
        "hourly_rate": inp.hourly_rate,
        "reward_announced": inp.reward_announced,
        "reward_type": str(rec.reward_type),
        "readiness": rec.readiness.value if hasattr(rec.readiness, "value") else str(rec.readiness),
        "provenance": getattr(rec, "provenance", {}),
        "notes": rec.notes,
    }
    out.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    logger.info("wrote ev_inputs sidecar %s", rec.id)
    return out


def load_ev_inputs(
    ev_dir: str = "research/a1-incentive-farming/ev_inputs",
) -> dict[str, EVInputsRecord]:
    from pathlib import Path

    import yaml

    pdir = Path(ev_dir)
    out: dict[str, EVInputsRecord] = {}
    if not pdir.exists():
        return out
    for yf in pdir.glob("*.yaml"):
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
            if not data or "id" not in data:
                continue
            rt = RewardType(str(data.get("reward_type", "speculative_points")))
            inp = EVScenarioInputs(
                p_eligibility=float(data["p_eligibility"]),
                p_distribution=float(data["p_distribution"]),
                reward_qty=float(data["reward_qty"]),
                realizable_price=float(data["realizable_price"]),
                liquidity_vesting_haircut=float(data["liquidity_vesting_haircut"]),
                base_yield=float(data["base_yield"]),
                gas_bridge_fees=float(data["gas_bridge_fees"]),
                capital=float(data["capital"]),
                days=float(data["days"]),
                benchmark_apy=float(data["benchmark_apy"]),
                expected_loss_reserve=float(data["expected_loss_reserve"]),
                manual_hours=float(data["manual_hours"]),
                hourly_rate=float(data["hourly_rate"]),
                reward_announced=bool(data["reward_announced"]),
            )
            rstr = data.get("readiness", "UNREADY")
            try:
                rd = EVReadiness(str(rstr).upper())
            except Exception:
                rd = EVReadiness.UNREADY
            out[str(data["id"])] = EVInputsRecord(
                id=str(data["id"]),
                inputs=inp,
                reward_type=rt,
                readiness=rd,
                provenance=dict(data.get("provenance", {})),
                notes=data.get("notes"),
            )
        except Exception:
            logger.warning("bad ev_inputs sidecar %s", yf)
    return out
