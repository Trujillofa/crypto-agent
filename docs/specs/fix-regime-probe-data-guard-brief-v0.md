# Builder brief — Probe must not emit NO_PULSE on missing/unreadable data

**Status:** QUEUED for builder (Grok). Created 2026-06-15 by reviewer (Claude).
**Type:** bug fix. **Branch:** `fix/regime-probe-data-guard`.
**Origin:** higher-tf-regime lane real-DB run (see `docs/reports/higher-tf-regime-probe-2026-06-14.md`).

## Problem

`scripts/probe_higher_tf_regime.py::main_async` wraps each
`IndicatorReader.fetch_multi_timeframe(...)` call in `try/except`, logs a WARNING,
and substitutes `[]` on failure. With every fetch failing (e.g. a missing
`perp_basis_metrics` / `funding_rates` table), all scenarios get `labeled=0` and the
probe still writes **`verdict: NO_PULSE`**. A pure *data gap* is then indistinguishable
from a real *no-edge* result — a false-closure risk. This actually masked two failed
runs during the regime lane before the data was fixed.

## Required behaviour

A verdict of `HAS_PULSE` / `WEAK_EDGE` / `NO_PULSE` may only be emitted when the probe
genuinely evaluated data. Specifically:

1. If **any** scenario fetch raises, do not swallow it into an empty result. Either
   abort the whole run, or mark that scenario as errored.
2. If the **total labeled bars across all scenarios is 0** (or any scenario fetch
   errored), write a verdict with status **`DATA_ERROR`** (new enum value), a `note`
   naming the failure, and **exit non-zero**. Never `NO_PULSE` in this case.
3. Keep the existing `--smoke` contract unchanged (still returns `NO_PULSE`, no DB).
4. The guard-consumable JSON shape stays the same plus the new status value.

## Scope / constraints (CLAUDE.md)

- Surgical: touch only `scripts/probe_higher_tf_regime.py` and
  `tests/test_probe_higher_tf_regime.py`. No changes to the reader or other probes.
- Type hints; `get_logger`; no `print` except the existing report block; no secrets.
- Add a unit test: simulate a fetch raising / zero labeled bars → asserts `DATA_ERROR`
  + non-zero exit + verdict JSON written. Keep the smoke test green.
- `uv run ruff check .` + `uv run ruff format --check .` + `uv run pytest -q` all green.
- Conventional commit, scope `backtest`; `Co-Authored-By: OpenCode <noreply@openai.com>`.

## Acceptance criteria

- Running the probe against a DB missing the joined tables exits non-zero with
  `DATA_ERROR`, not `NO_PULSE`.
- Smoke path unchanged. Tests + lint green. No new config knobs.
