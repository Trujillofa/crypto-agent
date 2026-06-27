# Builder Handoff — A1 Phase-0 Tooling (Grok)

**From:** Claude/Codex (planner/reviewer). **To:** Grok (builder).
**Parent spec:** [a1-incentive-farming-pilot-v0.md](./a1-incentive-farming-pilot-v0.md) (v0.2, narrowed).
**Test fixture (build against this):** `research/a1-incentive-farming/starter-registry-v0.yaml` (17 entries).
**Hard boundary:** Phase 0 is **read-only, no capital, no keys, no transaction signing, no auto-claim/
deploy.** This is **not** the trading engine — no `src/strategy` code, not in the RBI loop, not in the
agent runtime. Success = the tooling below passes its acceptance tests; **no on-chain action.**

---

## 0. Where it lives

New self-contained package **`tools/incentive_ops/`** (kept out of the trading runtime). May import
`src/utils/logger.py` for logging. Tests in `tests/` as `test_incentive_ops_*.py`. Standards: Python
3.11, type hints, `ruff` clean, `ruff format`, `structlog`/`get_logger` (no `print()`), line length 100.

```
tools/incentive_ops/
  __init__.py
  registry.py      # load + validate the YAML fixture -> typed records
  classify.py      # derive classification from rules; check vs recorded labels
  ev.py            # scenario EV calculator (pure, deterministic)
  deadlines.py     # windows / claims / review-expiry monitor (offline, registry-driven)
  eligibility.py   # read-only public eligibility/points lookups (address-only adapters)
  accounting.py    # capital-at-risk caps, gas/fee ledger, realized-PnL + hours report
  cli.py           # `python -m tools.incentive_ops <command>`
```

---

## 1. `registry.py` — loader + schema validator

- Load the YAML; map each entry to a frozen `@dataclass ProgramRecord`.
- **Validate, hard-fail with a clear message** on: missing required field (`id, name,
  official_source_url, observed_at, snapshot_sha256, distribution_mechanism, reward_type,
  classification, classification_reason, selection_criteria, review_expiry, verification_status`);
  enum value outside `field_dictionary`; unparseable date; duplicate `id`.
- **Warn (not fail)** on: `review_expiry` in the past (stale record); `snapshot_sha256 ==
  PENDING_TOOL_CAPTURE`; `live_round_status: UNVERIFIED`; `official_source_url: UNVERIFIED`.
- `selection_criteria` values are tri-state: `true | false | maybe | na` — preserve, don't coerce.

## 2. `classify.py` — the classification engine (THIS is the core test)

The fixture carries a recorded `classification` label per entry. Grok implements an **independent
rule-based classifier** and must **reproduce the recorded labels**; genuine disagreements are surfaced
for planner review, not silently overridden.

**Decision rules (planner-authoritative — evaluate top-to-bottom, first match wins):**

1. **REJECT** if any: `distribution_mechanism ∈ {lp_market_making, vote_incentive}`; OR
   `sybil_policy` indicates weak/multi-wallet incentive; OR `c2_terms_documented == false` (materially
   undisclosed); OR record notes leverage/recursive-points loops; OR contract exposure unbounded.
2. **DEFER** if `distribution_mechanism == proportional_points` (capital-proportional, risk-compensated
   points). Carry the test in the reason: *"hold on cash-yield+risk alone if points=0?"*
3. **YIELD_ONLY** if `distribution_mechanism ∈ {pro_rata_capital, capped_cashback}` (reward scales with
   capital/volume/spend; not a per-identity allocation).
4. **IN_CONDITIONAL** if `distribution_mechanism == labor_task` (input is labor, not capital).
5. **IN_CORE** if `distribution_mechanism == fixed_per_identity_cap` AND `c1_fixed_or_capped == true`
   AND `c2_terms_documented != false`.
6. Else **REJECT** (default-deny) with reason "unclassifiable / criteria incomplete".

