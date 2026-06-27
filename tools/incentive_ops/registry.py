"""registry.py — load + validate the YAML fixture into typed ProgramRecord list.

Hard-fail on missing fields, bad enums, duplicate ids, unparseable dates.
Warn (collect + logger) on PENDING, UNVERIFIED, past review_expiry.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.utils.logger import get_logger

from .types import (
    Classification,
    Criterion,
    Mechanism,
    PilotCaps,
    ProgramRecord,
    RewardType,
    SelectionCriteria,
    ValidationError,
)

logger = get_logger(__name__)

REQUIRED_FIELDS = {
    "id",
    "name",
    "official_source_url",
    "observed_at",
    "snapshot_sha256",
    "distribution_mechanism",
    "reward_type",
    "classification",
    "classification_reason",
    "selection_criteria",
    "review_expiry",
    "verification_status",
}

# Note: some yaml entries omit secondary_url, live_round_status etc.; defaults applied.
# live_round_status and verification are required per spec v0.1 list.


def _load_yaml(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValidationError("Registry root must be a mapping")
    return data


def _parse_date(s: str, field: str, prog_id: str) -> date:
    try:
        return date.fromisoformat(s.strip())
    except Exception:
        raise ValidationError(f"Bad date in {field} for {prog_id}: {s!r}") from None


def _parse_criterion(v: Any, field: str, prog_id: str) -> Criterion:
    if isinstance(v, bool):
        return Criterion.TRUE if v else Criterion.FALSE
    if isinstance(v, str):
        vv = v.strip().lower()
        if vv in ("true", "false", "maybe", "na"):
            return Criterion(vv)
    raise ValidationError(f"Bad tri-state for {field} in {prog_id}: {v!r}")


def _parse_selection_criteria(d: dict[str, Any], prog_id: str) -> SelectionCriteria:
    if not isinstance(d, dict):
        raise ValidationError(f"selection_criteria must be mapping for {prog_id}")
    try:
        return SelectionCriteria(
            c1_fixed_or_capped=_parse_criterion(
                d.get("c1_fixed_or_capped"), "c1_fixed_or_capped", prog_id
            ),
            c2_terms_documented=_parse_criterion(
                d.get("c2_terms_documented"), "c2_terms_documented", prog_id
            ),
            c3_eligibility_public=_parse_criterion(
                d.get("c3_eligibility_public"), "c3_eligibility_public", prog_id
            ),
            c4_capital_bounded=_parse_criterion(
                d.get("c4_capital_bounded"), "c4_capital_bounded", prog_id
            ),
            c5_tail_named=_parse_criterion(d.get("c5_tail_named"), "c5_tail_named", prog_id),
            c6_reward_rationale=_parse_criterion(
                d.get("c6_reward_rationale"), "c6_reward_rationale", prog_id
            ),
        )
    except KeyError as e:
        raise ValidationError(f"Missing selection criterion key for {prog_id}: {e}") from e


def _parse_bool_or_maybe(v: Any) -> bool | str:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        vv = v.strip().lower()
        if vv == "maybe":
            return "maybe"
    # default loose to bool if present
    if v is None:
        return False
    return bool(v)


def _validate_enums(
    mech: str, rew: str, cls: str, prog_id: str
) -> tuple[Mechanism, RewardType, Classification]:
    try:
        m = Mechanism(mech)
    except ValueError:
        raise ValidationError(f"Unknown distribution_mechanism for {prog_id}: {mech}") from None
    try:
        r = RewardType(rew)
    except ValueError:
        raise ValidationError(f"Unknown reward_type for {prog_id}: {rew}") from None
    try:
        c = Classification(cls)
    except ValueError:
        raise ValidationError(f"Unknown classification for {prog_id}: {cls}") from None
    return m, r, c


def load_registry(
    path: Path | str = "research/a1-incentive-farming/starter-registry-v0.yaml",
    *,
    warn: bool = True,
) -> tuple[list[ProgramRecord], list[str]]:
    """Load and validate. Returns (records, warnings). Warnings are surfaced but do not fail load."""
    data = _load_yaml(path)
    schema_version = data.get("schema_version")
    if schema_version is not None and schema_version != 0:
        logger.warning("Unexpected schema_version: %s", schema_version)

    programs = data.get("programs")
    if not isinstance(programs, list):
        raise ValidationError("programs must be a list")

    records: list[ProgramRecord] = []
    seen_ids: set[str] = set()
    warns: list[str] = []

    for idx, raw in enumerate(programs):
        if not isinstance(raw, dict):
            raise ValidationError(f"Program entry {idx} must be mapping")
        missing = REQUIRED_FIELDS - raw.keys()
        if missing:
            raise ValidationError(
                f"Program {raw.get('id', idx)} missing required fields: {sorted(missing)}"
            )

        pid = str(raw["id"]).strip()
        if not pid:
            raise ValidationError(f"Empty id at index {idx}")
        if pid in seen_ids:
            raise ValidationError(f"Duplicate id: {pid}")
        seen_ids.add(pid)

        observed = _parse_date(str(raw["observed_at"]), "observed_at", pid)
        review = _parse_date(str(raw["review_expiry"]), "review_expiry", pid)

        mech, rew, cls = _validate_enums(
            str(raw["distribution_mechanism"]),
            str(raw["reward_type"]),
            str(raw["classification"]),
            pid,
        )

        sel = _parse_selection_criteria(raw["selection_criteria"], pid)

        rec = ProgramRecord(
            id=pid,
            name=str(raw["name"]).strip(),
            official_source_url=str(raw["official_source_url"]).strip(),
            secondary_url=str(raw.get("secondary_url", "")).strip() or None,
            observed_at=observed,
            snapshot_sha256=str(raw["snapshot_sha256"]).strip(),
            distribution_mechanism=mech,
            reward_type=rew,
            capital_required=str(raw.get("capital_required", "")).strip(),
            lockup_vesting=str(raw.get("lockup_vesting", "")).strip(),
            eligibility_window=str(raw.get("eligibility_window", "")).strip(),
            kyc_required=_parse_bool_or_maybe(raw.get("kyc_required")),
            jurisdiction_restrictions=_parse_bool_or_maybe(raw.get("jurisdiction_restrictions")),
            sybil_policy=str(raw.get("sybil_policy", "")).strip(),
            chains_contracts=str(raw.get("chains_contracts", "")).strip(),
            exit_liquidity=str(raw.get("exit_liquidity", "")).strip(),
            tail_risks=[str(x).strip() for x in raw.get("tail_risks", []) if str(x).strip()],
            classification=cls,
            classification_reason=str(raw.get("classification_reason", "")).strip(),
            selection_criteria=sel,
            verification_status=str(raw.get("verification_status", "")).strip(),
            live_round_status=str(raw.get("live_round_status", "")).strip() or "UNVERIFIED",
            review_expiry=review,
            notes=str(raw.get("notes", "")).strip() or None,
        )

        # Warnings per spec
        if review < date.today():
            w = f"{pid}: review_expiry in the past ({review})"
            warns.append(w)
            if warn:
                logger.warning(w)
        if rec.snapshot_sha256 == "PENDING_TOOL_CAPTURE":
            w = f"{pid}: snapshot_sha256 is PENDING_TOOL_CAPTURE"
            warns.append(w)
            if warn:
                logger.warning(w)
        if rec.live_round_status in ("UNVERIFIED", "NOT_LIVE_REFERENCE_ONLY", "NOT_A_REAL_PROGRAM"):
            w = f"{pid}: live_round_status={rec.live_round_status}"
            warns.append(w)
            if warn:
                logger.warning(w)
        if (
            rec.official_source_url == "UNVERIFIED"
            or "UNVERIFIED" in rec.official_source_url.upper()
        ):
            w = f"{pid}: official_source_url UNVERIFIED"
            warns.append(w)
            if warn:
                logger.warning(w)
        if (
            "SYNTHETIC" in rec.verification_status.upper()
            or "ARCHETYPE" in rec.verification_status.upper()
        ):
            # informational only; not a hard warn
            pass

        records.append(rec)

    return records, warns


def get_default_caps() -> PilotCaps:
    return PilotCaps()


# For CLI quick validate entry
def validate_registry(
    path: Path | str = "research/a1-incentive-farming/starter-registry-v0.yaml",
) -> list[str]:
    _, warns = load_registry(path, warn=False)
    return warns
