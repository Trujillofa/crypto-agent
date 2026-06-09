# RBI Loop Completion Audit — 2026-06-09

## Objective

Design and implement a solid automated system loop for `crypto-trading-agent` that keeps
profitability as the original goal and supports RBI: Research, Backtest, Implement,
including autoresearch.

## Verdict

**Complete for supervised automation.**

The repo now has a documented and executable RBI control plane:

1. A runbook defining the profitability-first loop, gates, roles, and stop rules.
2. A deterministic guard that decides the next allowed action from artifacts.
3. A one-step runner that persists decisions and only executes with explicit `--execute`.
4. A report renderer that creates reviewable lane artifacts.
5. A manifest runner for repeatable lane control files.
6. A batch supervisor for multiple lane manifests.
7. A dry systemd scheduling path for daily supervision.

The implementation intentionally does **not** provide unattended production deployment or
unreviewed live-risk changes. Those remain human approval points by design.

## Requirement Audit

| Requirement | Evidence | Status |
|---|---|---|
| Keep profitability as the goal | `docs/RBI_AUTORESEARCH_LOOP.md` starts from portfolio truth, blocks weak lanes, and keeps count secondary to edge quality | Complete |
| Include Research | Gate 0 lane brief and Gate 1 cheap probe require falsifiable thesis and `HAS_PULSE` before sweeps | Complete |
| Include Backtest | Gate 2 uses bounded autoresearch with WFO/bootstrap; Gates 3-5 require promotion pre-filter, bootstrap=1000, and overlap | Complete |
| Include Implement | Implementation rules require minimal code/config, focused tests, exact validation rerun, and ledger update only after gates pass | Complete |
| Include autoresearch | Existing `scripts/autoresearch_loop.py` remains the search engine; new RBI scripts gate and orchestrate its use | Complete |
| Closed-loop artifacts | Runner writes `research/rbi_loop/<lane>/decision.json`; report writer writes `docs/reports/rbi-loop-<lane>.md`; batch writes summaries | Complete |
| Multi-lane supervision | `scripts/rbi_loop_batch.py` processes lane manifests and writes batch JSON/Markdown summaries | Complete |
| Repeatable lane config | `config/autoresearch/rbi_loop.example.yaml` defines the manifest shape for artifact paths and commands | Complete |
| Scheduling | `scripts/run_rbi_loop_batch.sh` plus `ops/systemd/crypto-agent-rbi-loop-batch.{service,timer}` provide daily dry supervision | Complete |
| Safety gates | Default mode is non-executing; `--execute` is explicit; scheduled systemd path does not include `--execute`; production deploy and live risk changes are banned from automation | Complete |
| Verification coverage | Focused tests cover guard, runner, report, manifest, batch, and scheduler invariants | Complete |

## Implemented Files

| File | Purpose |
|---|---|
| `docs/RBI_AUTORESEARCH_LOOP.md` | Primary runbook and operating model |
| `scripts/rbi_loop_guard.py` | Deterministic next-action gate |
| `scripts/rbi_loop_runner.py` | One-step decision/execution wrapper |
| `scripts/rbi_loop_report.py` | Decision JSON to Markdown report renderer |
| `scripts/rbi_loop_from_manifest.py` | One-step runner from lane manifest |
| `scripts/rbi_loop_batch.py` | Multi-lane batch supervisor |
| `scripts/run_rbi_loop_batch.sh` | Dry batch wrapper for ops/scheduling |
| `config/autoresearch/rbi_loop.example.yaml` | Lane manifest template |
| `ops/systemd/crypto-agent-rbi-loop-batch.service` | Dry scheduled supervisor service |
| `ops/systemd/crypto-agent-rbi-loop-batch.timer` | Daily scheduled supervisor timer |
| `tests/test_rbi_loop_*.py` | Focused verification suite |

## Verification Commands

Focused verification used:

```bash
bash -n scripts/run_rbi_loop_batch.sh
.venv/bin/python -m pytest tests/test_rbi_loop_guard.py tests/test_rbi_loop_runner.py tests/test_rbi_loop_report.py tests/test_rbi_loop_from_manifest.py tests/test_rbi_loop_batch.py tests/test_rbi_loop_scheduler.py -v
ruff check scripts/rbi_loop_guard.py scripts/rbi_loop_runner.py scripts/rbi_loop_report.py scripts/rbi_loop_from_manifest.py scripts/rbi_loop_batch.py tests/test_rbi_loop_guard.py tests/test_rbi_loop_runner.py tests/test_rbi_loop_report.py tests/test_rbi_loop_from_manifest.py tests/test_rbi_loop_batch.py tests/test_rbi_loop_scheduler.py
ruff format --check scripts/rbi_loop_guard.py scripts/rbi_loop_runner.py scripts/rbi_loop_report.py scripts/rbi_loop_from_manifest.py scripts/rbi_loop_batch.py tests/test_rbi_loop_guard.py tests/test_rbi_loop_runner.py tests/test_rbi_loop_report.py tests/test_rbi_loop_from_manifest.py tests/test_rbi_loop_batch.py tests/test_rbi_loop_scheduler.py
```

Smoke verification used temporary manifests under `/tmp` and produced:

- `/tmp/rbi-batch/wrapper-summary.json`
- `/tmp/rbi-batch/wrapper-summary.md`

## Boundaries

This completion is for a **supervised** automated loop. The following remain explicit
human approval points:

- `--execute` on any manifest or batch run
- live config changes
- production service rebuilds or restarts
- enabling new paper/live services
- changing risk limits or order sizes
- adding credentialed data providers

Those boundaries are part of the design, not missing implementation.