Output per record: `derived_label`, `rule_fired`, `matches_recorded` (bool), and on mismatch a short diff.

## 3. `ev.py` — scenario EV calculator (implement the parent-spec formula exactly)

Pure functions, no I/O. Inputs are explicit ranges (base + upside scenarios).

```
Net_EV =
    P(eligibility) * P(distribution) * E[reward_qty]
      * conservative_realizable_price * liquidity_vesting_haircut      # speculative component
  + contractual_base_yield
  - gas_and_bridge_fees
  - opportunity_cost(capital * days, vs benchmark_apy)
  - expected_loss_reserve
  - manual_labor_cost(hours * hourly_rate)
```

- **Base case: if `reward_type ∈ {speculative_points}` and no token announced, the speculative
  component = 0.** Upside scenario carries the non-zero estimate separately. Never let a base-case go
  decision depend on unannounced points.
- Emit both ranking metrics: `net_ev_per_capital_day` and `net_per_manual_hour`.
- Deterministic; unit-tested with golden values.

## 4. `deadlines.py` — windows / claims / expiry (offline, registry-driven)

From the registry compute: upcoming eligibility windows, claim/vesting dates, and `review_expiry`
status; alert items within `--within-days` (default 7); flag expired records. No network needed.

## 5. `eligibility.py` — read-only public lookups (address-only)

- Interface: `fetch_eligibility(program_id, wallet_address) -> EligibilitySnapshot`. **Address only —
  the function signature must make a private key impossible to pass.**
- HTTP **GET only**, public endpoints/explorers; per-program adapters (pluggable; ship 1–2 examples,
  the rest stubbed). Cache responses + diff over time so accrual is visible.
- **Forbidden:** any write/sign/send, any wallet-connect, any key/seed parameter. If an adapter needs
  auth beyond a public read, it raises `NotSupportedReadOnly`.

## 6. `accounting.py` — caps + ledger + report

- Enforce pilot caps as assertions/reports (Phase 1 values, surfaced now): **total ≤ $1,000, ≤ $250/
  program, ≤ 3 concurrent programs.** Reading a manual-entry ledger (CSV/JSON the human fills) — the tool
  never moves funds.
- Gas/fee ledger; realized-PnL + hours-worked report; `net_per_manual_hour` actuals vs EV estimates.

## 7. `cli.py`

`validate | classify [--check] | ev [--scenario base|upside] | deadlines [--within-days N] |
eligibility --address 0x.. | report`. Structured logging; non-zero exit on validation failure.

---

## Acceptance criteria (planner review gate)

1. `validate` passes on the starter registry (with the expected PENDING/UNVERIFIED **warnings**).
2. `classify --check` **reproduces all 17 recorded labels** (5 IN_CORE / 4 IN_CONDITIONAL / 3 YIELD_ONLY
   / 2 DEFER / 3 REJECT); the synthetic `undisclosed-sybil-points-farm-archetype` must auto-REJECT.
3. `ev` unit-tested with deterministic golden values; base case forces unannounced-points → 0.
4. **No network calls** in `validate/classify/ev/deadlines/accounting`. `eligibility` is the only
   networked module and is **read-only GET, address-only** (a key cannot be passed by construction).
5. `ruff check` + `ruff format --check` clean; type hints throughout; `get_logger`, no `print()`.
6. **Absent by construction:** any tx signing, key/seed handling, auto-claim/auto-deploy, or a
   "restaking optimizer." If present → reject the PR.

## Out of scope (do NOT build in Phase 0)

Auto-deploy/auto-claim, transaction construction/signing, wallet-connect, a restaking/yield optimizer,
any capital movement, any `src/strategy` or RBI-loop integration.

## Roles & flow

1. Planner froze the fixture + these rules. 2. Grok builds `tools/incentive_ops/` + tests. 3. Planner
reviews code against acceptance criteria + the classification reproduction. 4. Human (only) decides any
Phase-1 capital later, gated. The tool informs; the human acts.
