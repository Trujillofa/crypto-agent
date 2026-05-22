# Sentiment-Macro Real-Money Decision Gate

**Date**: 2026-05-20
**Agent**: `agent_sentiment_macro` (config: `settings.sentiment_macro.yaml`)
**Analysis window**: 2026-04-20 → 2026-05-20 (live USDT-M futures only)
**Data source**: Binance `/fapi/v1/income` (REALIZED_PNL + COMMISSION + FUNDING_FEE) and `/fapi/v2/account` — **real money, not the DB**
**Supersedes**: `sentiment-macro-decision-gate-20260413.md` (which was computed on pre-live/testnet trades)

---

## TL;DR

Real-money net is **-$1.53** over 25 closed round-trips. **BTCUSDT alone accounts for the entire loss (-$1.57); ETH+SOL combined are break-even (+$0.03).** By the 2026-04-13 gate's own KILL rule (any symbol net-negative + rolling expectancy negative), the verdict is **WIND-DOWN** — but magnitudes are tiny and the sample is small, so this reads as "no demonstrated edge," not "bleeding out."

**Do NOT deposit capital or raise `max_concurrent_longs`.** Highest-value action is free: **drop BTCUSDT** from the pair list and keep ETH+SOL at minimum size as a live-data probe.

---

## Why the prior "edge is real" gate was wrong

The 2026-04-13 decision gate concluded "edge is real" (+$261, 53% WR, +$5.34/trade expectancy). That analysis was run on the window **Mar 19 → Apr 13** at **~$1,003 average notional**. Real live futures did not start until **2026-04-20**, and real order size is **$22**. The April gate therefore measured **testnet money**, not real capital, and its conclusion does not carry over.

The only offline test that used *real* `xai_live` sentiment (`sentiment-macro-replay-and-tuning-2026-05-06.md`) was **negative on all three live symbols** (BTC -5.85%, ETH -7.03%, SOL -5.33%; all negative Sharpe). Real money below now confirms break-even-at-best.

---

## Dataset

- Source: Binance futures income endpoint, real account shared by all agents (single API key).
- Window: trades realized on/after 2026-04-20 00:00 UTC.
- 127 income rows; **25 closed round-trips** (REALIZED_PNL events).
- Wallet balance at run time: **$52.94 USDT**, unrealized PnL $0.00 (flat / no open positions).

## By-Symbol Breakdown (real money)

| Symbol  | Trades | WR  | Avg Win | Avg Loss | Gross PnL | Commission | Funding | **Net PnL** | Net/trade |
|---------|-------:|----:|--------:|---------:|----------:|-----------:|--------:|------------:|----------:|
| BTCUSDT | 10     | 40% | $0.848  | -$0.698  | -$0.80    | -$0.78     | +$0.01  | **-$1.57**  | -$0.157   |
| ETHUSDT | 7      | 43% | $0.353  | -$0.248  | +$0.07    | -$0.15     | +$0.02  | **-$0.06**  | -$0.008   |
| SOLUSDT | 8      | 62% | $0.165  | -$0.214  | +$0.18    | -$0.09     | -$0.00  | **+$0.09**  | +$0.012   |
| **Total** | **25** | **48%** | $0.440 | -$0.448 | **-$0.54** | **-$1.02** | **+$0.03** | **-$1.53** | -$0.061 |

(Aggregate Avg Win/Loss and 48% WR are derived from per-symbol win/loss counts: 12 wins / 13 losses; aggregate R:R ≈ 0.98.)

## Equity & Distribution

- Net equity (sum of trades): **-$1.53**
- Peak equity: +$0.78
- Max drawdown: -$2.37
- Rolling-20 net expectancy (last 20 trades): **-$0.021/trade**

---

## Findings

### 1. BTCUSDT is the entire real-money loss
ETH (+$0.07 gross) and SOL (+$0.18 gross) are slightly positive before fees and net break-even after. BTC is -$0.80 gross / -$1.57 net by itself. Removing BTCUSDT moves the bot from -$1.53 to ~break-even (+$0.03), and it is a free config change. The April gate already flagged BTC's "fat left tail" and recommended "promote BTC last, or tighten SL" — real money confirms BTC has no edge here.

### 2. Fees roughly double the damage but are not the root cause
Total commission (-$1.02) exceeds the strategy's gross loss (-$0.54). All entries and exits are MARKET orders (taker, 0.05%). However, even at **zero fees** the bot is still -$0.51 gross — and that entire gross loss is BTC. Cutting fees (limit/maker entries) improves the margins but cannot manufacture an edge from a gross-negative book.

### 3. No demonstrated edge to scale
Aggregate real WR is 48% with R:R ≈ 0.98 (break-even WR ≈ 50.5%), i.e. gross expectancy is marginally negative. ETH+SOL break-even on 15 trades (+$0.03) is statistically indistinguishable from zero. There is no real-money basis for adding capital or concurrency.

---

## Decision: WIND-DOWN / PROBE (do not scale, do not deposit)

### KILL criteria from 2026-04-13 gate — status on real money
- [x] A single symbol PnL net-negative post-live → **BTCUSDT -$1.57, ETHUSDT -$0.06**
- [x] Per-trade expectancy negative over rolling 20-trade window → **-$0.021/trade**
- [ ] Equity below peak-minus-max-DD → not applicable at this scale

Two of the three KILL triggers are met. Formal verdict: **wind-down**.

### Practical interpretation
The losses are tiny ($1.53 over a month, 25 trades). Rather than a hard kill, the data supports a **surgical, free correction**:

1. **Drop BTCUSDT** from `trading.pairs` and `futures.symbols` in `config/settings.sentiment_macro.yaml`. (Decision deferred — documented here, not yet applied.)
2. **Keep ETH+SOL at current minimum size as a live-data probe.** The cost is ~break-even fees in exchange for collecting real sentiment+price pairs, which is the input the 2026-05-06 replay report says is needed for proper validation.
3. **Do NOT deposit capital** and **do NOT raise `max_concurrent_longs`** — that would repeat the AVAX mistake (scaling on paper/overfit validation).
4. **Limit-order entries** remain a worthwhile fee-reduction improvement but are lower priority than removing BTC, and will not by themselves create an edge.

### Reassess to scale (all required)
- [ ] Real per-symbol net expectancy clearly positive (not break-even)
- [ ] 80+ real closed round-trips
- [ ] Replay/backtest parity with real money established (denser sentiment replay)

---

## Reproduction

Real-money gate script (run inside the live container, which holds `BINANCE_API_KEY`/`BINANCE_API_SECRET`):

```bash
docker exec crypto-agent-agent_sentiment_macro-1 python /tmp/decision_gate.py
```

The script queries `/fapi/v1/income` (paginated from 2026-04-20) and `/fapi/v2/account`, classifies REALIZED_PNL events per symbol, folds in commission and funding for net, and applies the KILL criteria above. (Not yet tracked in `scripts/` — add there if this becomes a recurring check.)

## References

- Prior (testnet) gate: `docs/reports/sentiment-macro-decision-gate-20260413.md`
- Real-sentiment replay (negative): `docs/reports/sentiment-macro-replay-and-tuning-2026-05-06.md`
- Improvement plan: `docs/reports/SENTIMENT_MACRO_IMPROVEMENT_PLAN.md`
- Agent config: `config/settings.sentiment_macro.yaml`
- AVAX disable rationale (scaling-on-overfit precedent): `docker-compose.prod.yml` (agent_avax block, 2026-05-20)
