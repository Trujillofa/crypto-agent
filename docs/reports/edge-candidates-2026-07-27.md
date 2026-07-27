# Edge Candidates — Plain-Language Explainer

**Date:** 2026-07-27
**Status:** advisory / decision input. Nothing here is a commitment or an open lane.
**Scope:** what is actually worth trying next, and exactly why, given everything this
program has already closed.

---

## The framing (read this first)

This program has already spent its budget on one hypothesis class and got a clear
answer. Roughly 1,440 WFO runs, ~30 probe lanes, a dual-pass adversarial review
(Claude × Grok, `docs/reports/deep-edge-research-reconciliation-2026-06-24.md`), and a
sealed public-data book all converge on the same conclusion:

> **A cleverer strategy on the same public OHLCV data is not an edge.**
> Everything that looked like one died to costs, tail risk, or single-horizon fragility.

So none of the candidates below is "another strategy idea." Each one is exactly one of
three things:

| Class | Meaning |
|---|---|
| **Measured** | You already ran the probe and it passed a pre-registered gate. Work left is execution, not discovery. |
| **Unpriced** | A specific variant the bank never actually tested. Cheap to price on paper before spending anything. |
| **Structural** | An advantage that comes from *what you are* (small, or a venue member), not from *what you predict*. Cannot be arbed away by a better model. |

An "advantage type" is named for every candidate. If a candidate has no advantage type,
it is not an edge — it is a backtest.

---

## 1. Finish the NFP event lane

**Class:** Measured · **Advantage:** information timing on a scheduled release
**Cost:** ~$0 and a few hours of coding · **Ceiling:** low but real · **Deadline: 2026-08-06**

### What it is
Non-farm payrolls prints the first Friday of each month at 08:30 ET. The probe tested a
*conditional* version of the classic macro reaction: not "surprise → drift," but
**good news is good** — a strong labor print reads as risk-on, and crypto follows.

