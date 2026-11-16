# crypto-agent — Next Workflow (2026-11)

**Written:** 2026-11-16 · **For:** builders (Cursor, Hermes, Claude Code, OpenCode) and the human operator
**Context:** cross-repo review of all trading agents. All four production agents are
paper. The structural-probe surface is closed (`docs/reports/research-consolidation-2026-06-19.md`).
The **only** lane with a passing OOS result, NFP good-news-is-good (probe #1, YES, PF 2.18,
21 trades), is about to fail its forward gate by default because the consensus
capture (Wayback "Save Page Now" of an Investing.com page) keeps failing. Meanwhile
recent effort went mostly into sentiment-provider fallbacks for a paper agent.

This plan: (1) save or properly restart the NFP forward measurement, (2) clear the
unsigned and unrun gates that leave the portfolio in limbo, (3) stop spending on
paths with no decision attached.

## Ground rules for every item

- `CLAUDE.md` (5-step framework, commit conventions, Co-Authored-By identities) and
  `docs/evidence_portfolio/README.md` (hard budget rules) stay authoritative.
- **No edits to a signed gate document after a print/data it governs has been seen.**
  Amendments are new documents with a named changed input and a human signature.
- No `--execute` on RBI tooling, no live config, no deploy without the human.
- Tests: `uv run pytest -v`; lint: `uv run ruff check . && uv run ruff format --check .`.
- Branch per item: `feat/<item-id>-<slug>` / `fix/<item-id>-<slug>`.

## Status board

