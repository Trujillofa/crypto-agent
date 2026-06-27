"""accounting.py — runtime cap validation (blocks), gas/fee ledger, PnL/hours report.

validate_caps is called by actionability and any future commit path; it returns failing CapCheck or raises.
Caps: total <=1000, per-program <=250, concurrent <=3 .
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger

from .types import CapCheck, PilotCaps

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
    cand_usd = float(candidate.get("usd", candidate.get("capital_usd", 100.0)))

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
