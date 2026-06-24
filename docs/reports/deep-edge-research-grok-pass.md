# Deep Edge Research — Independent Grok Pass

**Date:** 2026-06-24
**Author:** Grok (independent; did not read any prior deep-edge pass)
**Mode:** Research only — no code, no probes, no `--execute`, no live-risk change
**Canonical bank:** [research-consolidation-2026-06-23.md](./research-consolidation-2026-06-23.md)
**Gate 1 null reference:** [RBI_AUTORESEARCH_LOOP.md](../RBI_AUTORESEARCH_LOOP.md) §Mandatory baseline (#118)

---

## Executive summary

The bank is **structurally sound**. ~1,440 WFO runs plus a dozen cheap probes fail in a small number of coherent ways — not scattered near-misses. Any remaining candidate that still uses **public aggregate data on liquid, well-arbitraged surfaces** should be assigned a **low prior** of clearing Gate 1 (#118: beat shuffled/phase-randomized or permuted-feature null, block-bootstrap `p_adj < 0.05`, concentration ≤ 25%, net of venue costs).

**Independent conclusion:** For a solo operator with a server, public exchange APIs, and modest capital, there is **no cheap probe left with positive expected value** on accessible surfaces. The rational default is **stay banked**.

If the operator insists on one more falsification before accepting terminal state, the **only** unexplored public-data primitive with a thesis structurally distinct from tested OHLCV structure is **forced liquidation / cascade flow** — but its expected outcome is `NO_PULSE` for the same “efficient venue” reason that killed OFI on majors. **Path 2 (illiquid venue microstructure)** is the only lane with a credible differentiated-advantage story; it is **held**, not probed, until the operator attests venue access and passes Gate 0 economic sub-gates.

**Do not reopen any banked liquid-major lane.**

---

## 1. What the bank covers — and does not

### 1.1 Edge *types* the bank covers

| Edge type | Mechanism | Bank outcome |
|-----------|-----------|--------------|
| **Directional return prediction** from price/volume structure | Trend, mean-reversion, breakout, MTF, overlays, regime gates | **NULL** — efficient venue (~1,440 WFO + probes) |
| **Directional prediction from perp microstructure on majors** | Funding extremes, basis premium, crowding, normalization | **NULL / CLOSED** — continuation > fade; filter-first never improved risk |
| **Cross-venue dislocation (majors)** | Spot/perp basis events, rolling thresholds | **NULL** — 0/30 WFO passes; thin edge dies under sizing + exits |
| **Session / calendar conditioning (crypto-internal)** | Americas gate, higher-TF regime allocator | **NULL** — no separation vs baseline |
| **Scheduled macro calendar (exogenous)** | FOMC/CPI/NFP event windows | **NO_PULSE** — no fee-surviving drift |
| **Macro surprise (exogenous, PIT-limited)** | Hot/cold CPI/NFP vs consensus | **WEAK_EDGE** — monotonicity fragments; NFP “hot → up” contradicts frozen sign |
| **TradFi risk-regime lead-lag** | QQQ/DXY/10Y/VIX → crypto forward | **WEAK_EDGE** — contemporaneous correlation, no predictive lead-lag |
| **Higher-TF trend (daily SMA)** | Long-only filter on majors | **HAS_PULSE probe, undeployable** — shared beta (breadth corr 0.59), not independent edge |
| **Event calendar (crypto-native)** | Token unlock 72h short | **NO_PULSE** — paper artifact; 49% neg vs claimed 88.5% |
| **Market-neutral yield** | Delta-neutral funding carry | **BANK** — known premium; excess over risk-free negative forward |
| **Relative value (different asset class)** | mNAV treasury premium reversion | **WEAK_EDGE** — one-name regime artifact (SBET) |
| **Probability calibration (different venue)** | Polymarket favorite-longshot | **WEAK_EDGE** — well-calibrated; net edge inside friction |
| **Tick-level signed flow (different telemetry, same venue)** | aggTrade OFI deciles on BTC/ETH/SOL | **NO_PULSE** — p≈0.5, net −7 to −10 bps, sign corr 0.009 |

### 1.2 *Populations* the bank covers

| Population | Covered? | Notes |
|------------|----------|-------|
| Liquid CEX majors (BTC, ETH, SOL, BNB, AVAX) | **Yes** | 1h/4h/daily; primary program surface |
| Top-10 liquid alts (XRP, DOGE, ADA, LINK, LTC, …) | **Partial** | Daily trend *breadth* probe only — NO_PULSE, shared beta |
| Mid / long-tail alts | **No** | Not systematically probed for structure or carry |
| Binance aggTrade / public tick flow | **Yes (majors only)** | #110 |
| L2 order book (any symbol) | **No** | Deliberately gated behind OFI pulse; never built |
| Liquidation / force-order stream | **No** | Named in reset doc; **never probed** |
| Cross-exchange (CEX–CEX) | **Yes (basis/dislocation)** | CLOSED on majors |
| DEX / on-chain | **No** | Out of program scope |
| TradFi proxies (Yahoo) | **Yes** | Cross-asset probe — WEAK_EDGE |
| US equities (treasury mNAV) | **Yes** | WEAK_EDGE |
| Prediction markets (Polymarket) | **Yes** | WEAK_EDGE |
| Scheduled US macro | **Yes** | NO_PULSE / WEAK_EDGE |
| LLM runtime sentiment | **Live only** | Not backtestable; live vehicle swept dead |
| **Illiquid venues / thin books** | **Gate 0 only** | Path 2 OPEN_PENDING_INFRA — no market probe |

### 1.3 Structural taxonomy (bank’s meta-finding)

Every detectable pulse so far maps to one of four classes — **none** leave deployable excess for a public-data solo operator on liquid surfaces:

1. **Efficient venue** — information already in price (OHLCV majors, OFI majors).
2. **Most-arbitraged trade** — premium exists but excess over cost of capital is gone (funding carry).
3. **Regime / bubble artifact** — one episode, not a relationship (mNAV SBET, unlock paper).
4. **Friction eats edge** — mispricing real but < round-trip cost (Polymarket, OFI net bps).

---

## 2. Surviving candidate spaces (exhaustive)

Below: every space where a *durable* edge could **still** live in principle, whether or not this operator can reach it. For each: structural reason vs majors, accessibility, cheapest Gate-1 probe, mandatory null (#118), and primary kill risk.

**Legend — accessibility**

- **A** = accessible now (public API, trusted major CEX, read-only)
- **B** = accessible with modest new infra (same operator, no co-lo)
- **C** = requires differentiated infra / capital / venue risk (Path 2+)

---

### Tier A — Public data, major/trusted venue, not yet probed

| # | Candidate space | Why it might survive where majors didn’t | Access | Cheapest Gate-1 probe | Mandatory null (#118) | Top kill risk |
|---|-----------------|------------------------------------------|--------|-------------------------|----------------------|---------------|
| A1 | **Forced liquidation / cascade flow** | Mechanical forced selling creates **temporary** impact not driven by OHLCV patterns; distinct from sweep probe (chart level vs exchange force orders) | **A** — Binance `!forceOrder@arr`, futures liquidation endpoints | Event study: liquidation-cluster windows (+5m/+30m/+2h forward return & MAE) vs **matched random windows** + **phase-randomized** event labels; block-bootstrap on excess return; concentration by day | Shuffled event timestamps preserving marginal size distribution; block-bootstrap `p_adj` across horizons | **Efficient venue** — HFT/arbs fade cascades on liquid perps before REST latency; same class as OFI NULL |
| A2 | **Open-interest shock + price divergence** | OI change may encode positioning stress not in candles; untested as **standalone** primitive | **A** — Binance futures OI history (public) | Bucket ΔOI z-score → forward return; monotonicity across deciles; vs permuted OI shocks | Phase-randomized OI changes paired to returns; shuffled-sign on OI→return link | Redundant with funding/crowding failures; **continuation** not reversion on majors |
| A3 | **New-listing / announcement drift** | Information arrives discretely; retail flow asymmetry at listing | **A** — Binance announcement archive + listing timestamps | Fixed window post-listing (+1d/+7d) vs matched non-listing alts; survivorship declared up front | Shuffled listing dates within symbol cohort; concentration cap on single listings | **Survivorship / look-ahead** — only listed winners observed; n thin; one-off events |
| A4 | **Alt-perp funding carry (non-majors)** | Thinner arb capital on long-tail perps → higher gross carry? | **A** — reuse funding API on top-20 perps ex-BTC/ETH/SOL | `probe_funding_carry_neutral` template on alt basket; durability split + excess vs RF | Block-bootstrap on carry series; forward-half holdout | **Most-arbitraged** — majors already BANK; alts add **negative funding tail + venue risk**; capacity tiny |
| A5 | **Mid-cap OFI on Binance (aggTrade)** | Thinner top-of-book → slower MM arb than BTC? | **A** — same REST aggTrade as #110 | Repeat OFI-decile test on e.g. 5 mid-caps (LINK, AVAX, DOGE, ADA, MATIC) | **Shuffled-sign** on trade flow (#110 protocol) | **Not Path 2** — same venue/MM ecosystem; majors NULL was decisive (p≈0.5, corr 0.009); **spread > edge** |
| A6 | **Stablecoin depeg mean-reversion** | Peg breaks are mechanical; CEX spot vs $1 | **A** — public USDT/USDC/BUSD prices | Event study on |price−1|>ε; forward reversion vs random | Shuffled depeg event times | **Capacity + competition** — arb bots; rare events → concentration; custody |
| A7 | **Weekend / session standalone drift** | 24/7 crypto vs closed TradFi; untested as **standalone** directional (session router tested overlay only) | **A** — existing OHLCV | Fri-close → Mon-open, Asia vs US session buckets vs matched random | Phase-randomized session labels | Already implied **efficient**; macro/cross-asset weak |

---

### Tier B — Public data, modest new infra (still solo-feasible)

| # | Candidate space | Why it might survive | Access | Cheapest Gate-1 probe | Mandatory null | Top kill risk |
|---|-----------------|----------------------|--------|----------------------|----------------|---------------|
| B1 | **L2 order-book imbalance (mid-caps, Binance)** | Queue position signal not in aggTrade sign | **B** — REST depth snapshots (no store build for probe) | Snapshot imbalance decile → +30s/+5m forward; permuted book shuffle | Shuffled depth levels preserving marginal sizes | Same as A5 — **efficient venue** on Binance; snapshot rate limits |
| B2 | **Liquidation + OI joint conditioning** | Cascade only when OI elevated | **B** — combine A1 + A2 data | Interaction buckets vs additive baselines | Permute OI and liquidation labels independently | **Sparse cells** → concentration > 25% |
| B3 | **ETF / fund flow proxy (GBTC premium, ETF tickers)** | TradFi wrapper friction | **B** — Yahoo/FRED + crypto | Premium z-score → forward BTC; PIT table | Shuffled premium series | **WEAK_EDGE repeat** of cross-asset; friction |
| B4 | **Historical social volume (non-LLM)** | Alternative sentiment without replay problem | **B** — LunarCrush/Glassnode free tier or GDELT | Event spikes → forward return | Shuffled spike timestamps | **Data cost / PIT** — free tiers lack audit trail; likely efficient |
| B5 | **Cross-prediction-market mispricing** | Polymarket vs Kalshi same event | **B** — two APIs + accounts | Joint calibration residual | Permuted resolution labels | **Friction + capital lock**; Polymarket already WEAK_EDGE |
| B6 | **DEX–CEX spot basis (major pairs)** | Fragmented liquidity | **B** — DexScreener + CEX | Basis z-score event study | Shuffled DEX prints | **Execution** — gas, MEV, wallet custody; not solo-modest-capital |

---

### Tier C — Differentiated advantage (Path 2 family — not “accessible now”)

| # | Candidate space | Why it might survive | Access | Cheapest Gate-1 probe | Mandatory null | Top kill risk |
|---|-----------------|----------------------|--------|----------------------|----------------|---------------|
| C1 | **Illiquid-venue microstructure** | Thin book → OFI persists; fewer tick models | **C** — named small CEX / perp dex | OFI-decile on **named** venue/pair; venue cost model | Shuffled-sign (#110) | **Capacity < cost** or **venue custody** — Gate 0 sub-gates |
| C2 | **Co-location / sub-second latency** | Pick off stale quotes | **C** | Not cheap on this stack; business not probe | N/A at Gate 1 | **Capital + infra** — explicit Path 2 item |
| C3 | **On-chain MEV / atomic arb** | Block-space priority | **C** | Simulation-only | Shuffled pool state | **Smart-contract risk**; not modest capital |
| C4 | **Proprietary alt data** (whale flows, exchange reserves) | Non-public information set | **C** — paid vendor | Vendor historical dump → event study | Shuffled flow labels | **Subscription cost > edge**; data PIT disputes |
| C5 | **Market-making / maker rebates** | Earn spread + rebate, not predict | **C** — quote engine | Inventory PnL sim | N/A — different objective | **Adverse selection** — business model change |
| C6 | **Multi-CEX funding arb at size** | Cross-venue carry | **C** — accounts + capital on N venues | Cross-venue funding spread panel | Shuffled venue spreads | **Most-arbitraged** — majors carry BANK |

---

### Tier D — Formally “not covered” but **low prior** (bank logic extends)

| # | Space | Why low prior |
|---|-------|---------------|
| D1 | More OHLCV symbols on same TFs | Same efficient-venue class |
| D2 | More Polymarket τ / categories | Friction-eats-edge already demonstrated |
| D3 | More mNAV tickers | Regime-artifact + post-hoc rescue banned |
| D4 | More macro indicators (PCE, ECB, claims) | Multiple-testing fishing; calendar NO_PULSE |
| D5 | Relative-strength rotation | Probe already sparse / stopped |
| D6 | LLM sentiment backtest | Not point-in-time reproducible |
| D7 | Re-open SOL overlay / sentiment-macro | Explicitly superseded at corrected costs |

---

## 3. Ranking

Scored 1–5 on four axes (higher = better for solo operator). **Composite** = product mindset: one weak axis zeros practical value.

| Rank | ID | Space | Accessible now | Cheap falsify | Bank doesn’t cover | Capacity > cost (prior) | Composite verdict |
|------|-----|-------|----------------|---------------|-------------------|------------------------|-------------------|
| 1 | **C1** | Illiquid-venue microstructure (Path 2) | 1 (pending infra) | 4 | 5 | 3 (if sub-gates pass) | **Hold** — only high-prior structural story; not runnable |
| 2 | **A1** | Liquidation / cascade flow | 5 | 5 | 5 | 2 | **Optional falsifier** — cheap closure, low edge prior |
| 3 | **A2** | OI shock standalone | 5 | 5 | 4 | 2 | Low prior — crowding family failed |
| 4 | **A3** | Listing events | 5 | 4 | 4 | 1 | Reject — survivorship |
| 5 | **A4** | Alt funding carry | 5 | 5 | 3 | 1 | Reject — majors BANK generalizes |
| 6 | **A5** | Mid-cap OFI (Binance) | 5 | 5 | 3 | 1 | Reject — not Path 2; #110 falsifies thesis |
| 7 | **B1** | L2 imbalance mid-cap | 3 | 4 | 3 | 1 | Reject — same venue |
| 8 | **A6** | Stablecoin depeg | 4 | 3 | 4 | 1 | Reject — rare + arb bots |
| 9 | **C2–C6** | Latency / MEV / MM / alt data | 1 | 1–2 | 5 | varies | **Hold** — separate programs |
| 10 | **D*** | Extensions of banked lanes | 5 | 5 | 1 | 1 | **Reject** — gate-shopping |

---

## 4. Recommendation

### 4.1 Default: **stay banked**

First-principles read of the scoreboard + taxonomy: the failures are **the same fact** from multiple angles. The cost-model error was found and fixed; false positives self-corrected. Further public-data probes on Binance-class surfaces have **negative expected value** — they are the “one more probe” trap the reset rules forbid.

A deployable edge for this operator requires **capacity > operating cost** after realistic fees, forward durability, and #118 null — not a positive backtest. Nothing in Tier A–B clears that bar on priors.

### 4.2 If one cheap probe (closure, not optimism): **A1 only**

| Probe | Run? | Rationale |
|-------|------|-----------|
| **A1 — Liquidation / cascade flow** | **Optional** (max 1) | Sole remaining *scheduled* primitive from [research-reset-2026-06-06.md](./research-reset-2026-06-06.md) never executed; thesis ≠ OHLCV; read-only; ~hours not weeks. **Expected verdict: NO_PULSE.** |
| A2 — OI shock | **No** | Too close to banked funding/crowding NULL family |
| A4 — Alt carry | **No** | Majors carry BANK; extends “most-arbitraged” not “new primitive” |
| A5 / B1 — Mid-cap OFI / L2 on Binance | **No** | Contradicts Path 2 definition; #110 already falsified same-venue microstructure |

**A1 probe spec (conceptual, no code):**

- **Universe:** BTC/ETH/SOL perps (frozen — no alt shopping).
- **Events:** Public force-liquidation prints; cluster definition frozen ex-ante (e.g. ≥$X notional in 5m).
- **Horizons:** +5m, +30m, +2h (and document if all fail net of ~10 bps taker).
- **Null:** Phase-randomized event times + block-bootstrap on excess vs matched quiet windows.
- **Gates:** #118 AND — monotonicity, `p_adj < 0.05`, beats null, concentration ≤ 25%, net edge > 0 after venue RT cost.

### 4.3 Hold (do not probe without prerequisites)

| Item | Action |
|------|--------|
| **Path 2 — illiquid venue** | Complete Gate 0: name venue/pair, feasibility table (spread, depth, volume), `PATH2_ILLIQUID_VENUE_ACCESS_ATTESTED`, economic sub-gates 1–4. **Then** Gate-1 OFI on *that* surface only. |
| Latency / MEV / proprietary data / market-making | Separate deliberate programs; each needs its own Gate 0 brief. Not cheap probes on current stack. |

### 4.4 Reject (do not spend cycles)

- Any **liquid-major OHLCV** lane (overlay, standalone, 4h, MTF, sentiment filter retune).
- **Mid-cap OFI on Binance** as Path 2 substitute — wrong advantage type.
- **Alt funding carry** extension — predicted BANK repeat.
- **Listing / unlock subsetting** — post-hoc rescue trap.
- **More Polymarket / mNAV / macro indicators** — friction / artifact classes already demonstrated.
- **Autoresearch / WFO** without new `HAS_PULSE` beating #118 null.

---

## 5. Decision matrix (operator fork)

```
                    ┌─────────────────────────────────────┐
                    │  Accept bank (recommended default)   │
                    │  Idle monitors only; no new lanes    │
                    └─────────────────────────────────────┘
                                      │
              ┌───────────────────────┴───────────────────────┐
              ▼                                               ▼
   ┌──────────────────────┐                    ┌──────────────────────────┐
   │ Optional: A1 liq probe │                    │ Path 2: attest illiquid   │
   │ (~1 day, expect NULL)  │                    │ venue → Gate 1 on THAT    │
   │ Closes reset doc loop  │                    │ venue only                │
   └──────────────────────┘                    └──────────────────────────┘
```

---

## 6. Reconciliation notes for Claude

1. **Agreement with capstone:** Public-data program terminal; missing ingredient is **differentiated advantage**, not another market.
2. **Independent addition:** Liquidation cascade is the **only** Tier-A primitive both (a) absent from the scoreboard and (b) named in reset doc — but **low prior**; optional for closure only.
3. **Disagreement risk:** If another pass ranks **A4 alt carry** or **A5 mid-cap OFI** highly — push back: those are same-venue / same-arbitrage class, not Path 2; priors should be near zero post-#110 and carry BANK.
4. **Path 2 status:** Ledger shows Gate 0 attestation complete, `OPEN_PENDING_INFRA` — this pass **does not** treat mid-cap Binance as Path 2; that would violate [path2-illiquid-venue-gate0.md](../specs/path2-illiquid-venue-gate0.md) hard stops.
5. **#118 compliance:** Any future probe must report shuffled/phase-randomized or permuted-feature null, block-bootstrap `p_adj`, and 25% concentration — raw positive means are insufficient.

---

## 7. Bottom line

| Question | Answer |
|----------|--------|
| Is there a cheap, accessible, high-prior edge left? | **No** |
| Is there a cheap probe left at all? | **One optional** (liquidation cascade) for epistemic closure |
| Is there a durable edge left with positive prior? | **Only behind Path 2 infra** (illiquid venue) or non-public Path 2 items |
| Recommended action | **Stay banked**; optional A1; hold Path 2 until access attested |

**Nothing cheap left with positive expected deployable edge — stay banked** is the first-principles landing point. The program’s terminal conclusion survives independent scrutiny.
