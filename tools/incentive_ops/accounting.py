"""accounting.py — runtime cap validation (blocks), gas/fee ledger, PnL/hours report.

validate_caps is called by actionability and any future commit path; it returns failing CapCheck or raises.
Caps: total <=1000, per-program <=250, concurrent <=3 .

Real validated ledger parsing added for operational Day-0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.utils.logger import get_logger

from .types import CapCheck, PilotCaps, ValidationError

logger = get_logger(__name__)


def validate_caps(
    ledger: list[dict[str, Any]],
    candidate: dict[str, Any],
    caps: PilotCaps | None = None,
) -> CapCheck:
    """Return CapCheck. Does NOT mutate. Raises CapsExceeded only on hard misuse (use .ok)."""
    caps = caps or PilotCaps()
    committed_total = 0.0
    per_program: dict[str, float] = {}
    current_programs: set[str] = set()

    for entry in ledger or []:
        pid = str(entry.get("id", entry.get("program_id", "")))
        usd = float(entry.get("usd", entry.get("capital_usd", 0.0)))
        committed_total += usd
        per_program[pid] = per_program.get(pid, 0.0) + usd
        if pid:
            current_programs.add(pid)

    cand_id = str(candidate.get("id", candidate.get("program_id", "unknown")))
    # No hardcoded 100 placeholder: caller must supply real proposed capital for the check.
    cand_usd = float(candidate.get("usd", candidate.get("capital_usd", 0.0)))
    if "usd" not in candidate and "capital_usd" not in candidate:
        logger.info("validate_caps: candidate usd not supplied, using 0.0 for Day-0 sim")

    total_after = committed_total + cand_usd
    per_after = per_program.get(cand_id, 0.0) + cand_usd
    conc_after = len(current_programs) + (0 if cand_id in current_programs else 1)

    if total_after > caps.total_usd + 1e-6:
        reason = f"total would be {total_after:.2f} > {caps.total_usd}"
        logger.warning("caps block: %s", reason)
        return CapCheck(
            ok=False,
            total_after=total_after,
            per_program_after=per_after,
            concurrent_after=conc_after,
            reason=reason,
        )
    if per_after > caps.per_program_usd + 1e-6:
        reason = f"per-program {cand_id} would be {per_after:.2f} > {caps.per_program_usd}"
        logger.warning("caps block: %s", reason)
        return CapCheck(
            ok=False,
            total_after=total_after,
            per_program_after=per_after,
            concurrent_after=conc_after,
            reason=reason,
        )
    if conc_after > caps.max_concurrent:
        reason = f"concurrent would be {conc_after} > {caps.max_concurrent}"
        logger.warning("caps block: %s", reason)
        return CapCheck(
            ok=False,
            total_after=total_after,
            per_program_after=per_after,
            concurrent_after=conc_after,
            reason=reason,
        )

    return CapCheck(
        ok=True, total_after=total_after, per_program_after=per_after, concurrent_after=conc_after
    )


def add_to_ledger(ledger: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure; return new list (human fills ledger file)."""
    new = list(ledger)
    new.append(dict(entry))
    return new


def realized_report(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    """Simple realized summary from manual ledger entries."""
    total_usd = sum(float(e.get("usd", 0)) for e in ledger)
    total_gas = sum(float(e.get("gas_usd", 0)) for e in ledger)
    total_realized = sum(float(e.get("realized_usd", 0)) for e in ledger)
    hours = sum(float(e.get("hours", 0)) for e in ledger)
    net = total_realized - total_gas
    per_hour = net / max(hours, 1e-9) if hours else 0.0
    return {
        "committed_total_usd": total_usd,
        "gas_total_usd": total_gas,
        "realized_total_usd": total_realized,
        "net_usd": net,
        "hours": hours,
        "net_per_manual_hour": per_hour,
        "entries": len(ledger),
    }


def load_ledger(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Real validated ledger parser (YAML or JSON). No auto-capital; human operated.

    Expects list of entries with at minimum 'id' and numeric 'usd' (or 'capital_usd').
    Raises ValidationError on bad schema. Used by actionability/report for Day-0.
    """
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        if p.suffix.lower() in (".yaml", ".yml"):
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
        else:
            import json

            data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValidationError(f"failed to parse ledger {p}: {e}") from e

    if not isinstance(data, list):
        raise ValidationError("ledger must be a list of entries")

    validated: list[dict[str, Any]] = []
    for i, e in enumerate(data):
        if not isinstance(e, dict):
            raise ValidationError(f"ledger entry {i} not a dict")
        pid = str(e.get("id") or e.get("program_id") or "").strip()
        if not pid:
            raise ValidationError(f"ledger entry {i} missing id/program_id")
        usd = e.get("usd", e.get("capital_usd"))
        if usd is None:
            raise ValidationError(f"ledger entry {i} ({pid}) missing usd/capital_usd")
        try:
            usd_f = float(usd)
        except Exception:
            raise ValidationError(f"ledger entry {i} ({pid}) usd not numeric") from None
        if usd_f < 0:
            raise ValidationError(f"ledger entry {i} ({pid}) usd < 0")
        entry = {
            "id": pid,
            "usd": usd_f,
            "gas_usd": float(e.get("gas_usd", e.get("gas", 0.0))),
            "realized_usd": float(e.get("realized_usd", 0.0)),
            "hours": float(e.get("hours", 0.0)),
            "date": str(e.get("date", "")),
        }
        validated.append(entry)
    logger.info("loaded validated ledger entries=%d from %s", len(validated), p)
    return validated
