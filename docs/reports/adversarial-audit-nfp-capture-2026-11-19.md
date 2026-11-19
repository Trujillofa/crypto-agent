# Adversarial audit — NFP capture stack and v2 gate draft (2026-11-19)

**Status:** EVIDENCE for the v2-signature deferral decision. Read-only audit; nothing was
deployed, signed, or changed on any host during it.
**Audited tree:** `main` @ `248af11` (PR #249 merged). `uv run pytest` and
`uv run ruff check . && uv run ruff format --check .` were green at that SHA.
**Scope:** `scripts/nfp_consensus_from_mt5.py`, `docs/evidence_portfolio/NFP_FORWARD_GATE_V2.md`,
`scripts/nfp_forward_capture.py`, `ops/systemd/crypto-agent-nfp-capture.*`, the sentinel
integration, and the MT-2 dependency in mt5-arch-integration.

## Gating verdict

| Question | Verdict |
|----------|---------|
| Can the v2 gate be signed now? | **No — blocked.** The named changed input does not exist: mt5-arch-integration `main` has no MT-1/MT-2 code (the plan is still open as PR #141), and `/opt/shared/calendar_pit` is absent on the host. B1 and B2 below also had to be fixed first. |
| What governs the 2026-12-04 print? | **v1, under v1's own text.** v1 is signed and in force (`DAY30_REVIEW.md`). With 3 missed captures, v1 says the gate "is decided on whatever sample exists at the original decision date (no extension)". Dec 4 is a v1 print. |
| Is retiring `agent_sol_sparse` safe? | **Yes, with sentinel co-changes** (the drift sentinel must stop expecting that agent in the same change). |
| Is the BTC–ETH OU screen affected? | **No.** Nothing here touches it. |

## Blocking defects (fixed by the PR that adds this report)

### B1 — MT5 consensus reader failed open

`scripts/nfp_consensus_from_mt5.py` promised fail-closed in its docstring but failed open.
Verified repros:

- A latest pre-release change of `'n/a'`, `null` or `0` was dropped (the `or` chain treated
  `0` as missing; the float cast failure returned `None`), so the reader silently returned
  the stale forecast from days earlier.
- `LONG_MIN` (MT5's "no value" marker) parsed as −9.2e18, and `NaN` was accepted.
- Event matching used a name regex: `Private Nonfarm Payrolls` (event 55) could beat the
  headline (event 65), and unknown events were relabelled through the
  `event_name` default `'US Nonfarm Payrolls'`.
- On broker disagreement the later timestamp silently won.
- Trade-server epochs were read as UTC. With a server clock ahead of UTC, the freshest
  pre-release change appears to be after the release and is dropped.

Per the MT-2 spec as extracted by the audit, `snapshot_server_time` / `time_server` carry
trade-server time (not UTC), numeric values are scaled ×10⁶, and `LONG_MIN` marks missing.

**Fix:** exact match on a required `--event-id` (no name matching); required
`--server-utc-offset` conversion, where a wrong offset fails as "no records" rather than
moving the cutoff; ×10⁻⁶ scaling of a JSON integer only; a `LONG_MIN`/`null`/`NaN`/string
latest change is a hard failure with no fallback; `0` is a legitimate consensus; broker
disagreement fails and prints every broker's value; a torn final line or truncated JSON
fails; any schema mismatch on any line fails.

### B2 — v2 gate draft was not decision-complete

- No rule for "MT5 missing but Investing captured" or "sources disagree". These must be
  fixed before any print is seen under v2.
- The header said v1 "closed at 3 missed captures on 2026-10-02 per its own rule". v1's
  text says the gate is decided on whatever sample exists at the *original decision date*
  (no extension), and `DAY30_REVIEW.md` lists v1 as SIGNED, in force.
- The first-eligible-print rule and the v1 scope limits ("No optimization", "no intraday
  variants", scripts read-only toward production services) had been dropped.
- The v2 CSV was not named.

**Fix:** the draft now carries the strict options, each marked
`[DRAFT CHOICE — HUMAN CONFIRMS AT SIGNATURE]`: MT5 PIT missing → `MISSED_CAPTURE`;
exact-match consensus, disagreement → `MISSED_CAPTURE` with both values; latest
pre-release capture authoritative. The header quotes v1's own text, the v1 scope limits are
copied verbatim, the first eligible print is the first release after signature, and the CSV
is `data/macro_events/nfp_good_news_forward_v2.csv`, with the capture-tool wiring as a
follow-up PR.

## Automation defects (open — owned by the parallel capture-tool PR)

- **H1 — release-date heuristic misfires.** `next_scheduled_release` guesses the first
  Friday of the month. That is wrong for **2027-01-08** (Jan 1 is a holiday) and
  **2027-10-08**, so the timer would capture for the wrong day.
- **H2 — systemd unit cannot run.** `crypto-agent-nfp-capture.service` runs
  `/usr/bin/python3`, which cannot import `httpx` on the host. Every timer firing fails on
  import.
- **H3 — fallback skipped on verification failure.** Snapshot verification runs outside the
  Wayback `try`, so a Wayback snapshot that fails point-in-time verification aborts the run
  instead of falling through to archive.ph.

## Input-integrity gaps (open)

- **M1 — typed-timestamp trust.** An operator-typed snapshot timestamp is trusted without
  independent verification.
- **M2 — unguarded miss.** Recording a miss is not guarded against an existing pending
  capture or committed row for the same release.
- **M3 — unchecked post numbers.** `post` values (actual / consensus) are not
  sanity-checked before the append-only row is written.
- **M4 — stranded pending stash.** A pending stash from a past release can be left in place,
  which blocks later `pre` runs without an alert.

## Consequence

The v2 signature is deferred until MT-2 exists and produces real PIT lines on the host. At
that point: fill in the reader parameters in the v2 draft, land the v2 CSV wiring, have a
human sign, and only then does the first eligible v2 print exist. Until then, NFP
measurement continues under v1.
