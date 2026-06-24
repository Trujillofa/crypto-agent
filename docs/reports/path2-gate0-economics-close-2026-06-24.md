# Path 2 Illiquid-Venue — Gate 0 Economic Close (2026-06-24)

**Decision:** **CLOSE** the illiquid-venue microstructure lane at **Gate 0 on economics** for a
solo, public-data operator at ≤ $10k venue exposure. No account opened, no Gate 1 data spend.
**Lane brief:** [path2-illiquid-venue-gate0.md](../specs/path2-illiquid-venue-gate0.md)
**Attestation state file:** `research/rbi_loop/path2-illiquid-venue/gate0-attestation.json`
**Capstone fork:** [research-consolidation-2026-06-23.md](./research-consolidation-2026-06-23.md) §The forward fork
**Mode:** paper analysis only — the Gate 0 sub-gates are economic questions answerable before any probe.

---

## Why a paper close is the correct move

The Gate 0 brief states explicitly: *"these are economic questions, answerable on paper before any
probe … If sub-gates 1 or 3 fail (no deployable size, or unacceptable venue risk), close the lane at
Gate 0 — do not spend on Gate 1 data."* This is that close. Operator inputs that drove it (2026-06-24):
**solo operator, public data, no latency/infra edge, ≤ $10k maximum at venue.**

## The four sub-gates, with numbers

| Sub-gate | Result | Reasoning |
|---|---|---|
| **2 — Spread vs edge** | **FAIL (decisive)** | The thesis *requires* a thin book, but a thin book *is* a wide spread. Small-venue taker fees ~0.1–0.2%/side (**20–40 bps RT**) + thin-pair spread crossing (**20–100+ bps RT**) ⇒ realistic round-trip cost **~40–150+ bps**. The candidate signal is taker-side signed-flow microstructure — the exact signal #110 measured **net −7 to −10 bps, p≈0.5, sign corr 0.009** on liquid majors. For the lane to work it must flip negative→strongly-positive **and** clear 40–150 bps. No empirical basis for a 10× larger, sign-flipped effect; the thinness that would create the edge is the same thinness that creates the cost that eats it. "Friction eats edge" class, amplified. |
| **1 — Capacity / cost** | **FAIL** | At ≤$10k bankroll, per-trade size ~$200–$2k. Even at an implausibly generous **+50 bps net** edge, that is **$1–$10 gross/trade** — below the fixed operating cost of running infra + data + monitoring a small exchange. Deployable dollar-edge < operating cost. |
| **3 — Custody / venue risk** | **FAIL (reinforcing)** | Small venues carry non-trivial freeze/insolvency/exit-scam base rates. A conservative ~2%/yr expected loss = **$20–$200/yr drag** on a $1k–$10k bankroll. Layered on a sub-gate-2 edge already ≤0, total EV is negative. Low exposure caps the downside *and* the upside — it does not rescue a negative-edge lane. |
| **4 — Defensibility** | **FAIL** | The thinness caps the edge and invites market makers back the moment it is real. A solo operator with ≤$10k, public data, no latency/infra is the smallest, slowest, marginal participant — no moat. A persistent thin-venue inefficiency is captured first by illiquid-venue specialists with custody tolerance and better infra. |

## Verdict

**Sub-gate 2 fails decisively; 1, 3, and 4 reinforce.** The accessible-now (solo, public-data,
≤$10k) expression of Path 2 illiquid-venue microstructure is **closed at Gate 0 on economics** — the
cheapest possible closure, before any venue commitment or data spend. The result is consistent with
the banked program's structural conclusion: **no edge without a differentiated advantage**, and this
expression does not clear its own economic gates.

## The one condition that reopens it

A **differentiated advantage at the venue**, which inverts the failing sub-gate:

- **Market-maker / maker-rebate** — *earn* the spread instead of paying it, inverting sub-gate 2.
- **Privileged API / co-location / latency** — a real execution asymmetry.
- **Designated liquidity-provider / structural access** at the venue.

Each is a **C-tier business** (capital + infra + a different objective), not "solo operator + public
data + $10k." Path 2's only surviving prior lives there, and the capstone already scopes C-tier as a
**separate, larger program entered deliberately** — not reachable from the current operator profile.
Reopening requires the operator to bring such an advantage *in hand*, then a fresh Gate 0 brief for
that specific advantage.

## Disposition

- **Path 2 illiquid-venue (accessible expression): CLOSED at Gate 0 (economics).**
- **C-tier venue-advantage door: noted, not open** — requires a named advantage + new Gate 0 brief.
- **No code, no probe, no `--execute`, no live-risk change** for this decision.