| ID | Item | Owner | Depends on | Size |
|----|------|-------|-----------|------|
| CR-1 | NFP forward gate: rescue Oct/Nov prints, then v2 capture | Builder + **Human signature** | MT-1/MT-2 (mt5 repo) for v2 source | M |
| CR-2 | Ratify or retire `DAY30_REVIEW.md` | **Human** (15 min) | — | XS |
| CR-3 | Run or close `BTC_ETH_OU_PREREG` (locked 2026-09-19, never screened) | Builder → Human | — | S |
| CR-4 | Sentiment spend freeze + DeepSeek window decision date | Human, Builder writes doc | — | XS |
| CR-5 | Retire `agent_sol_sparse`; closed-bar state parity test | Human decides, Builder implements | — | S |
| CR-6 | PR hygiene (#224, #238) | Human | — | XS |

---

## CR-1 — NFP forward gate: rescue, then v2

**Facts (read before acting):**
- Gate: `docs/evidence_portfolio/NFP_FORWARD_GATE.md` (signed 2026-07-21).
- Forward CSV `data/macro_events/nfp_good_news_forward.csv` holds 2 rows, both
  `NFP_MISSED_CAPTURE` (2026-08-07, 2026-09-04). Rows are append-only and are
  **never edited**.
- The prints after that (first Fridays of Oct and Nov 2026: verify the dates on
  https://www.bls.gov/schedule/news_release/empsit.htm) have **no row at all**.
- Rule: 3+ missed captures → decided on the existing sample at the original decision
  date, no extension. With 0 captured trades, that ends in a FAIL by default.
- The rule only needs a pre-release snapshot of the consensus page captured
  *within 24h before release*, with its exact URL. It does not require that **we**
  triggered it.

### Part A — rescue Oct and Nov (builder, from a machine where archive.org works)

Wayback CDX queries returned `Connection reset by peer` from the review sandbox. That
is the same failure mode as the August capture. Run from the Hetzner host or a laptop:

```bash
URL='investing.com/economic-calendar/nonfarm-payrolls-227'
# window = [release - 24h, release); adjust dates to the verified BLS schedule
curl -s "https://web.archive.org/cdx/search/cdx?url=$URL&from=20261001123000&to=20261002123000&output=json&fl=timestamp,statuscode,original"
curl -s "https://web.archive.org/cdx/search/cdx?url=$URL&from=20261105133000&to=20261106133000&output=json&fl=timestamp,statuscode,original"
# also try archive.ph: https://archive.ph/newest/https://www.investing.com/economic-calendar/nonfarm-payrolls-227
```

For each print:
- **A 200 snapshot exists inside the window** → open it, confirm the page shows that
  release date and a forecast (the tool's `verify_snapshot_is_point_in_time` does this),
  then:
  ```bash
  uv run python scripts/nfp_forward_capture.py pre --release-date <YYYY-MM-DD> --snapshot-url '<web.archive.org/web/<ts>/...>'
  uv run python scripts/nfp_forward_capture.py post ...   # BLS actual, per the checklist
  ```
- **None exists** → `uv run python scripts/nfp_forward_capture.py miss --release-date <date>`
  with the CDX output pasted into the PR as evidence.

Commit rows in release order (Oct before Nov). One PR: `research/nfp-forward-rescue-2026-10-11`.
If either is a miss, the count reaches 3: write that plainly in the PR and in
`docs/evidence_portfolio/DAY30_REVIEW.md`'s NFP row. No extension, no relabel.

### Part B — make future captures not depend on one fragile path

Whatever Part A finds, the next print (first Friday of December 2026, verify on BLS)
must not fail the same way:

1. Hetzner cron: `nfp_forward_capture.py pre` at release−23h **and** release−6h, each
   trying Wayback SPN, then archive.ph submit (`https://archive.ph/submit/`), with the
   resulting URL stored in the pending JSON.
2. Telegram alert at release−3h if no pending capture exists, so a human can save the
   page manually with the browser while there is still time.
3. Add `nfp_forward_capture.py status --strict` that exits non-zero when the next
   release is < 24h away with no pending capture; wire it into the existing drift
   sentinel / monitoring alert rules.

This is collection plumbing under the existing gate. It changes no rule, so it needs
no new signature.

### Part C — v2 gate (only if Part A ends at ≥3 missed)

The old gate then closes per its own rules. A restart needs a **named changed input**:
an automated, redundant consensus capture whose source is the MT5 economic calendar's
`forecast` logged point-in-time by mt5-arch-integration **MT-2** (`CalendarValueLast`
change log), with Investing.com/Wayback as a cross-check.

- Builder drafts `docs/evidence_portfolio/NFP_FORWARD_GATE_V2.md`: rule under test
  copied **verbatim** from v1 (BTCUSDT 1h, hot surprise → long at first 1h close, exit
  +24h, 0.12% costs, `z = surprise / 220.28`); the consensus source definition;
  sample/decision point; pass gates copied from v1; what happens on a capture miss.
- **Human signs** before the first print it governs. The draft PR must not be merged
  by a builder.
- A `scripts/nfp_consensus_from_mt5.py` reader takes the MT-2 PIT log
  (rsynced from the MT5 desktop to the server by mt5 `scripts/push_calendar_pit.sh`,
  e.g. `/opt/shared/calendar_pit/<broker>/*.jsonl`; event code for US Nonfarm Payrolls) and writes the pre-release forecast for a release date, failing
  closed if the last pre-release change is missing or after the release.

**Do not:** backfill v1 rows from MT5 history, change thresholds, add ETH/SOL to the
verdict, or call any of this a new probe.

## CR-2 — Ratify or retire `DAY30_REVIEW.md` (human)

`docs/evidence_portfolio/DAY30_REVIEW.md` has been `READY FOR RATIFICATION — unsigned`
since August and its NFP row is stale. Either sign it as-is with a dated note that the
NFP row is superseded by CR-1, or mark it `RETIRED (superseded by NEXT-WORKFLOW-2026-11)`.
Builder can prepare the one-line status edit; the human makes the call.

## CR-3 — Run or close `BTC_ETH_OU_PREREG`

`docs/evidence_portfolio/BTC_ETH_OU_PREREG.md` (Binance spot BTC–ETH log-spread fade)
is `LOCKED — no tape fetch, no develop metric` since 2026-09-19.

- **Option run (builder):** execute exactly as locked: develop window
  `[2024-01-01, 2026-01-01)`, holdout sealed, its own kill section. Write
  `docs/reports/btc-eth-ou-screen-<date>.md` with the verdict. No parameter changes.
- **Option close (human):** mark `CLOSED_NOT_RUN` with a reason (e.g. budget moved to
  CR-1). Either is fine; leaving it locked forever is not.

## CR-4 — Sentiment spend freeze and DeepSeek window decision date

`agent_sentiment_macro` is paper. October and November had 15+ PRs on provider
fallbacks (NVIDIA, Command Code, OpenCode Go, Ollama). The DeepSeek paper window
(`DEEPSEEK_PAPER_WINDOW_PREREG.md`, T0 2026-08-31) has no performance denominator approved.

- Human decides one of: (a) set a decision date for that window and what number
  decides it, or (b) stop recording sentiment and disarm the agent.
- Until then, builders take no further provider-fallback work unless the feed has
  been down > 24h. Write this as a short note in `docs/commandcode-sentiment.md`.

## CR-5 — Retire the sparse agent; closed-bar state parity

- `agent_sol_sparse` has 2 signals ever (2026-04-25, 2026-05-05); the 2026-10-22
  rebaseline shows the drought is by design, and both signals came from an intrabar
  ratchet on forming candles. Human: retire it from `docker-compose.prod.yml` (paper
  only, no capital) or keep with a written reason.
- The forming-candle input was fixed on 2026-10-22 (#208 live indicators publish closed
  bars only, #212 backfills). Remaining parity gap: `src/strategy/trend_pullback.py`
  keeps `_previous_rsi/_previous_macd_hist/_previous_close` **per evaluation call**
  (every 60s), not per bar. With closed-bar indicators this is mostly harmless, but a
  restart makes `previous == current` and suppresses the first recovery check.
  Builder: key the previous-values by the indicator row's bar timestamp (update only
  when the bar changes), add a test that N evaluations inside one bar equal one
  evaluation, and a test that replaying closed bars through `evaluate()` reproduces
  the backtest engine's signals on a fixture.

## CR-6 — PR hygiene (human)

- #224 `fix: use supported node exporter healthcheck wget flags` (open since 2026-10-29):
  merge or close; #225 already fixed the Prometheus one the same way.
- #238 `fix(backtest): treat sentiment errors as replay gaps`: review against CR-4's
  decision. If sentiment is being retired, close it.

---

## Cross-repo dependencies

| This item | Needs | From |
|-----------|-------|------|
| CR-1 Part C | MT5 calendar PIT forecast log for US NFP | mt5-arch-integration **MT-1, MT-2** |
| (ctrader CT-1) | nothing here | — |
