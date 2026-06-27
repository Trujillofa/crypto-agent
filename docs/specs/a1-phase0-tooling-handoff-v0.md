# Builder Handoff — A1 Phase-0 Tooling (Grok)

**Version:** v0.1 (amended 2026-06-27) — adds source capture, classification/actionability separation,
typed criteria + EV/deadline inputs, validated address handling, endpoint allowlisting, and runtime cap
validation. Otherwise unchanged from v0.

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
  types.py         # typed value objects + enums (criteria, EV inputs, deadline inputs, Address, caps)
  registry.py      # load + validate the YAML fixture -> typed records
  capture.py       # fetch official source, store raw snapshot, compute snapshot_sha256, set captured_at
  classify.py      # derive CLASSIFICATION (tier) from rules; check vs recorded labels
  actionability.py # SEPARATE gate: is this record actionable right now? (capture + verify + promotion + caps)
  ev.py            # scenario EV calculator (pure, deterministic) — typed inputs
  deadlines.py     # windows / claims / review-expiry monitor (offline, registry-driven) — typed inputs
  eligibility.py   # read-only public lookups — validated Address + endpoint allowlist
  accounting.py    # runtime cap validation, gas/fee ledger, realized-PnL + hours report
  cli.py           # `python -m tools.incentive_ops <command>`
