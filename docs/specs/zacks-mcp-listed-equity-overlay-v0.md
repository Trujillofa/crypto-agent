# Zacks MCP listed-equity overlay — PARKED capability note

**Status:** PARKED capability / data-availability note — **no research authorization.**
**Date:** 2026-08-24 (rewritten from 2026-08-22 lane-spec draft)
**Program:** [research-consolidation-2026-06-23.md](../reports/research-consolidation-2026-06-23.md)
(terminal; supersedes the 2026-06-19 consolidation). Public-data book **sealed**.

This file is **not** a research-lane specification, not a Gate-0 brief, and not a KEEP path.
It records that a generally licensed fundamentals/holdings MCP exists. It does **not** open a
lane, authorize a probe, or treat data acquisition as progress.

---

## Seal (do not read this file as a reopen)

The crypto research program and the public-data book remain **sealed**. Path 2 is closed at
Gate 0 on economics for the accessible operator profile. Access to a vendor dataset is not a
differentiated advantage.

Acquiring a dated / point-in-time extract does **not**:

- produce `DATA_PASS`;
- authorize a cheap probe, sweep, extract ingestion, or strategy overlay;
- reopen the sealed program;
- distinguish this feed from the closed mNAV / equity-fundamentals lane.

`DATA_PASS` is not a data-availability checkbox. No data-arrival event unblocks research.

---

## What this note records (capability only)

`~/.cursor/mcp.json` can expose official Zacks MCP (`https://mcp.zacksdata.com`). That feed is
North American **listed** fundamentals and **current** ETF holdings. This repo's live vehicles
are crypto perps (paper / disarmed). The MCP is a licensed public-market data surface, not a
non-public edge.

### 2026-08-22 schema facts (availability, not a thesis)

Source: Zacks Investment Research. Numeric values are not pinned here.

- Tools observed: `get_company_snapshot`, `get_income_statement`, `get_balance_sheet`,
  `get_cash_flow`, `get_etf_holdings`
- Annual statements: 5 years observed (AAPL `periods=40` → 2021–2025)
- Holdings: `symbol` + `top_n`; no as-of history parameter
- No `estimate_observed_ts` — this is not an earnings-surprise lane

These facts explain why **current** MCP output cannot be a historical backtest input. They do
not define a research question and they do not become one when a longer extract appears.

### User MCP snippet (no credentials)

Place in `~/.cursor/mcp.json`. Do not commit project `.mcp.json`, tokens, or live extracts.

```json
{
  "mcpServers": {
    "zacks": {
      "type": "http",
      "url": "https://mcp.zacksdata.com"
    }
  }
}
```

---

## This note must not

- start a research lane, Gate-0 brief, probe, sweep, or overlay contract;
- treat current MCP holdings as dated flow or as historical backtest inputs;
- treat a future licensed dated extract as `DATA_PASS` or as probe authorization;
- re-arm paper/live agents, or create any paper/live path;
- commit MCP tokens or live Zacks extracts;
- leak desk snapshots into later research inputs, configs, seeds, or backtests.

Desk-only current snapshots, if taken at all, are operator convenience with attribution.
They are **not** research inputs. A later extract must prove no desk snapshot entered a
config, seed, probe, or simulator.

---

## Reopening (all five required; any missing = still sealed)

Reopening is a **new program**, not a continuation of this note. It requires **all** of:

1. **Explicit human authorization** to reopen research (not implied by merging this note,
   connecting MCP, or obtaining data).
2. A **separately scoped new program**, with its own control-plane artifacts — not a revival
   of the sealed public-data book and not an addendum to this file.
3. A **specific named differentiated / non-public advantage**. Access to this generally
   licensed Zacks dataset, or to any other commonly licensed fundamentals/holdings feed, is
   **not** that advantage.
4. **Proof the thesis is distinct** from the closed mNAV / equity-fundamentals lane, including
   the prior **MSTR failure**. That lane
   ([mnav-premium-reversion-probe-v0.md](./mnav-premium-reversion-probe-v0.md);
   ledger: [autoresearch-candidate-ledger.md](../reports/autoresearch-candidate-ledger.md))
   closed **WEAK_EDGE**: only SBET passed in one 2025 window; MSTR, the most liquid
   longest-history name, failed (H=10 edge −1.44, p=0.630). A Zacks overlay on MSTR / COIN /
   MARA / IBIT / GBTC is the same listed-crypto-equity surface unless a written proof shows
   otherwise.
5. A **complete Gate-0 brief** **before** code, probe, sweep, or extract ingestion.

Gate 0/1 in [RBI_AUTORESEARCH_LOOP.md](../RBI_AUTORESEARCH_LOOP.md) **validate an already
authorized lane**. They do not authorize reopening.

### Future Gate-0 brief (if, and only if, 1–4 already hold)

The brief must preregister, in writing:

| Required field | Why it is mandatory |
|----------------|---------------------|
| Null model the probe must beat | A brief with no stated null is incomplete |
| Multiple-testing correction | Horizons / names / thresholds are a scan, not one test |
| Concentration limit | Single-name / single-window artifacts (SBET, one 2025 spike) |
| Expected regime and expected failure | Including the mNAV regime/bubble failure mode |
| Symbol and timeframe | No silent inheritance from live configs |
| Target trade density | Sparse overlays that never trade are not a pass |
| Independence vs live / paper agents | No correlated add-on to disarmed vehicles |
| `strategy.global_trend_filter_enabled` choice | Explicit `true` or `false`; do not inherit `base.yaml` |
| Validation plan | Commands, cost book, WFO/bootstrap, independence check |

No brief, no extract ingestion, no probe, no code.

---

## Non-goals (permanent unless a new program is explicitly authorized)

- New perp research family from this overlay
- Agent re-arm or paper/live path
- Ledger promotion/reject row (this note is neither)
- Autoresearch configs, strategy classes, or MCP credentials in-repo
