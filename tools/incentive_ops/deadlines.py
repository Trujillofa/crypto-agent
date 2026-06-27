"""deadlines.py — windows/claims/review-expiry monitor (offline, registry-driven).

No network. Operates on parsed dates from ProgramRecord + DeadlineInputs.
"""

from __future__ import annotations

from datetime import date

from src.utils.logger import get_logger

from .registry import load_registry
from .types import DeadlineInputs, ProgramRecord

logger = get_logger(__name__)


def build_deadline_inputs(rec: ProgramRecord, within_days: int = 7) -> DeadlineInputs:
    """Best-effort from record (eligibility_window is often textual; review always present)."""
    # eligibility_window is textual in v0 fixture; keep None (future adapters populate)
    return DeadlineInputs(
        review_expiry=rec.review_expiry,
        within_days=within_days,
    )


def is_expired(d: date | None, today: date | None = None) -> bool:
    if d is None:
        return False
    today = today or date.today()
    return d < today


def days_until(d: date | None, today: date | None = None) -> int | None:
    if d is None:
        return None
    today = today or date.today()
    return (d - today).days


def alerts_for_record(rec: ProgramRecord, within_days: int = 7) -> list[dict]:
    """Return alert items for this record."""
    alerts: list[dict] = []
    today = date.today()
    di = build_deadline_inputs(rec, within_days)

    for label, d in [
        ("review_expiry", di.review_expiry),
        ("eligibility_close", di.eligibility_close),
        ("claim_date", di.claim_date),
        ("vesting_end", di.vesting_end),
    ]:
        if d is None:
            continue
        du = days_until(d, today)
        if du is None:
            continue
        if is_expired(d, today):
            alerts.append(
                {
                    "program_id": rec.id,
                    "kind": label,
                    "date": str(d),
                    "status": "EXPIRED",
                    "days": du,
                }
            )
        elif 0 <= du <= within_days:
            alerts.append(
                {
                    "program_id": rec.id,
                    "kind": label,
                    "date": str(d),
                    "status": "UPCOMING",
                    "days": du,
                }
            )
    return alerts


def compute_alerts(records: list[ProgramRecord] | None = None, within_days: int = 7) -> list[dict]:
    if records is None:
        records, _ = load_registry(warn=False)
    all_alerts: list[dict] = []
    for rec in records:
        all_alerts.extend(alerts_for_record(rec, within_days))
    return all_alerts


def main_deadlines(within_days: int = 7) -> list[dict]:
    return compute_alerts(within_days=within_days)