config/incentive_ops/endpoint_allowlist.yaml   # approved public read-only hosts/paths (see §5)
```

### Binding invariant (do not weaken)

**All 17 current fixture records are NON-ACTIONABLE.** A record becomes actionable *only* after (a)
`capture.py` has frozen the current official terms (real `snapshot_sha256`, `captured_at`), (b) live terms
are verified against that snapshot, (c) `classify` puts it in an actionable tier, **and** (d) the
promotion gate + runtime caps pass (§2.5). Until then every record is informational only — no capital,
no claim, no deploy. `classification` (what *kind* it is) is decided independently of `actionability`
(whether it may be acted on now).

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

## 1.5 `types.py` — typed value objects (no loose dicts across module boundaries)

Define and use these everywhere (dataclasses + enums; `registry.py` returns them):

- `class Criterion(Enum)`: `TRUE, FALSE, MAYBE, NA` — replaces raw strings; `SelectionCriteria` dataclass
  with the six typed `Criterion` fields (`c1_fixed_or_capped … c6_reward_rationale`).
- `Classification(Enum)` / `Mechanism(Enum)` / `RewardType(Enum)` — mirror `field_dictionary`.
- `Actionability(Enum)`: `ACTIONABLE, BLOCKED_NEEDS_CAPTURE, BLOCKED_UNVERIFIED, BLOCKED_TIER,
  BLOCKED_PROMOTION, BLOCKED_CAPS`.
- `EVScenarioInputs` dataclass: typed fields `p_eligibility, p_distribution, reward_qty,
  realizable_price, liquidity_vesting_haircut, base_yield, gas_bridge_fees, capital, days,
  benchmark_apy, expected_loss_reserve, manual_hours, hourly_rate` (floats; `reward_announced: bool`);
  validate ranges (probabilities ∈ [0,1], non-negative costs) at construction.
- `DeadlineInputs` dataclass: typed `eligibility_open/close, claim_date, vesting_end, review_expiry`
  (`date | None`), `within_days: int`.
- `Address` value object — see §5.
- `PilotCaps` dataclass: `total_usd=1000, per_program_usd=250, max_concurrent=3` (frozen defaults).

## 1.6 `capture.py` — source capture (populates the frozen snapshot)

Turns a `PENDING_TOOL_CAPTURE` record into a frozen one. **Read-only GET to the record's
`official_source_url` (allowlisted per §5)**; persist the **raw fetched bytes** to
`research/a1-incentive-farming/snapshots/<id>/<captured_at>.html`; compute `snapshot_sha256` over those
raw bytes (reproducible — not over model-summarized text); write back `snapshot_sha256` + `captured_at`
into a capture sidecar (do not mutate the human-frozen fixture in place — emit
`…/captures/<id>.yaml`). Refuse non-allowlisted or `UNVERIFIED` URLs. No JS execution, no auth.

## 2. `classify.py` — the classification engine (tier only; THIS is the core test)

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
**`classify` decides tier only — it must NOT decide actionability** (that is §2.5).

## 2.5 `actionability.py` — separate actionability gate (default-deny)

Independent of tier. `actionability(record, captures, ledger, caps) -> Actionability`. Returns
`ACTIONABLE` **only if all** hold; otherwise the first failing `BLOCKED_*`:

1. **Captured:** a `capture.py` sidecar exists with a real `snapshot_sha256` (≠ `PENDING_TOOL_CAPTURE`)
   and a `captured_at` within freshness window → else `BLOCKED_NEEDS_CAPTURE`.
2. **Verified:** `live_round_status` confirmed against the captured snapshot (open round, terms match) →
   else `BLOCKED_UNVERIFIED`.
3. **Tier:** `classification ∈ {IN_CORE, IN_CONDITIONAL}` → else `BLOCKED_TIER` (YIELD_ONLY/DEFER/REJECT
   are never actionable in the pilot).
4. **Promotion gate:** `selection_criteria` has no `FALSE`, required criteria are `TRUE` (not `MAYBE`),
   and base-case EV (§3) is positive → else `BLOCKED_PROMOTION`.
5. **Caps:** committing it keeps the ledger within `PilotCaps` (§6) → else `BLOCKED_CAPS`.

**Given the fixture is uncaptured/unverified, this MUST return a non-`ACTIONABLE` status for all 17
records** (most `BLOCKED_NEEDS_CAPTURE`). This is a hard acceptance check.

## 3. `ev.py` — scenario EV calculator (implement the parent-spec formula exactly)

Pure functions, no I/O. **Input is the typed `EVScenarioInputs` (§1.5), validated at construction**
(probabilities ∈ [0,1], non-negative costs); call once per scenario (base + upside). No raw dicts.

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

From the registry compute, **via the typed `DeadlineInputs` (§1.5)** (parsed `date` objects, not
strings): upcoming eligibility windows, claim/vesting dates, and `review_expiry` status; alert items
within `within_days` (default 7); flag expired records. No network needed.

## 5. `eligibility.py` — read-only public lookups (address-only)

- Interface: `fetch_eligibility(program_id: str, address: Address) -> EligibilitySnapshot`. **Address
  only — the parameter is the typed `Address` value object (§1.5), never a raw string.**
- **Validated address handling (`Address`):** constructor validates format (EVM `0x` + 40 hex w/
  EIP-55 checksum, or the chain's canonical format) and **rejects anything that looks like a private
  key or seed phrase** (e.g. 64-hex / 0x+64-hex / BIP-39 mnemonic word-count) by raising
  `SuspectedSecretError` — fail closed, never log the offending value.
- **Endpoint allowlisting:** every GET URL's host+path prefix must match
  `config/incentive_ops/endpoint_allowlist.yaml` (approved public read-only endpoints per program);
  non-allowlisted targets raise `EndpointNotAllowed`. No redirects off-allowlist.
- HTTP **GET only**, public endpoints/explorers; per-program adapters (pluggable; ship 1–2 examples,
  the rest stubbed). Cache responses + diff over time so accrual is visible.
- **Forbidden:** any write/sign/send, any wallet-connect, any key/seed parameter. If an adapter needs
  auth beyond a public read, it raises `NotSupportedReadOnly`.

## 6. `accounting.py` — caps + ledger + report

- **Runtime cap validation (not just reporting):** `validate_caps(ledger, candidate, caps: PilotCaps)
  -> CapCheck` is called by `actionability.py` (§2.5 gate 5) and by any commit path; it **blocks**
  (returns a failing `CapCheck` / raises `CapsExceeded`) if adding the candidate would breach **total ≤
  $1,000, ≤ $250/program, or > 3 concurrent programs**. Caps are the typed `PilotCaps` (§1.5), enforced
  at runtime — a record cannot be marked `ACTIONABLE` if it would breach them.
- Reads a manual-entry ledger (CSV/JSON the human fills) — the tool never moves funds.
- Gas/fee ledger; realized-PnL + hours-worked report; `net_per_manual_hour` actuals vs EV estimates.

## 7. `cli.py`

`validate | capture [--id ID] | classify [--check] | actionability | ev [--scenario base|upside] |
deadlines [--within-days N] | eligibility --address 0x.. | report`. Structured logging; non-zero exit on
validation failure. `actionability` prints the per-record gate result (expected: all 17 non-`ACTIONABLE`).

---

## Acceptance criteria (planner review gate)

1. `validate` passes on the starter registry (with the expected PENDING/UNVERIFIED **warnings**).
2. `classify --check` **reproduces all 17 recorded labels** (5 IN_CORE / 4 IN_CONDITIONAL / 3 YIELD_ONLY
   / 2 DEFER / 3 REJECT); the synthetic `undisclosed-sybil-points-farm-archetype` must auto-REJECT.
   Classification is tier-only and does not consult actionability.
3. `actionability` returns a **non-`ACTIONABLE` status for all 17 records** (uncaptured/unverified);
   most `BLOCKED_NEEDS_CAPTURE`. No record can be `ACTIONABLE` without capture + verify + actionable
   tier + promotion gate + caps.
4. `capture` fetches an allowlisted source, persists raw bytes, writes a real `snapshot_sha256` (over
   raw bytes) + `captured_at` to a sidecar; refuses non-allowlisted/`UNVERIFIED` URLs.
5. `ev` unit-tested with deterministic golden values on typed `EVScenarioInputs`; base case forces
   unannounced-points → 0; invalid inputs (prob ∉ [0,1], negative cost) raise at construction.
6. **Validated address + allowlist:** `Address` rejects key/seed-shaped input (`SuspectedSecretError`,
   never logged); eligibility GETs only hit allowlisted hosts (`EndpointNotAllowed` otherwise).
7. **Runtime caps enforced:** `validate_caps` blocks total > $1,000 / per-program > $250 / > 3 concurrent
   (unit-tested with a breaching ledger).
8. **No network calls** in `validate/classify/actionability/ev/deadlines/accounting`. Only `capture`
   (allowlisted GET to official_source_url) and `eligibility` (allowlisted GET, address-only) touch the
   wire — both read-only.
9. `ruff check` + `ruff format --check` clean; typed value objects across module boundaries (no loose
   dicts); `get_logger`, no `print()`.
10. **Absent by construction:** any tx signing, key/seed handling, auto-claim/auto-deploy, or a
    "restaking optimizer." If present → reject the PR.

## Out of scope (do NOT build in Phase 0)

Auto-deploy/auto-claim, transaction construction/signing, wallet-connect, a restaking/yield optimizer,
any capital movement, any `src/strategy` or RBI-loop integration.

## Roles & flow

1. Planner froze the fixture + these rules. 2. Grok builds `tools/incentive_ops/` + tests. 3. Planner
reviews code against acceptance criteria + the classification reproduction. 4. Human (only) decides any
Phase-1 capital later, gated. The tool informs; the human acts.