### Why it might be real
It is the only lane in the entire repository that passed an out-of-sample gate with a
verdict of **YES** (`docs/reports/`, PR #154): **+0.70% net expectancy per event,
profit factor 2.18, and every leave-one-out fold positive.** That last part matters —
it means no single event is carrying the result.

### What is left to do
The forward-confirmation gate is signed and in force
(`docs/evidence_portfolio/NFP_FORWARD_GATE.md`, 2026-07-21). It requires, for each
future print:

1. **Before** the release, a Wayback "Save Page Now" snapshot of the consensus page —
   this freezes the consensus *point-in-time*, so you cannot later be accused of
   fitting to a revised number.
2. **After** the release, the headline actual read from the BLS release.
3. One append-only row in `data/macro_events/nfp_good_news_forward.csv`.

The build brief exists (`docs/specs/nfp-forward-capture-routine.md`); the script
(`scripts/nfp_forward_capture.py`) **does not exist yet.**

### The catch
- **Hard deadline.** The capture window for the 2026-08-07 print opens **2026-08-06
  12:30 UTC**. A print with no pre-release capture is a `MISSED_CAPTURE` row, and
  **three misses cap the sample** — i.e. you can lose the lane by inaction alone.
- **Low frequency by construction.** ~12 events/year. At +0.70% per event this is
  "hundreds of dollars per $10k deployed per year," not a living.
- Forward results may simply not confirm. That is the point of the gate.

### Kill criteria
Whatever the signed gate says — do not renegotiate them after seeing data.

### Verdict
**Do it.** Near-zero cost, hard deadline, and it validates the whole data-first method.
This is the single highest-priority item on the list purely because of the calendar.

---

## 2. Conditional-CPI analog — *deferred behind #1*

**Class:** Unpriced · **Advantage:** same primitive, wider calendar
**Cost:** days of archive work, $0 cash · **Ceiling:** low-mid

### What it is
The same "good news is good" conditional framing, applied to CPI: a *cool* inflation
print → risk-on. If it works, it roughly doubles your event count per year.

### The honest caution
A **generic** CPI+NFP surprise-drift probe already closed **WEAK_EDGE**, and the ledger
attached an explicit instruction: *do not widen the series to rescue a weak result.*
What survived was specifically the **conditional** framing, which was never tested on
CPI. That distinction is the only thing that makes this legitimate rather than
gate-shopping.

### Therefore
This is allowed only as a **fresh, separately pre-registered lane** — its own null
model (#118 standard), its own frozen event set, written before any data is pulled —
**and only after the NFP forward gate confirms.** Running it now, while NFP is
unconfirmed, is exactly the failure mode of trying two things until one prints.

### Verdict
**Yes, later.** Gate it on the NFP day-30 review.

---

## 3. Delta-neutral *staking* carry

**Class:** Unpriced · **Advantage:** yield stacking, non-directional
**Cost:** hours for a paper memo; capital only if the memo clears · **Ceiling:** mid, capped by size

### What it is
Hold spot SOL or ETH, short the equivalent perpetual future. Price exposure cancels, so
you are not predicting anything — you collect funding. **The new part:** stake the spot
leg, so you earn staking yield *on top of* funding.

### Why this is not a re-run of a closed lane
The funding-carry lane was **banked** (`funding-carry-neutral-probe-v0`) for a precise
reason: excess return over the risk-free rate went negative once you measured it
honestly on deployed capital. But that arithmetic used **plain unstaked spot**. Adding
~6–8% APY (SOL) or ~3% (ETH) of staking yield changes the sum. Nobody has done that
sum.

### The question the memo must answer
> staking APY + expected funding − fees − depeg/liquidity risk, versus ~4% risk-free.

### The new risks the old lane did not have
These are the whole reason this needs a memo and not a trade:
- **Liquid-staking depeg** — the LST (e.g. jitoSOL, stETH) can trade below the
  underlying, which is a real loss on the spot leg.
- **Unbonding delay vs. margin calls** — if the perp leg needs collateral *now* and the
  spot leg is locked in an unstaking queue for days, "delta-neutral" stops being
  neutral at exactly the wrong moment. This is the failure that liquidates the
  position.
- Funding compresses; the earlier probe measured ~80% forward compression.

### Why it is still attractive
It does not decay like a prediction edge. You are being paid a risk premium to warehouse
a real risk — which is durable as long as you are honestly priced for that risk.

### Verdict
**Write the Gate-0 memo unconditionally** (it costs hours). **Commit capital only if
excess-over-risk-free is clearly positive after honest haircuts** for depeg and
unbonding.

---

## 4. Access / "size-is-edge" operations

**Class:** Structural · **Advantage:** flows a whale *cannot* harvest
**Cost:** ~$0 ongoing, already automated · **Ceiling:** mid, lumpy and episodic

### What it is
Rewards that are allocated to *participants*, not to *capital*: merit-based sale
allocations, announced airdrops, measurable incentive programs. A large fund entering
these dilutes the very reward it is chasing; a small operator does not. That asymmetry
is structural.

### Why this is on the list
The reconciliation doc flagged this as **the one edge class the entire bank never
tested** — every other closed lane was a prediction bet on public data. This is not a
prediction bet at all.

### Where it actually stands
Phase-0 ran and gave an honest, unglamorous answer: **0 programs actionable today**
(closed 2026-07-14). But the machinery survives and costs nothing to keep armed:
- `incentive_ops` registry
- `scripts/legion_watch.sh` — weekly watch on the best candidate (Legion merit sales),
  which is hold-and-watch pending a live round with a **disclosed merit formula**.

### The discipline that keeps this positive-EV
Act **only** on rewards that are (a) announced, (b) measurable, and (c) clear a base-case
EV of **$25/hr** for the labor involved. Never on speculative "points" that might
convert someday. Without that rule this quietly becomes unpaid work.

### Verdict
**Keep as a standing watch.** Readiness costs nothing and payoffs are uncorrelated with
everything else you run.

---

## 5. Scale the cTrader FX system

**Class:** Measured · **Advantage:** compounding an expectancy you already have
**Cost:** capital allocation, zero new research · **Ceiling:** mid-high

### What it is
Not a new idea at all — put more behind the system that already works. The cTrader
session-momentum FX agent is the designated forward vehicle (Track 1): **live-validated
+EV on a FundedHive prop challenge**, with per-instrument costs derived empirically
(`derive_sm_pair_costs.py`) rather than assumed. That cost discipline is precisely the
lesson the crypto program paid ~1,440 WFO runs to learn.

### Why it belongs on an "edges" list
The highest-expected-dollar action available is usually not a sixth objective — it is
more capital, more instruments, and tighter cost calibration on the one system with
measured positive expectancy, up to its capacity limit.

### The open hole (do this before scaling)
Per `docs/evidence_portfolio/CTRADER_EXTERNAL_GATE.md`: every live close so far has been
a **broker bracket fill caught by the reconciler**. The **agent-initiated** exit paths
(partial TP, trailing stop) are still **unexercised in live**. The gate requires ≥5
real-time executions per agent-managed exit path before that path counts as
production-ready, plus ≥30 closed forward trades (or 10 live sessions), 0 rule
violations, and positive net expectancy after all frictions.

### Boundary
That agent lives in its **own repository**. This repo records the gate; it must not
modify cTrader source.

### Verdict
**Probably the largest absolute-dollar item here, and it needs zero new hypotheses.**
Close the exit-path hole first — scaling an unexercised close path is how a small bug
becomes a challenge failure.

---

## 6. Maker-rebate market-making on a C-tier venue

**Class:** Structural · **Advantage:** *earn* the spread instead of paying it
**Cost:** high — this is a business, not a probe · **Ceiling:** high, uncapped, durable

### What it is
Become a designated market maker / liquidity provider (or reach a rebate fee tier) on a
smaller venue, and quote both sides continuously.

### Why it is the only surviving *trading* prior
Look at how the crypto lanes actually died: a real gross signal, then costs ate it. The
Path 2 illiquid-venue gate failed on exactly that sub-gate — thin books mean wide
spreads, and taking liquidity there is expensive. Market-making **inverts the sign of
that term**. The thin book stops being your cost and becomes your revenue. Both review
passes ranked this as the sole durable trading edge left standing.

### What it actually demands (do not underestimate this)
- Dedicated capital well beyond $10k
- Real infrastructure: uptime, inventory/position management, alerting, kill switches
- Custody risk tolerance on a **small venue** — venue failure is a live risk, not a
  footnote
- Months of committed operation; this is not a probe you can abandon after two weeks

### The entry gate your own docs already specify
1. Name a **specific** venue.
2. Obtain its maker-fee / rebate schedule **in writing**.
3. Write a fresh Gate-0 brief demonstrating that sub-gate 2 (cost) genuinely inverts —
   with the rebate arithmetic, not a hope.

### Verdict
**Only if you deliberately decide to build a business.** Never on momentum, never as a
"let's see." But it is the one item here with an uncapped ceiling.

---

## Wildcard: on-chain flow primitives

**Class:** Unpriced · **Prior: low — expect NO_PULSE** · **Cost:** a few hours, free tiers only

Stablecoin net mints, exchange netflows, whale-wallet concentration. This is the last
data *family* the bank never touched — every probe so far used exchange-side telemetry
(price, volume, funding, order flow). The efficient-venue taxonomy that correctly
predicted the microstructure null predicts a null here too.

Rules if you run it: **free tiers only** (do not buy Glassnode/Nansen on spec), standard
#118 null model, pre-registered. Run it only if you want the book *fully* sealed. A
pulse here would be a genuine surprise — treat it with more suspicion than enthusiasm,
not less.

---

## Summary table

| # | Edge | Class | Upfront cost | Prior | Ceiling | Action |
|---|------|-------|--------------|-------|---------|--------|
| 1 | NFP forward capture | Measured | ~0 — **deadline 2026-08-06** | **YES (passed OOS)** | Low but real | **Build now** |
| 2 | Conditional-CPI analog | Unpriced | Days, $0 | Moderate | Low-mid | Defer behind #1 |
| 3 | Staking carry | Unpriced | Hours (memo) | Moderate | Mid, capped | Write memo |
| 4 | Access / size-is-edge | Structural | ~0 (built) | Real but episodic | Mid, lumpy | Keep watching |
| 5 | Scale cTrader FX | Measured | Capital only | **Proven live** | Mid-high | Close exit-path gate, then scale |
| 6 | Maker-rebate MM | Structural | High (a business) | Only durable trading prior | High | Deliberate decision only |
| — | On-chain flows | Unpriced | Hours, free tiers | **Low** | Unknown | Optional book-sealer |

The pattern across all of them: **execute what is measured (1, 5), price what is
unpriced (3), stay ready for what is reserved for you (4)** — and fund the expensive
structural bet (6) only as a conscious decision.

---

## Do NOT reopen these

Listed explicitly because "find me an edge" invites them straight back in. Each was
closed with margin, not marginally — see
`docs/reports/autoresearch-candidate-ledger.md` and
`docs/reports/research-consolidation-2026-06-19.md`.

| Closed lane | Why it stays closed |
|---|---|
| OHLCV / threshold tuning on majors | ~1,440 WFO runs; structural-probe surface formally CLOSED |
| Funding / short crowding mean-reversion | Measured null |
| Alt-perp funding carry (unstaked) | Negative excess over risk-free; banked |
| New-listing sniping | A latency business in disguise; most bot-contested events in crypto |
| Polymarket / mNAV re-runs | Both closed WEAK_EDGE; mNAV "pulse" was a 4-bug artifact |
| Liquid-market microstructure (OFI) | sign_corr 0.009 on BTC, net −7 to −10 bps, p≈0.5 |
| Token-unlock 72h short | Failed to reproduce on independent data; source prices were reconstructed templates |
| Cross-sectional alt momentum | 4 of 6 cells bankrupt the book (solvency FAIL) |
| Illiquid-venue **taking** | Fails the cost sub-gate — only viable with the #6 maker advantage |

Reopening any of these requires a **new, differentiated advantage** — alt-data, latency,
scale, or access — not a new parameter.
