# Strategy-Drought Re-Baseline — 2026-10-22

**Status:** complete, read-only analysis. No live configuration was changed.

**Scope.** This re-baselines the "no signals" drought reported by the
delegated task `deleg_e97d9c81` (report produced 2026-10-20 ~21:12 UTC,
reviewer-challenged for review). The delegated report was not written to a
file — it survives only in a truncated subagent log
(`~/.hermes/cache/delegation/live/deleg_e97d9c81/task-0.log`), so it could
not be reviewed as-is. Everything below was re-derived independently from
the live database and the durable event logs.

**Headline correction.** The delegated report's central claim — *"no
`signal_received` since 2026-10-02T11:44:20Z"* — was already **stale by two
days** when it was written. A further signal landed on 2026-10-20T21:09:33Z.
The current last-signal time for the panic-block agent is that one. Any
conclusion drawn from the "no signal since 2026-10-02" premise is invalid.

---

## 1. Current signal state (measured, not reported)

Durable event logs on the shared agent-data volume, read directly from the
running containers:

| agent | log file | `signal_received` | last signal |
|---|---|---|---|
| `sentiment-macro-bot` | `event_log_sentiment-macro-bot.jsonl` | 140 | 2026-08-14T10:35:32Z |
| `avax-4h-ma` | `event_log_avax-4h-ma.jsonl` | 23 | 2026-05-11T23:37:00Z |
| `sol-4h-panic-block-paper` | `event_log_sol-4h-panic-block-paper.jsonl` | 13 | **2026-10-20T21:09:33Z** |
| `eth-4h` | `event_log_eth-4h.jsonl` | 6 | 2026-04-06T03:50:45Z |
| `btc-4h` | `event_log_btc-4h.jsonl` | 5 | 2026-04-06T03:50:45Z |
| `default` | `event_log_default.jsonl` | 3 | 2026-04-08T05:48:15Z |
| `agent2` | `event_log_agent2.jsonl` | 2 | 2026-04-06T13:58:49Z |
| `sol-trend-pullback-sparse` | `event_log_sol-trend-pullback-sparse.jsonl` | 2 | **2026-05-05T18:59:20Z** |
| `sol-1h-trend-pullback-overlay-live` | `event_log_sol-1h-trend-pullback-overlay-live.jsonl` | 1 | 2026-08-28T19:54:09Z |

Totals: 195 `signal_received` + 156 `signal_ignored` = **351 signal
events**, which is what the production drift sentinel (2026-10-22T10:00:03Z)
reports as "Signal events recorded: 351". The sentinel counts both event
types; the table above counts only `signal_received`.

The sentinel reports exactly one warning — `WATCH_SERVICE_SIGNAL_STALE` for
`agent_sol_sparse` (4071 h old against a 731 h threshold). The sentinel is
working correctly; the warning is real.

