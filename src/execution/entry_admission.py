"""Entry-only durable admission and independently refreshed observations."""

from src.execution.metrics import ExecutionMetrics
from src.risk.entry_control import EntryControl


async def refresh(control: EntryControl, metrics: ExecutionMetrics):
    state = await control.state(force=True)
    metrics.record_entry_control(
        armed=state.armed,
        reduce_only=state.reduce_only,
        read_age_s=control.last_success_age_s,
        fault=control.last_fault is not None,
    )
    return state


async def admit(control: EntryControl, metrics: ExecutionMetrics, symbol: str) -> None:
    state = await refresh(control, metrics)
    if not state.entries_allowed:
        metrics.record_risk_block(symbol, f"entry_control: {state.reason}")
        raise RuntimeError(f"Entry blocked by control: {state.reason}")
