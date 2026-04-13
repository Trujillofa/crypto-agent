# AVAX WFO sweep (2026-04-08) — reorder-only diff note

**Date observed**: 2026-04-13
**Files**: `avax-wfo-{bollinger,breakout_retest,mean_reversion,momentum_strategy}-20260408-153456.json`

## Finding

Working tree showed 4 modified WFO JSON reports (2272 line diff total). Verified via
`jq -S 'sort_by(.name)' | sha256sum` that all 4 files are **semantically identical** to
the committed versions at `d715a9e` — the diff is pure array reordering.

## Decision

Not committed — reorder-only diffs pollute git history with noise (2k lines, zero
information change). Not discarded — left in working tree for reference.

## Likely cause

WFO sweep output order is non-deterministic across re-runs. Candidates:
- Unordered `dict` iteration inside sweep aggregation
- Parallel worker result ordering (whichever finishes first)
- `glob`/filesystem listing order differences

## Action items (if this recurs)

- Decide whether `scripts/run_wfo.py` (or wherever sweep JSON is written) should
  `sort_by(name)` before dumping, to make re-runs produce byte-stable output.
- Until then: expect cosmetic diffs on any WFO re-run; verify via the sha256 check
  above before assuming results changed.
