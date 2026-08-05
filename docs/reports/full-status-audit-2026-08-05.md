# Full Status Audit — Local · GitHub · Hetzner

**Date:** 2026-08-05 (UTC snapshot ~15:45)
**Branch:** `chore/full-status-audit-2026-08-05`
**Scope:** `crypto-agent`, `ctrader-trading-agent`, shared Hetzner host `trujillo-server`
**Method:** live git fetch, `gh` API, SSH to production, local config/docs scan
**Verdict overall:** Infrastructure is healthy. **One hard calendar deadline hits in ~21h.** Several open cTrader PRs are blocked by a one-line main-branch regression. Research program remains terminal on public OHLCV.

---

## Executive summary (read this first)

| Priority | Item | Surface | Action needed |
|---|---|---|---|
| **P0 — TODAY/TOMORROW** | NFP forward pre-capture for 2026-08-07 print | crypto-agent | Run `scripts/nfp_forward_capture.py pre` in the window **2026-08-06 12:30 UTC → 2026-08-07 12:30 UTC**. Tool exists; forward CSV is empty (0 captured / 0 missed). |
| **P0 — ~TOMORROW** | Day-30 portfolio ratification | crypto-agent evidence portfolio | Sign `docs/evidence_portfolio/DAY30_REVIEW.md` (still DRAFT). Kill gate does not fire (NFP YES). |
| **P1** | Merge cTrader PR #50 (backtest harness path) | GitHub + main CI | Unblocks `main` CI and PR #48/#49. All checks green. |
| **P1** | Land PR #48 exit-path gate telemetry | cTrader | Blocked only by main CI red from harness path. After #50, re-run CI and merge. |
| **P2** | cTrader open safety/research branches | local + GitHub | `fix/live-safety-hardening`, pair-concentration, phase3 CLI split, MC harness — not deployed |
| **P2** | FundedHive challenge progress | Hetzner cTrader | Peak equity $5k, target not reached, last closed trade 2026-07-29; agent healthy, currently outside entry window |
| **P3** | Local hygiene | both repos | Stale branches with `[gone]` upstream, stashes, untracked noise |
| **OK** | crypto-agent production | Hetzner | At `4c5a750` = `origin/main`; 4 agents + infra healthy; **all paper** |
| **WATCH** | cTrader production | Hetzner | Deployed `2d3ae6b` = `origin/main`; agent healthy after **recreate 15:45Z today**; phantom alert noise (May ticket=0 rows); FundedHive flat since 2026-07-29 |
| **CLOSED** | Staking-carry candidate | research | Closed 2026-07-27 (PR #159); forward excess still negative even with staking |
| **CLOSED** | Public OHLCV research program | research | Terminal as of 2026-06-23/24; do not reopen structural probes |

---

## 1. Host / Hetzner production

Both stacks run on the **same** machine:

| Field | Value |
|---|---|
| Host | `trujillo-server` |
| Access | `ssh crypto-agent` or `ssh -i ~/.ssh/hetzner_deploy root@46.225.119.221` |
| Uptime | 51 days |
| Load | 0.88 / 0.63 / 0.46 |
| Disk `/` | 75G total, **45G used (63%)**, 27G free |
| Docker images | 15.2G total; **~8.8G build cache reclaimable** |
| Docker volumes | 1.7G (0.3G reclaimable) |

### 1.1 Containers (all healthy unless noted)

| Name | Status | Role |
|---|---|---|
| `crypto-agent-agent_sol_1h_trend_pullback_overlay_live-1` | Up 7d healthy | SOL 1h overlay (**paper**) |
| `crypto-agent-agent_sentiment_macro-1` | Up 7d healthy | Sentiment/macro feed (**paper**) |
| `crypto-agent-agent_sol_sparse-1` | Up 7d healthy | SOL sparse (**paper**) |
| `crypto-agent-agent_sol_panic_block_paper-1` | Up 7d healthy | SOL panic-block (**paper**) |
| `crypto-agent-timescaledb-1` | Up 7w healthy | Timescale PG14 |
| `crypto-agent-prometheus-1` | Up 4w healthy | Metrics |
| `crypto-agent-grafana-1` | Up 7w healthy | Dashboards |
| `ctrader-agent` | Up 7d healthy | Session-momentum FX |
| `ctrader-timescaledb` | Up 4w healthy | Timescale PG16 |
| `ctrader-prometheus` / `ctrader-alertmanager` / `ctrader-grafana` | healthy | cTrader stack |
| `manual-trading-agent` | Up 10d healthy | Separate manual agent |

### 1.2 Deploy pin alignment

| Stack | Server HEAD / DEPLOYED_VERSION | `origin/main` | Match? |
|---|---|---|---|
| crypto-agent `/opt/crypto-agent` | `4c5a750` — *Record cTrader exit-path measurement; close staking-carry candidate (#159)* | `4c5a750` | **Yes** |
| cTrader `/opt/ctrader-trading-agent` | `2d3ae6b` / `v2026-07-14-journal-repair-stable-2-g2d3ae6b` @ 2026-07-27T20:09Z | `2d3ae6b` | **Yes** |

Agents have been up **7 days** without rebuild. Code on disk matches main, but image rebuild cadence is independent — current containers were built ~7d ago (still consistent with post-#159 deploy window for crypto-agent).

### 1.3 Production log / state notes

**crypto-agent**

- Indicator + strategy loops healthy (1–3 symbols per cycle, signals=0 common on sparse paper agents).
- Timescale log noise: extension version lag (`2.19.3` installed vs `2.29.x` available) — non-blocking.
- One bad ad-hoc query observed: `select max(time) ... from funding_rates` → `column "time" does not exist` (operator error, not agent failure).

**cTrader**

- Cycles every ~2m: `evaluated=11 … attempted=0 executed=0` — GBPUSD buy score 2.46 **outside entry window** (normal off-session behavior).
- `health_monitor_state.local.json`: `status=ok`, `health=healthy`, auth expiry ok (~23d).
- `ops_monitor_state.json`: `last_phantom_alert` stamped **2026-08-05T15:40:01Z** (same second as last run). Worth a quick human glance at alert channel / phantom reason — monitor is running, but stamp is fresh.
- `fundedhive_state.json` (last update **2026-07-29**):

  | Field | Value |
  |---|---|
  | peak_equity | 5000.0 |
  | profit_target | 0.08 |
  | target_reached | false |
  | profitable_days (raw) | 19 |
  | profitable_days_count | 8 |
  | daily_pnl_r | −0.19 |
  | open_tickets | {} |

- Recent journal closes (through 2026-07-29): mix of `trailing_stop (broker)`, `partial_tp`, `tp (broker)`, `sl (broker)`. Last trade EURUSD sell stopped out (~−1.01R).

---

## 2. crypto-agent — GitHub & local

### 2.1 Git

| Item | State |
|---|---|
| Remote | `https://github.com/Trujillofa/crypto-agent` |
| `origin/main` | `4c5a750` |
| Local main | fast-forwarded to match |
| Active audit branch | `chore/full-status-audit-2026-08-05` |
| Prior branch | `docs/ctrader-exit-path-gate-finding` — **merged via #159**, remote deleted; local branch still present with `[gone]` |
| Open PRs | **none** |
| Open issues | **none** |
| CI on main | last runs **success** (CI + Deploy after #159) |
| Working tree | clean on audit branch |

### 2.2 Recent merges (context)

| PR | Title | Merged |
|---|---|---|
| #159 | Record cTrader exit-path measurement; close staking-carry candidate | 2026-07-27 |
| #158 | NFP forward gate: capture routine spec + capture tool | 2026-07-27 |
| #157 | remove no-op pyrightconfig | 2026-07-25 |
| #156–#154 | NFP gate / OOS YES path | mid-July |

### 2.3 Local stashes (hygiene — not urgent)

| Stash | Origin | Notes |
|---|---|---|
| `stash@{0}` | `feat/mnav-probe-impl` | small docs/migration whitespace-ish |
| `stash@{1}` | `feat/macro-surprise-drift-probe` | empty/near-empty |
| `stash@{2}` | main | backtesting report rewrite Mar 2026 |
| `stash@{3}` | main | settings + download_historical WIP |

Safe to review/drop after confirming nothing unique is needed. **Do not apply blindly.**

### 2.4 Production agents (config truth)

All four production strategy services are **disarmed / paper**:

| Service | AGENT_ID | mode | trading_execution.enabled |
|---|---|---|---|
| sol 1h trend pullback overlay | `sol-1h-trend-pullback-overlay-live` | paper | false |
| sentiment macro | `sentiment-macro-bot` | paper | false |
| sol sparse | `sol-trend-pullback-sparse` | paper | false |
| sol panic-block | `sol-4h-panic-block-paper` | paper (implied) | false |

Rationale unchanged: threshold/edge failures at corrected costs; research program terminal on that surface. Overlay name still contains `_live` but config is paper.

---

## 3. NFP forward gate — **time-critical**

| Field | Value |
|---|---|
| Gate doc | `docs/evidence_portfolio/NFP_FORWARD_GATE.md` — **IN FORCE** (signed 2026-07-21) |
| OOS verdict | **YES** (PR #154) |
| Capture tool | `scripts/nfp_forward_capture.py` — **exists** (PR #158); tests exist |
| Forward CSV | `data/macro_events/nfp_good_news_forward.csv` — **0 captured, 0 missed** |
| Pending stash | none |
| Next release | **2026-08-07 12:30 UTC** (08:30 ET) |
| Capture window opens | **2026-08-06 12:30 UTC** |
| Hours to window open (as of audit) | **~20.8h** |
| Kill risk | ≥3 `MISSED_CAPTURE` rows caps the sample |

### Required human actions

1. **Before 2026-08-07 12:30 UTC**, preferably soon after window opens on **2026-08-06**:

   ```bash
   cd /home/yderf/Projects/trading/TRADING/crypto-agent
   uv run python scripts/nfp_forward_capture.py pre
   # (follows Wayback Save Page Now of Investing.com NFP consensus)
   ```

2. After BLS print:

   ```bash
   uv run python scripts/nfp_forward_capture.py post
   # supply BLS actual + consensus from frozen snapshot
   ```

3. If pre missed:

   ```bash
   uv run python scripts/nfp_forward_capture.py miss
   ```

**Note:** `docs/reports/edge-candidates-2026-07-27.md` §1 still says the script “does not exist yet” in one place — that sentence is **stale**; #158 landed the tool. The deadline and gate rules remain correct.

This is the **single highest-priority item across the whole estate** purely because of the calendar.

---

## 4. Research / edge-candidate board

Source: `docs/reports/edge-candidates-2026-07-27.md` (+ #159 staking close).

| # | Candidate | Class | Status | Next |
|---|---|---|---|---|
| 1 | NFP good-news event lane | Measured | **ACTIVE — capture pending** | Pre-capture 2026-08-06→07 |
| 2 | Conditional CPI analog | Unpriced | Deferred | Only after NFP day-30 review |
| 3 | Delta-neutral staking carry | Unpriced | **CLOSED 2026-07-27** | Do not reopen; forward excess still < 0 |
| 4 | Access / size-is-edge ops | Structural | Optional business track | Not engineering |
| 5 | Scale cTrader FX system | Measured | Active external | Exit-path gate + FundedHive rules |
| 6 | Maker-rebate MM C-tier | Structural | Closed for accessible profile | Needs venue rebate access |
| — | On-chain flow wildcard | Unpriced | Low prior | Cheap probe only if deliberately opened |

**Public-data OHLCV program:** TERMINAL (2026-06-19 / 06-23 / 06-24 consolidations). Path 2 illiquid-venue closed at Gate 0 economics. RBI loop lanes under `research/rbi_loop/` are historical; no active execute mandate.

Autoresearch candidate ledger still lists historical `DEPLOY_LIVE` for SOL overlay — **superseded**; service is paper.

---

## 5. cTrader trading agent — GitHub & local

### 5.1 GitHub open PRs

| PR | Title | CI | Blocker | Action |
|---|---|---|---|---|
| **#50** | fix(cli): repair backtest harness path after scripts reorg | **All green** (Lint/Test/Build/Infra) | None | **Merge first** — unblocks main + other PRs |
| **#48** | Exit-path gate telemetry | Test **fail** (inherits main red) | Needs #50 on main | Merge after #50; re-check CI |
| **#49** | docs: rsync exclude dev caches + .gitignore newline | Test **fail** (same) | Needs #50 | Merge after #50 (docs-only) |

**Root cause of red main:** PR #47 moved `scripts/backtest_harness.py` → `scripts/backtest/`, but `src/cli.py` still constructs the old path at runtime. One-line fix is #50; full suite 293 passed on that branch.

### 5.2 GitHub open issues

| # | Title | Notes |
|---|---|---|
| #20 | ops: promote + deploy cost-model P0/P1 backlog | Dated June; partially historical vs current deploy pin |
| #19 | bug: prod `ctrader_client.py` out-of-band | Untracked patched client on server historically — still relevant to audit |
| #14 | Integrate ForexGPT (paper-first) | Feature backlog |
| #10 | CI lint mypy drift | May be improved; main still had test red from harness |

### 5.3 Local branches of interest

| Branch | vs main | Remote | Intent |
|---|---|---|---|
| `fix/backtest-harness-path` *(checked out)* | +1 commit | origin open (#50) | Unblock CI |
| `feat/exit-path-gate-telemetry` | +4 commits | origin open (#48) | Gate measurement |
| `fix/live-safety-hardening` | +1 | on origin | Retry flatten / unprotected fill alerts |
| `fix/phantom-close-reconciliation` | (check tip) | on origin | Live close confirm |
| `feat/pair-concentration-robustness` | research | on origin | NZDJPY concentration study |
| `phase3-split-cli` | multi-commit CLI split | on origin | High-risk refactor; merge plan exists |
| `claude/monte-carlo…` / `trial-mc-rebase` | MC harness | diverged | Low risk additive |
| Several `[gone]` locals | orphaned | deleted remotes | Delete after confirm |

Untracked local noise: `.omo/`, `DEPLOYED_VERSION`, `docker-compose.yml.bak-*`, `docs/plans/branch-merge-plan.md`.

Local `main` was **2 commits behind** `origin/main` at audit start (now known: needs `git pull` on that repo). `stable` pin still at `52dd271` (pre-#47 cleanup) — **stable ≠ main**.

### 5.4 Exit-path gate (external to crypto-agent)

Canonical write-up mirrored in crypto-agent as `docs/evidence_portfolio/CTRADER_EXTERNAL_GATE.md` (boundary note only).

| Path | Live executions (as of 07-27/28 analysis) | Replay | Ready? |
|---|---|---|---|
| `partial_tp` | 20 | dedicated parity | **yes** (per gate restatement) |
| `trailing_stop` | 13 (pre broker_managed_protection) | needs dedicated scenario (added in #48 era) | pending merge/deploy of telemetry |
| `time_stop` | 0 — effectively unreachable at defaults (10 bars vs live max 9) | none | no / redesign |
| `stale_exit` | 0 — same horizon issue | none | no / redesign |
| `weekend_flatten` | 0 live | not replayed | no |

**Decision already recorded:** inferred live counts accepted toward ≥5 threshold; deterministic replay still required. With `broker_managed_protection` (2026-07-14), live bare trailing stops largely move to `"… (broker)"` suffix.

PR #48 is the instrumentation; it is **not** deployed to production yet (prod still on `2d3ae6b` without that branch).

### 5.5 Stranded-branch plan

`docs/plans/branch-merge-plan.md` (2026-06-18, planning only) still recommends:

1. MC harness — rebase + merge (easy)
2. `phase3-split-cli` — re-derive against current main (high risk; conflicts in `src/cli.py`)

That plan is **~7 weeks stale** relative to #45/#47 and open #48–#50. Revisit only after #50/#48 land.

---

## 6. Cross-cutting risks & hygiene

| Risk | Severity | Detail |
|---|---|---|
| Miss NFP pre-capture | **High** | Calendar; 3 misses kill sample |
| cTrader main CI red | Medium | Blocks honest CI signal for all PRs until #50 |
| Exit-path gate incomplete | Medium | time/stale/weekend paths not live-proven; trailing may need re-exercise post broker-managed |
| FundedHive not at target | Medium | Operational; agent healthy but challenge unfinished |
| Phantom alert stamp today | Low–Med | Inspect Telegram / ops reason |
| Disk 63% + 8.8G build cache | Low | Optional `docker builder prune` |
| Timescale extension lag | Low | crypto-agent PG14 extension 2.19.3 vs 2.29.x |
| Stale local branches/stashes | Low | Cleanup pass |
| Issue #19 out-of-band client | Medium (latent) | Confirm server still has untracked patched client |
| `stable` lagging main (cTrader) | Low | Promote only after deliberate gate |

---

## 7. Recommended action sequence

### Immediate (next 24h)

1. **NFP:** prepare environment for `nfp_forward_capture.py pre` at/after **2026-08-06 12:30 UTC**. Do not miss the first eligible print.
2. **cTrader PR #50:** merge (green, one-line, unblocks main).
3. **cTrader PR #48:** rebase/recheck CI after #50, merge telemetry.
4. **Optional:** PR #49 docs/rsync hygiene after green main.

### This week

5. Re-run exit-path gate report against production journal **after** #48 is deployed (or against a journal copy with new tagging).
6. Inspect phantom alert from 2026-08-05 ops monitor.
7. Decide FundedHive posture: continue challenge vs pause for exit-path completeness (funded capital is not QA).
8. Fast-forward local cTrader `main`; prune `[gone]` branches after review.

### Explicit non-actions

- Do **not** re-arm crypto-agent SOL overlay or sentiment-macro live from this audit.
- Do **not** reopen staking carry or OHLCV structural probes.
- Do **not** start CPI lane until NFP forward interim allows it.
- Do **not** merge `phase3-split-cli` casually — high conflict / behavior surface.

---

## 8. Evidence sources (this audit)

| Source | How |
|---|---|
| crypto-agent git | `git fetch`, branch track, stash list, main `4c5a750` |
| cTrader git | local branches, `git fetch --prune`, worktrees |
| GitHub | `gh pr/issue/run list` both repos |
| Hetzner | `ssh crypto-agent` — compose ps, logs, DEPLOYED_VERSION, fundedhive/paper_pnl/ops/health JSON |
| NFP tool | `uv run python scripts/nfp_forward_capture.py status` |
| Docs | edge-candidates, CTRADER_EXTERNAL_GATE, NFP_FORWARD_GATE, candidate ledger head |

---

## 9. Workflow verification (`full-status-audit`, complete)

Independent parallel scanners (crypto-agent · cTrader · Hetzner · research-gates) + synthesis ran ~4 minutes after the manual audit. **Surfaces scanned: 4/4. Synthesis: ok.**

### Confirmed (no contradiction on the big items)

| Claim | Manual audit | Workflow |
|---|---|---|
| crypto-agent main = prod = `4c5a750`, CI green, no open PRs | yes | yes |
| All crypto prod configs paper | yes | yes |
| NFP window opens 2026-08-06 12:30Z; CSV empty | yes | yes (**critical**) |
| cTrader main CI red; PR #50 green unblocker | yes | yes |
| Staking-carry closed; public-data program terminal | yes | yes |

### New / sharpened findings from workflow (folded in)

1. **`DAY30_REVIEW.md` is still DRAFT** and the evidence-portfolio window ends **~2026-08-06**. Ratify on/about that date (15-minute checklist). Kill gate does **not** fire (NFP OOS YES). This is a second calendar item next to NFP capture prep.
2. **cTrader agent container recreated at `2026-08-05T15:45:32Z`** (compose replace, healthy now, auth OK account `46430710`). Cause unknown — not OOM. Investigate timers/cron/manual compose after this audit; agent recovered with brief trendbar disconnect → yfinance fallback → reconnect.
3. **Phantom alert root cause:** 5 historical `ticket: 0` rows in `paper_pnl.jsonl` (May 2026). `ops_monitor` lacks a persisted `phantom_count`, so baseline never advances → hourly phantom noise. Not five new live phantoms.
4. **Hetzner memory pressure:** 3.7 Gi host, ~1.7 Gi available, **swap ~1.2/2.0 Gi (~60%)**. Not urgent, but prune/build carefully before heavy deploys.
5. **FundedHive / paper_pnl flat ~7 days** while agent still cycles (`executed=0`) — confirm intentional (window / risk) vs blocked path.
6. **Issue #19** (out-of-band `ctrader_client`) re-emphasized before more live risk work.

### Severity split note (expected)

Research scanner rated NFP **critical**; crypto-local rated the same capture **medium** under a “healthy” surface (no outage). Synthesis correctly promoted NFP + day-30 to **P0** because they are calendar-hard, not because production is down.

### Workflow P0 / P1 (synthesis)

**P0**

- NFP `pre` after 2026-08-06 12:30Z → `post` after 2026-08-07 print
- Ratify `docs/evidence_portfolio/DAY30_REVIEW.md` ~2026-08-06

**P1**

- Merge cTrader #50 → re-CI #48/#49
- Investigate cTrader recreate @ 15:45Z
- Fix phantom monitor baseline / archive May ticket=0 rows after review
- Confirm flat FundedHive since 2026-07-29
- Triage issue #19
- Watch swap before heavy jobs
- Continue exit-path accrual (do not scale capital early)
- Keep Conditional-CPI deferred

Artifact: session scratch `full-status-audit-verify.md` (workflow run complete).

---

## 10. One-line bottom line

**Servers are mostly green and both mains are deployed; research is correctly idle on dead lanes; the only things that fail by pure inaction in the next day are the NFP pre-capture and day-30 portfolio ratification — and the only cheap engineering unblock on GitHub is merging cTrader PR #50 so exit-path telemetry (#48) can land.**