**The 1h live-money agent is worth calling out.** It produced exactly one
signal (2026-08-28T19:54:09Z) and it was fully executed: `guard_passed`
→ `staged_order_created` → `order_filled` (BUY 0.21248 SOLUSDT @ 103.54,
futures, 3x, $22 notional) → the following day
`order_filled` SELL @ 103.68 with `realized_pnl` +0.0209 USDT, closed
`paper_close`. The agent has started 100 times since 2026-06-03 (one
`system_startup` event per container start, the most recent at
2026-10-22T10:36:27Z — the redeploy from PR #205) with
`kill_switch_active: False` and `indicator_ready: True`. It is healthy; it
simply has not been triggered since August.

So there are **two different problems** that the delegated report conflated:

1. **panic-block agent** — *not* in a drought. 13 signals, the most recent
   1.5 days before this re-baseline.
2. **sparse agent** (`sol-trend-pullback-sparse`) — a genuine, long-running
   drought. 2 signals ever, both in April/May 2026.

Everything below concerns the sparse agent, because that is the one that is
actually dry.

---

## 2. The sparse agent's drought is real and it is NOT the VWAP window

The delegated report attributed the drought to a "33-day rolling VWAP"
making the strategy's 3 % VWAP-distance gate unreachable. **That
attribution is wrong.** Measured directly:

```
SOLUSDT 4h — how often is close within 3% of the STORED vwap?
  month       bars  within3%    pct
  2026-01      186         0     0.0%
  2026-02      162         0     0.0%
  2026-03      186         0     0.0%
  2026-04      180         0     0.0%
  2026-05       43         0     0.0%
  2026-06      175        44    25.1%
  2026-07      186        63    33.9%
  2026-08      186        68    36.6%
  2026-09      180        12     6.7%
  2026-10      129        25    19.4%
```

The window is *not* 33 days and the gate is *not* unreachable. The stored
VWAP was structurally wrong for the first five months of 2026 and correct
afterwards — because of the window, but not in the way claimed.

### Which window produced the stored VWAP?

Reproducing the stored value from the same OHLCV at several candidate
window lengths:

```
  bar                    stored        w=200        w=251        w=751   verdict
  2026-03-01 00:00    126.8437      91.3748      96.0689     126.7762   ~w=751
  2026-03-10 08:00    118.9315      84.6208      89.9092     118.9108   ~w=751
  2026-05-04 00:00     96.9782      84.2745      84.7642      96.9631   ~w=751
  2026-06-26 20:00     71.5406      71.3415      73.8183      82.4326   ~w=200
  2026-08-17 16:00     75.2174      75.2191      75.9043      78.6799   ~w=200
  2026-10-02 04:00    109.3399     109.4189     106.2154      87.1173   ~w=200
```

The pre-June-2026 rows were written with a **751-bar (125-day) VWAP** — the
legacy `compute_historical_indicators.py` default. The 200-bar live writer
took over on **2026-06-02** for 4h, and the 1h series flipped
`w751 -> w200` on 2026-06-18, `w200 -> w751` on 2026-08-13, and back to
`w200` on 2026-10-20 (the last one being this repository's PR #204 backfill).

A 125-day VWAP on SOL was pinned near ~127 while price traded at ~88. The
price could not be within 3 % of it for five straight months. **The
legacy-writer VWAP was the drought's enabling condition** — but it is a
*data* defect, already fixed for all future writes by PR #204. It is not
the reason the agent is dry *today*.

---

## 3. Why the sparse agent is dry *today*

### 3.1 The strategy conditions are simply not met

Replaying `TrendPullbackStrategy` over SOLUSDT 4h with live-equivalent
(200-bar recomputed) indicators, using the agent's real config, produces
**zero BUY signals for the entire history** — and the two real signals in
the log cannot be reproduced from 4h bars alone either.

Decomposing the strategy's `in_uptrend` gate (which gates everything):

```
month      bars close>e200  e50>e200  ts>=.008  atr>=.008   IN_UPTREND
  2026-01     186         97        94        83        186           70
  2026-02     162          0         0         0        162            0
  2026-03     186         38        17         0        186            0
  2026-04     180         68        67         0        178            0
  2026-05      43         17        10         4         38            4
  2026-06     175         16         0         0        168            0
  2026-07     186        130       164       121        179          106
  2026-08     186        135       106        76        129           75
  2026-09     180        180       180       180        175          175
  2026-10     129         44        75        65        128           43
```

The `trend_strength_pct >= 0.008` condition (i.e. `ema_50` at least
0.8 % above `ema_200`) fails outright in February, March, April and June
2026, and is marginal in May. In July–September it passes comfortably
(106 / 75 / 175 bars in uptrend) and yet **still no signal**, because the
remaining gates (`near_ema50` ≤ 2 %, `near_vwap` ≤ 3 %, and a
*stateful* recovery requiring RSI, MACD histogram and price all to be
rising **relative to the previous evaluation**) are not satisfied on
4h-bar granularity.

This is the crux: the strategy is a **trend pullback** strategy. It wants
a pullback into EMA50/VWAP *within* an established uptrend, followed by a
turn. On a 4h series, SOL in 2026 delivered uptrends but almost never a
*pullback-and-recover* shape, and certainly not with the one-bar
confirmation the stateful `previous_*` comparisons demand.

### 3.2 The live agent sees the forming candle, which is why the replay misses its 2 real signals

The two logged signals have reason strings that only make sense with
*intrabar* resolution:

```
2026-04-25T15:17:33  price=86.47 ema50=85.99 ema200=85.03 vwap=84.80 rsi=56.64
                     macd_hist=0.0252 atr_pct=0.0105
2026-05-05T18:59:20  price=85.47 ema50=84.46 ema200=83.76 vwap=84.44 rsi=62.48
                     prev_rsi=60.03 macd_hist=0.1113 prev_macd_hist=0.0728
```

`prev_rsi=60.03` and `prev_macd_hist=0.0728` are *smaller* than the
current values — the recovery condition `rsi > previous_rsi AND
macd_hist > previous_macd_hist AND close > previous_close` fired. Those
"previous" values are the **previous 60-second evaluation cycle**, not the
previous 4h bar. The live agent recomputes the 200-bar window on every
cycle using the *forming* 4h candle, so RSI/MACD/close all ratchet upward
*within* the bar as 1h sub-candles land. That intra-bar ratchet is what
satisfies `recovery_ok`; a bar-to-bar replay can never reproduce it.

This is the same forming-candle mechanism already documented for the
panic-block agent in the indicator-vintage report (§5b), now shown to be
load-bearing for the sparse strategy's *entry* logic, not just for its
stored values.

### 3.2b The live agent's own values confirm the 200-bar window

The agent's logged reason string carries the indicator values it actually
used. Comparing those against a 200-bar recompute of the same bar:

```
2026-04-25 08:00 bar
  AGENT live vwap        = 84.798693
  200-bar recompute vwap = 84.803215   MATCH
  (stored row for that bar: vwap = 98.7003  <- legacy 751-bar writer)

2026-05-05 12:00 bar
  AGENT live vwap        = 84.440000
  200-bar recompute vwap = 84.440935   MATCH
  (stored row for that bar: vwap = 96.5744  <- legacy 751-bar writer)
```

So the live path has always used the 200-bar window; the *stored historical
rows* are the ones carrying the legacy 751-bar VWAP. This is the same
split the indicator-vintage diagnosis established, seen from the strategy
side: the live agent and the historical writers disagreed, and only the
live agent was ever canonical.

It also means the strategy's own gating ran on **correct** 200-bar values
all along. The drought is not caused by the strategy seeing bad indicators.

### 3.3 Consequence

The sparse agent's drought is **substantially by design**:

- The strategy is a pullback-recovery strategy with a demanding
  stateful confirmation and a 3 % VWAP-distance gate.
- SOLUSDT 4h in 2026 produced uptrends but few qualifying pullbacks.
- The agent is `mode: paper`, and the config comment states it was
  reverted to paper on 2026-04-25 because the spot account had $0
  balance. Its 2 signals are both from that window.

The sentinel's `WATCH_SERVICE_SIGNAL_STALE` warning asks exactly the right
question — *"is this sparse strategy idle by design or did conditions
change materially?"* — and the answer, on the evidence, is **mostly by
design**, with one genuine data defect (§2) that has already been fixed
for all future writes.

---

## 4. What is NOT established

- Whether the *thresholds* are well chosen. This analysis shows the gates
  are not being met; it does not show that loosening them would produce
  profitable trades. No backtest was run as part of this re-baseline,
  and the decision to loosen `vwap_pullback_distance_pct` or
  `min_trend_strength_pct` is a strategy decision, not a bug fix.
- Whether `BTCUSDT 4h` or other symbols would behave differently. The
  analysis is SOLUSDT-specific because that is the agent's only pair.
- The exact intra-bar path. The forming-candle explanation is inferred
  from the signal reason strings (which contain `prev_rsi`/`prev_macd_hist`
  values that only exist at sub-bar resolution) and from the ingest code
  path; it was not captured from a live tick trace.

---

## 5. Recommended disposition

1. **Do not change the strategy thresholds** to silence the sentinel. The
   drought is a strategy-fit observation, not a defect in the plumbing.
2. **Re-baseline the sentinel's expectation** for `agent_sol_sparse`. A
   731 h threshold against a strategy that produced 2 signals in 5.5
   months will warn forever. Either widen the threshold for this agent
   explicitly, or exclude it with a documented reason, so the warning keeps
   meaning something for the agents that are supposed to be active.
3. **Treat the legacy-VWAP finding as a data-history issue, not a live
   issue.** PR #204 fixed the writer; the pre-2026-06-02 rows are still
   wrong in the table and will remain wrong until (and unless) the
   historical backfill is run under review with a tested rollback.
4. **Fix the forming-candle ingest separately** for both the 4h agents.
   It is a real defect: the REST poller writes `payload[-1]` (the forming
   candle) and never revisits the row after the candle closes. It affects
   the 4h paper agents, not the 1h live-money path, and it should be
   dispositioned explicitly rather than left implicit.

---

## 6. Reproducing this

All findings are reproducible read-only from a running agent container:

```bash
docker exec -i crypto-agent-agent_sol_sparse-1 python - <<'PY'
import json, glob
for f in glob.glob("/app/data/event_log_*.jsonl"):
    sigs = []
    for line in open(f, errors="replace"):
        line = line.strip()
        if not line: continue
        try: e = json.loads(line)
        except Exception: continue
        if (e.get("type") or e.get("event_type")) == "signal_received":
            sigs.append(e.get("ts") or e.get("timestamp"))
    print(f, len(sigs), max(sigs) if sigs else None)
PY
```

The VWAP-window classification is reproducible with
`~/.hermes/cache/scratch/probe_vwap_boundary.py` (copied into the
container as `/tmp/probe_vwap_boundary.py`).

---

## 7. Relationship to the delegated report

The delegated report `deleg_e97d9c81` reached a conclusion ("the strategy
is idle by design, the VWAP window is the cause") that is **directionally
right on the conclusion but wrong on the mechanism and wrong on its
premise**:

| claim | verdict |
|---|---|
| "no signal since 2026-10-02T11:44:20Z" | **false** — a signal landed 2026-10-20T21:09:33Z |
| the VWAP window is the drought cause | **partly** — a 751-bar (125-day) VWAP, not 33 days; it explains Jan–May 2026, not the current drought |
| the agent is idle by design | **supported**, and now evidenced rather than asserted |
| the strategy's gates are unreachable | **false** — the gates are reachable; the *market* has not met them |

This re-baseline supersedes it.
