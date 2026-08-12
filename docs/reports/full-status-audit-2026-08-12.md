# Full Status Audit — Local · GitHub · Hetzner

**Date:** 2026-08-12
**Method:** `full-status-audit` workflow (4 parallel scanners + synthesis) plus live
local `git`/`gh`/`nfp_forward_capture.py status` follow-up
**Scope:** `crypto-agent`, `ctrader-trading-agent`, shared Hetzner host `trujillo-server`
**Prior audit:** `docs/reports/full-status-audit-2026-08-05.md` (#160)
**Verdict overall:** Infrastructure is healthy and both mains are deployed. Nothing
is calendar-hard inside 24 hours. The Aug-7 NFP first print was **not captured**
(Wayback `ECONNRESET`); the miss was **booked 2026-08-12** (1 of 3 allowed).
Day-30 ratification is still unsigned. Sentiment-macro's xAI primary
path is 403ing onto DeepSeek.

---

## Executive summary (read this first)

| Priority | Item | Surface | Action needed |
|---|---|---|---|
| **DONE 2026-08-12** | Book NFP 2026-08-07 `MISSED_CAPTURE` | crypto-agent | Booked. Forward CSV now 0 captured / **1 missed**. Next work is Sep-4 pre. |
| **P1** | Schedule Sep-4 NFP pre | crypto-agent | Verify 2026-09-04 on BLS. Window opens **2026-09-03 12:30 UTC**. Restore a working Wayback/archive path (Aug-6 pre died on `Connection reset by peer`). |
| **P1** | Ratify or discard Day-30 | crypto-agent | `docs/evidence_portfolio/DAY30_REVIEW.md` is uncommitted, unsigned, and still says "first clean print pending". Sign without editing locked gates, or refresh the NFP row then sign. |
| **P1** | sentiment-macro xAI 403 | Hetzner | Hourly `POST api.x.ai/v1/chat/completions` → 403, then DeepSeek 200. Confirm key/plan; decide if fallback is an accepted recording path. |
| **P1** | Phantom-ticket restamp | Hetzner cTrader | `ops_monitor` restamps `last_phantom_alert` every 5m from **five May 2026 `ticket:0` rows**. Baseline or ignore them. Aug-11 ops-guard deploy did not persist a phantom baseline. |
| **P1** | Accrue remaining exit paths | cTrader (external) | `partial_tp` / `stale_exit` ready; need **2× `time_stop`** and **4× `weekend_flatten`**. Challenge-ready criteria unmet. Do this in `ctrader-trading-agent` only. |
| **P1** | Watch Hetzner swap | Hetzner | Swap 1.0 / 2.0 Gi (50%). Optional `docker builder prune` of 5.2G reclaimable cache. Disk 59% — improved vs 63% on Aug-5. |
| **P2** | Local hygiene (both repos) | local | Gone branches, stashes, implicit paper flags, untracked `.omo/` / merge plan |
| **P2** | Close stale issue #14 | GitHub cTrader | ForexGPT paper-first request, opened 2026-04-22, no comments |
| **OK** | crypto-agent git + prod | local + Hetzner | `main` = `origin/main` = `/opt/crypto-agent` = `e0b9daa` (#160). No open PRs/issues. CI last success 2026-08-05. All four agents **paper**. |
| **OK** | cTrader git + prod | local + Hetzner | `main` = `origin/main` = `DEPLOYED_VERSION` = `6707d70` (`v2026-08-11-ops-guard-hardening-stable`). CI green after #50–#53. `stable` now equals `main`. |
| **WATCH** | FundedHive | Hetzner cTrader | `peak_equity=5000`, `target_reached=false`, `profitable_days_count=8`. Last close **today** 16:20Z EURUSD buy SL −1.00R (live). Agent healthy, positions empty. |
| **CLOSED** | cTrader PRs #48/#49/#50 | GitHub | Merged 2026-08-05. Main was red after #47–#49; #50 repaired harness path. Subsequent #51/#52/#53 also green. |
| **CLOSED** | Staking-carry / public OHLCV | research | Unchanged. Do not reopen. |

**P0 is empty.** The research scanner labeled the Aug-7 miss `critical`; synthesis kept
it P1 because the print is already past, the next window is 2026-09-03, and
production is not broken.

---

## Delta vs 2026-08-05 audit

| Item | Aug-5 | Aug-12 |
|---|---|---|
| crypto-agent HEAD | `4c5a750` (#159) | `e0b9daa` (#160) — this prior audit |
| cTrader HEAD / deploy | `2d3ae6b` (Jul-27) | `6707d70` (Aug-11 ops-guard) |
| cTrader `stable` | lagged at `52dd271` | **matches main** `6707d70` |
| Open cTrader PRs | #48 #49 #50 | **none** |
| Main CI (cTrader) | red (harness path) | **green** since #50 |
| NFP first print | window opening in ~21h | **missed**; booked 2026-08-12 (1/3) |
| Day-30 review | DRAFT, window ~tomorrow | still unsigned; now stale vs missed print |
| FundedHive last close | 2026-07-29 | **2026-08-12** (trading resumed) |
| Hetzner disk | 63% | **59%** |
| New prod issues | phantom stamp, swap | **+ xAI 403** on sentiment-macro; phantom loop still every 5m |

---

## 1. Host / Hetzner production

Both stacks run on the same machine: `trujillo-server`
(`ssh crypto-agent` / `ssh -i ~/.ssh/hetzner_deploy root@46.225.119.221`).

| Field | Value |
|---|---|
| Disk `/` | 43G / 75G (**59%**), inodes 14% |
| RAM | 3.7 Gi host, ~1.6 Gi available |
| Swap | **1.0 / 2.0 Gi (50%)** — watch |
| Docker build cache | 5.2G reclaimable (was 8.8G on Aug-5) |
| Container health | all crypto-agent + cTrader + `manual-trading-agent` healthy; `restart=0`, `oom=false` |

### 1.1 Deploy pin alignment

| Stack | Server pin | `origin/main` | Match? |
|---|---|---|---|
| crypto-agent `/opt/crypto-agent` | `e0b9daa` — *full-status-audit 2026-08-05 (#160)* | `e0b9daa` | **Yes** |
| cTrader `/opt/ctrader-trading-agent` | `6707d70` / `v2026-08-11-ops-guard-hardening-stable` @ 2026-08-11T22:05:14Z | `6707d70` | **Yes** |

crypto-agent containers started **2026-08-05T16:07Z** (7d, post-#160 Deploy).
cTrader agent up **~21h** after the Aug-11 ops-guard deploy. No `.git` under
`/opt/ctrader-trading-agent` (file-copy deploy).

### 1.2 Production log / state notes

**crypto-agent**

- Four strategy services + Timescale / Prometheus / Grafana: healthy.
- All four agents remain **paper / disarmed** (overlay + sentiment explicit;
  sparse `mode: paper`; panic `trading_execution.enabled: false`).
- `agent_sentiment_macro`: recurring `POST api.x.ai/v1/chat/completions` **HTTP 403**,
  then DeepSeek fallback 200. **13/13** in the last 800 log lines (~hourly).
  This agent is kept up specifically to record the xAI sentiment feed — the primary
  path is failing.
- Timescale tail: leftover operator ad-hoc SQL errors through 2026-08-11 18:01Z
  (`trading` DB missing, columns `time`/`pnl_net`/`pnl_gross`) — not agent runtime.

**cTrader**

- `health_monitor_state.local.json` @ 19:30Z: `status=ok`, `health=healthy`,
  `fail_cycle=0`, `err=0`, `lock=false`, `runtime_fresh=true`, auth expiry ~25.9d.
  Non-local `health_monitor_state.json` is stale since **2026-04-07** — ignore it;
  `.local` is the live file.
- `ops_monitor_state.json`: `last_run` = `last_phantom_alert` = **2026-08-12T19:30:01Z**.
  Log shows `phantom_new=5` every 5 minutes. Still the **five historical `ticket:0`
  `paper_pnl` rows from 2026-05-13..15**, not new live phantoms. The Aug-11 ops-guard
  deploy (`#51`/`#53`) did **not** persist a phantom baseline.
- `fundedhive_state.json` (live mtime 2026-08-12T16:20Z):

  | Field | Value |
  |---|---|
  | peak_equity | 5000.0 |
  | profit_target | 0.08 |
  | target_reached | false |
  | profitable_days (raw) | 20 |
  | profitable_days_count | 8 |
  | daily_pnl_r | −1.0 |
  | current_day | 2026-08-12 |
  | open_tickets | {} |

- Last journal close: ticket **4859686** EURUSD **buy** `sl (broker)` net_r=**−1.0049**
  at 2026-08-12T16:20:27Z (`agent_initiated=false`). Prior: EURGBP 4669848 +0.14/+0.28
  on 2026-08-06. `paper_positions` empty.

---

## 2. crypto-agent — GitHub & local

### 2.1 Git

| Item | State |
|---|---|
| Remote | `https://github.com/Trujillofa/crypto-agent` |
| Branch | `main` @ `e0b9daa` |
| vs `origin/main` | **0 ahead / 0 behind** |
| Open PRs | **none** |
| Open issues | **none** |
| CI on main | last success 2026-08-05 (run `31023511104`); Deploy same day (`31023624272`) |
| Last Deploy failures | 2026-07-06 |
| Working tree | dirty: `M docs/evidence_portfolio/DAY30_REVIEW.md`; `?? research/nfp_forward/` |

### 2.2 Recent merges (since prior audit)

| PR | Title | Merged |
|---|---|---|
| #160 | docs(reports): full multi-surface status audit 2026-08-05 | 2026-08-05 |
| #159 | Record cTrader exit-path measurement; close staking-carry | 2026-07-27 |
| #158 | NFP forward gate: capture routine spec + capture tool | 2026-07-27 |

### 2.3 Uncommitted / local noise

| Path | Notes |
|---|---|
| `docs/evidence_portfolio/DAY30_REVIEW.md` | Modified +123/−24 vs HEAD. Status still **READY FOR RATIFICATION**; NFP row still says first clean print pending (filled 2026-08-05). Kill gate does not fire. |
| `research/nfp_forward/` | Untracked runner: `CHECKLIST_2026-08-07.md`, `run_pre_2026-08-07.sh`, `pre-2026-08-07.log` (the failed Wayback pre). |
| `docs/ctrader-exit-path-gate-finding` | Local branch tracks deleted origin; empty vs `4c5a750` (squash #159). Safe to delete. |
| 4 stashes | `stash@{0}` 2026-06-20 mNAV WIP; `{1}` 2026-06-17 empty-ish; `{2}` 2026-03-19 report rewrite; `{3}` 2026-03-18 old agent2/btc-4h configs. Review/drop; do not apply blindly. |

### 2.4 Production agents (config truth)

Unchanged from Aug-5. All four production strategy services are **disarmed / paper**:

| Service | AGENT_ID | mode | trading_execution.enabled |
|---|---|---|---|
| sol 1h trend pullback overlay | `sol-1h-trend-pullback-overlay-live` | paper | false |
| sentiment macro | `sentiment-macro-bot` | paper | false |
| sol sparse | `sol-trend-pullback-sparse` | paper | *omitted* (defaults false) |
| sol panic-block | `sol-4h-panic-block-paper` | *omitted* (defaults paper) | false |

Filename `overlay_live` is a naming hazard only. Sparse/panic implicit flags are
low-severity hygiene.

---

## 3. NFP forward gate — **highest-priority ledger item**

| Field | Value |
|---|---|
| Gate doc | `docs/evidence_portfolio/NFP_FORWARD_GATE.md` — **IN FORCE** (signed 2026-07-21) |
| OOS verdict | **YES** (PR #154) — stands |
| Capture tool | `scripts/nfp_forward_capture.py` — exists (PR #158) |
| Forward CSV | `data/macro_events/nfp_good_news_forward.csv` — **0 captured / 1 missed** (created 2026-08-12) |
| Pending stash | none (`pending_capture.json` never written) |
| First eligible print | **2026-08-07 12:30 UTC** — not captured; **`NFP_MISSED_CAPTURE` booked 2026-08-12** |
| What happened | Automated `pre` fired 2026-08-06 12:32 UTC via `research/nfp_forward/run_pre_2026-08-07.sh`. Wayback Save Page Now of Investing.com NFP consensus: **`[Errno 104] Connection reset by peer`**. No `post`. Miss booked with that note. |
| Next release | **2026-09-04 12:30 UTC** (verify on [BLS empsit schedule](https://www.bls.gov/schedule/news_release/empsit.htm)) |
| Next window | **2026-09-03 12:30 UTC → 2026-09-04 12:30 UTC** |
| Later prints | 2026-10-02, 2026-11-06, 2026-12-04 |
| Kill risk | ≥3 `MISSED_CAPTURE` rows cap the sample. Aug-7 consumed **1 of 3**. |

### Required human actions

1. **Miss booked** (2026-08-12):

   ```bash
   uv run python scripts/nfp_forward_capture.py miss --release-date 2026-08-07 \
     --note "pre failed 2026-08-06T12:32Z: Wayback Save Page Now Connection reset by peer"
   # -> 0 captured, 1 missed
   ```

2. **Before 2026-09-04 12:30 UTC**, after the window opens on **2026-09-03**:

   ```bash
   uv run python scripts/nfp_forward_capture.py pre
   ```

   The Aug-6 failure was Wayback, not the local tool. Confirm archive path works
   (retry / alternate snapshot) *before* the window, not during it.

3. After the BLS print:

   ```bash
   uv run python scripts/nfp_forward_capture.py post --actual <n> --consensus <n>
   ```

4. If pre fails again:

   ```bash
   uv run python scripts/nfp_forward_capture.py miss --release-date 2026-09-04
   ```

Decision rule (unchanged): after **8 hot trades** or **14 prints**; 3 misses cap
the sample.

---

## 4. Research / edge-candidate board

Sources: `docs/reports/edge-candidates-2026-07-27.md`,
`docs/evidence_portfolio/NFP_FORWARD_GATE.md`,
`docs/evidence_portfolio/CTRADER_EXTERNAL_GATE.md`,
`docs/reports/autoresearch-candidate-ledger.md`.

| # | Candidate | Class | Status | Next |
|---|---|---|---|---|
| 1 | NFP good-news event lane | Measured | **ACTIVE — Aug-7 miss booked (1/3)** | Arm Sep-4 pre; restore Wayback |
| 2 | Conditional CPI analog | Unpriced | Deferred | Only after NFP forward confirms. Running it now is gate-shopping. |
| 3 | Delta-neutral staking carry | Unpriced | **CLOSED 2026-07-27** | Do not reopen; ETH −1.59%, SOL −1.46% vs RF; 8% SOL still −0.76% vs +1.0% gate |
| 4 | Access / size-is-edge ops | Structural | Optional business track | A1 Phase-0 (2026-07-14): 0 programs actionable; Legion weekly watch only; $25/hr EV floor |
| 5 | Scale cTrader FX system | Measured | Active external | Accrue remaining exit-path execs; FundedHive rules |
| 6 | Maker-rebate MM C-tier | Structural | Closed for accessible profile | Needs named venue + written rebate + new Gate 0 |
| — | On-chain flow wildcard | Unpriced | Low prior | Cheap probe only if deliberately opened; expect `NO_PULSE` |

**Public-data OHLCV program:** TERMINAL (2026-06-19 / 06-23 / 06-24). Path 2
illiquid-venue closed at Gate 0 economics. Ledger: OHLCV, funding-MR, unstaked
carry, unlocks, mNAV, Polymarket, OFI, XS-momentum, illiquid taking all **CLOSED**.
SOL overlay `DEPLOY_LIVE` is **superseded** (service is paper). Probe #2
`DELETED_NOT_NAMED`.

**Day-30 portfolio:** `DAY30_REVIEW.md` is ratification-only. Kill gate does **not**
fire (NFP OOS YES). Human signature and allocation 1–5 still blank after the
~2026-08-06 window. Do not edit locked gates when signing.

---

## 5. cTrader trading agent — GitHub & local

### 5.1 Git

| Item | State |
|---|---|
| Branch | `main` @ `6707d70` |
| vs `origin/main` | **0 ahead / 0 behind** |
| `stable` | **`6707d70`** (caught up; was lagging on Aug-5) |
| Open PRs | **none** |
| Open issues | **#14** only (ForexGPT, 2026-04-22, no comments/labels) |
| Working tree | clean tracked files; untracked `.omo/` and `docs/plans/branch-merge-plan.md` |

### 5.2 Merges since Aug-5 (the previous P0/P1 board is done)

| PR | Title | CI at merge | Landed |
|---|---|---|---|
| **#50** | fix(cli): repair backtest harness path | all green | 2026-08-05 — unblocked main |
| **#49** | docs: rsync exclude + .gitignore | Test **red** (inherited harness) | 2026-08-05 — merged anyway |
| **#48** | Exit-path gate telemetry | Test **red** (same) | 2026-08-05 — merged anyway |
| **#51** | hotfix: ticket-zero diagnostics (#19) | green | 2026-08-11 |
| **#52** | ops: version drift-sentinel units | green | 2026-08-11 |
| **#53** | fix(ops): thermo-review F1–F10 | green | 2026-08-11 — current pin |

**Root cause (historical, not current):** PR #47 moved
`scripts/backtest_harness.py` → `scripts/backtest/`; `src/cli.py` still pointed at
the old path. #50 repaired it. Latest main run `31540643806` (merge #53,
2026-08-11): Lint / Test / Validate Infra Config / Build all **success**.

Issues #19 (out-of-band `ctrader_client`) and #20 (promote/deploy backlog) are no
longer open — #51 brought ticket-zero diagnostics into git.

### 5.3 Local branches / hygiene

| Branch | vs main | Remote | Action |
|---|---|---|---|
| `fix/live-safety-hardening` | +1 | still tracks origin | review independently |
| `phase3-split-cli` | ahead 1 | origin | high-risk; do not merge casually |
| `trial-mc-rebase` | ahead 8 / behind 1 | `claude/monte-carlo…` | optional MC harness |
| `trial-p3-merge` | — | tracks `phase3-split-cli` | leftover trial |
| `feature/symbol-watchdog` | — | **gone** | prune |
| 3 stashes | watchdog-deploy / protocol-volume / backtest-experiment | local | review/drop |
| `.omo/` | untracked notes | local | add to `.gitignore` if they stay local |
| `docs/plans/branch-merge-plan.md` | 2026-06-18, ~8 weeks stale vs #45–#53 | untracked | commit or discard |

### 5.4 Exit-path gate (external to crypto-agent)

Canonical write-up: `docs/evidence_portfolio/CTRADER_EXTERNAL_GATE.md` (boundary
note only). Work belongs in `ctrader-trading-agent`.

| Path | Status (research-gate scanner, consistent with Day-30 draft) |
|---|---|
| `partial_tp` | **ready** |
| `stale_exit` | **ready** |
| `trailing_stop` | broker-side (not agent-managed) |
| `time_stop` | needs **2** more paper/demo execs |
| `weekend_flatten` | needs **4** more |

Telemetry (#48) is now **on main and on the running pin** (`6707d70` includes it
via the #48→#53 stack). Challenge-ready criteria still **unmet**. Funded challenge
is not QA.

---

## 6. Cross-cutting risks & hygiene

| Risk | Severity | Detail |
|---|---|---|
| NFP Aug-7 miss booked | **High** (closed) | Ledger row committed; 1 of 3 miss budget used |
| Sep-4 pre without working Wayback | **High** | Same failure mode as Aug-6 |
| Unsigned Day-30 | Medium | Allocation not ratified; draft stale vs missed print |
| xAI 403 on sentiment-macro | Medium | Primary reason that paper agent exists |
| Phantom alert loop | Medium | 5-minute restamp; not new live phantoms |
| Exit-path gate incomplete | Medium | time/weekend still short; do not scale capital |
| FundedHive not at target | Medium | Operational; agent healthy, last trade today was −1R |
| Swap 50% | Medium | Watch before heavy deploys |
| Implicit paper flags (sparse/panic) | Low | Defaults are safe; make explicit |
| Stale local branches/stashes | Low | Cleanup pass |
| Stale issue #14 | Low | ForexGPT backlog |
| Timescale ad-hoc SQL noise | Low | Operator error, not agent |

---

## 7. Recommended action sequence

### This week (P1)

1. **NFP miss booked** 2026-08-12 (1 of 3). No further miss action for Aug-7.
2. **Verify Sep-4** on BLS; schedule `pre` for 2026-09-03T12:30Z; smoke-test Wayback
   *before* that window.
3. **Sign or refresh+sign** `DAY30_REVIEW.md`. Do not edit locked gates. Then commit
   the review (and optionally the `research/nfp_forward/` runner + this report).
4. **Check xAI key/plan** on the sentiment-macro container. Decide whether DeepSeek
   fallback is an accepted recording path or whether the agent should stop pretending
   it is capturing xAI.
5. **Baseline or ignore** the five May `ticket:0` `paper_pnl` rows so `ops_monitor`
   stops restamping `last_phantom_alert`.
6. **Accrue** 2× `time_stop` and 4× `weekend_flatten` in `ctrader-trading-agent`
   (paper/demo — not funded capital).
7. **Watch swap**; optional `docker builder prune` of 5.2G cache.

### Backlog (P2)

8. Make sparse/panic paper flags explicit.
9. Delete `docs/ctrader-exit-path-gate-finding`; prune cTrader gone/trial branches.
10. Inspect or drop stashes on both repos.
11. Close or explicitly keep issue #14.
12. Commit or discard `docs/plans/branch-merge-plan.md`; gitignore `.omo/`.

### Explicit non-actions

- Do **not** re-arm crypto-agent SOL overlay or sentiment-macro live from this audit.
- Do **not** reopen staking carry or OHLCV structural probes.
- Do **not** start the CPI lane until NFP forward interim allows it.
- Do **not** merge `phase3-split-cli` casually — high conflict / behavior surface.
- Do **not** treat the funded challenge as QA for remaining exit paths.

---

## 8. Evidence sources (this audit)

| Source | How |
|---|---|
| Workflow | `.grok/workflows/full-status-audit.rhai` — 4/4 surfaces + synthesis, ~3m41s |
| crypto-agent git | `git status`, `stash list`, `branch -vv`, `log` |
| cTrader git | same + untracked listing |
| GitHub | `gh pr/issue/run list` both repos |
| NFP tool | `uv run python scripts/nfp_forward_capture.py status` |
| Failed pre | `research/nfp_forward/pre-2026-08-07.log` |
| Day-30 | `docs/evidence_portfolio/DAY30_REVIEW.md` (working tree) |
| Hetzner | workflow scanner via `ssh crypto-agent` — compose, HEAD/`DEPLOYED_VERSION`, fundedhive/paper_pnl/ops/health JSON, log tails |
| Docs | edge-candidates, CTRADER_EXTERNAL_GATE, NFP_FORWARD_GATE, candidate ledger |

---

## 9. Workflow verification (`full-status-audit`, complete)

Independent parallel scanners (crypto-agent · cTrader · Hetzner · research-gates)
+ synthesis. **Surfaces scanned: 4/4. Synthesis: ok.** Display handle:
`full-status-audit`. Artifact: session scratch `full-status-audit-verify.md`.

### Confirmed (no contradiction on the big items)

| Claim | Local follow-up | Workflow |
|---|---|---|
| crypto-agent `main` = prod = `e0b9daa`, CI green, no open PRs | yes | yes |
| All crypto prod configs paper | yes | yes |
| NFP 0/0 at scan; next window 2026-09-03/04; Aug-6 Wayback fail | yes | yes (miss booked after scan) |
| cTrader `main` = prod = `6707d70`; CI green after #50–#53 | yes | yes |
| Staking-carry closed; public-data program terminal | yes | yes |
| Day-30 unsigned / uncommitted | yes | yes |

### Severity split (expected)

| Surface | Workflow status | Why |
|---|---|---|
| research-gates | `critical` | Aug-7 print unrecorded (gate ledger) |
| hetzner-production | `degraded` | xAI 403 + phantom loop + swap watch; containers themselves healthy |
| crypto-agent-local-github | `watch` | same NFP fact, no outage |
| ctrader-local-github | `healthy` | mains green, PRs landed |

Synthesis correctly **did not** promote NFP to P0: the print is past, next window
is three weeks out, production is not broken.

### Contradictions the synthesizer flagged (resolved in this report)

1. **NFP severity:** research=`critical`, local=`high`. Same facts. This report
   treats it as **P1 ledger**, not a production outage.
2. **Day-30:** local says stale vs missed print; research says READY FOR
   RATIFICATION / sign without editing locked gates. Both true: sign is allowed,
   but the NFP track row is factually stale until the miss is booked and the
   sentence is updated *or* the human signs knowing the print was missed.
3. **xAI 403 and phantom loop** appear only on Hetzner (prod logs), not in the
   local GitHub scans — expected.
4. **cTrader CI red on #48/#49** is historical. `origin/main` is green.

---

## 10. One-line bottom line

**Both mains are deployed and containers are healthy; the Aug-5 P0 board (NFP
pre-capture, Day-30 window, cTrader #50) is now a booked Aug-7 miss (1/3), an
unsigned Day-30 draft, and finished GitHub work. This week is arm September,
ratify the portfolio, fix or accept the xAI 403, and silence the phantom loop —
without reopening dead research lanes or re-arming paper agents.**
