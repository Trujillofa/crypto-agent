# A1 Phase-0 Final Report — 2026-07-14

**Baseline:** `baseline-20260630T222523Z` (COMPLETE)
**Window:** 2026-06-30 → 2026-07-14 (13 days, 17 observations + Day-0)
**Git SHA at start:** `9f1bfcc` — all 5 frozen artifacts validated at baseline open

---

## Summary

- **0 ACTIONABLE programs** — invariant held through every tick
- **17/17 non-ACTIONABLE**: 6 BLOCKED_UNVERIFIED, 11 BLOCKED_NEEDS_CAPTURE
- **0 verified**, 0 EV-READY, zero capital deployed, no wallets used
- Phase-0 was **successful at what it was designed to do**: answer "do these 5 programs have the shape of a tradeable edge?" — verdict below

---

## Per-Program Analysis

### 1. coinlist-token-sale — REJECT at Gate 2 (verification)

| Field | Value |
|---|---|
| Classification | IN_CORE |
| Capture target | `coinlist.co/faq` (HTML FAQ page) |
| Capture stability | **100% churn** — hash changed every single tick (19/19) |
| Snapshot count | 21 across baseline window |
| Size range | 210–235 KB (HTML with CDN headers, nonces, timestamps) |

**Analysis:** The FAQ page is a generic platform info page, not a specific sale round. Every daily capture produced a different hash — this is HTML noise (nonces, CDN cache-bust headers, analytics timestamps) masquerading as content change. There is **no stable artifact to verify against**. Even if a human reviews the HTML, `terms_match_snapshot: true` would be meaningless because the snapshot itself is a moving target.

**Recommendation:** Drop from active research. Replace the source URL with a **specific sale round page** (e.g., `coinlist.co/sale/<token-name>`) when one opens. Re-enter at a future baseline on that concrete URL.

---

### 2. legion-merit-sale — BEST CANDIDATE, blocked on merit-score opacity

| Field | Value |
|---|---|
| Classification | IN_CORE |
| Capture target | `legion.cc/documents/Launchpad_Terms_of_Service.pdf` (PDF) |
| Capture stability | **5% churn** — changed only at Day-0, then identical SHA for 18 consecutive ticks |
| File type | PDF — no CDN noise, immutable binary |

**Analysis:** This is the only program with a **stable, verifiable terms artifact**. The PDF TOS has not changed in 14 days. This is what "capture then verify" was designed for. However:

1. **No live round identified.** The PDF is the platform TOS, not a specific offering. A live round would have a separate sale page with token name, allocation size, eligibility dates, and participant caps.
2. **Merit score is undisclosed.** The TOS references a "merit score" that determines allocation priority but does not disclose the formula. This breaks criterion #3 (`c3_eligibility_public`): you cannot estimate P(eligibility) without knowing how the score is computed. The registry notes this explicitly.
3. **No KYC/jurisdiction resolution.** `jurisdiction_status: UNKNOWN` — needs a human to confirm whether their country/ID qualifies.

**Recommendation:** **Hold and watch** — this is structurally the strongest program but it's not actionable without a specific live round. When Legion announces a sale: capture the sale-specific page, extract the merit-score formula (if disclosed), verify jurisdiction eligibility, then re-run verification + EV. Do not deploy capital before the merit formula is public.

---

### 3. layer3-quests — UNLIKELY TO CLEAR Gate 4 (promotion)

| Field | Value |
|---|---|
| Classification | IN_CONDITIONAL |
| Capture target | `docs.layer3.xyz/` (documentation landing page) |
| Capture stability | **68% churn** — hash changed on 13/19 ticks |
| Reward type | `speculative_points` — **unannounced** |
| EV readiness | UNREADY, all provenance entries = `UNKNOWN` placeholder |

**Analysis:** The capture target is a docs landing page, not a specific quest or reward program. The reward is unannounced speculative points. Per the EV spec rule: `reward_announced: false → realizable_price = 0`. That forces base-case EV ≤ $0 even before subtracting gas, labor ($25/hr), and capital opportunity cost. Layer3 quests require hands-on task labor (testnet interactions, social tasks) which consumes operator hours at $25/hr+ — the EV formula deducts this. With zero realizable reward value, the net EV will always be negative.

**Recommendation:** **Freeze.** Do not deploy capital or labor into speculative-points programs unless (a) a specific distribution is announced, (b) the reward quantity and realizable price are measurable, and (c) base-case EV exceeds operator labor cost. Watch for announced Layer3 distributions and re-evaluate.

---

### 4. galxe-quests-oat — LOW REALIZABLE VALUE

| Field | Value |
|---|---|
| Classification | IN_CONDITIONAL |
| Capture target | `docs.galxe.com/about/introduction` (intro page) |
| Capture stability | **68% churn** — hash changed on 13/19 ticks |
| Reward type | `nft_or_xp` — OATs/NFT badges |
| EV readiness | UNREADY, all provenance entries = `UNKNOWN` |

**Analysis:** Same capture-target problem as Layer3 — the intro page doesn't describe a specific round. More critically: **OATs and NFT badges have near-zero realizable market value**. These are on-chain achievement tokens, not transferable ERC-20 rewards. The EV formula's `realizable_price` would be negligible (< $0.01), making even a 100% probability of earning one produce trivial returns. The labor cost (quest completion) would almost certainly dominate any possible reward.

**Recommendation:** **Kill for Phase-1.** The reward type is structurally incompatible with the EV formula — labor→OAT→$0 realizable is a guaranteed net-negative. Keep in registry as a classification fixture (IN_CONDITIONAL archetype) but do not deploy capital.

---

### 5. kaito-yaps — SPECULATIVE POINTS, UNANNOUNCED

| Field | Value |
|---|---|
| Classification | IN_CONDITIONAL |
| Capture target | `docs.kaito.ai/` (documentation landing page) |
| Capture stability | **68% churn** — hash changed on 13/19 ticks |
| Reward type | `speculative_points` — **unannounced** |

**Analysis:** Same fatal flaw as Layer3: unannounced speculative points → realizable_price forced to 0 → EV ≤ 0. Yaps are attention/engagement points with no announced conversion rate, distribution date, or token backing. Base-case EV is negative under the locked formula. The capture target is also a docs intro page, not a specific distribution round.

**Recommendation:** **Freeze.** Same logic as Layer3. Watch for an announced Yaps→token conversion with a measurable rate, then re-evaluate.

---

## Structural Findings

### 1. Capture targets are generic, not round-specific

All 5 active programs capture **platform documentation/intro pages**, not concrete offering documents. For verification to work, the URL must target a **specific round** with:
- Token name and quantity
- Eligibility dates (open/close)
- Distribution mechanism details
- Per-participant cap (quantity or USD)

The tooling works correctly — it fetches and hashes exactly what's pointed at. The registry needs round-specific URLs, not platform docs.

### 2. HTML CDN noise breaks verification for web pages

Coinlist (100% churn), Layer3/Galxe/Kaito (68% churn) all capture HTML pages that incorporate nonces, timestamps, cache-bust headers, or analytics tokens. These cause SHA-256 changes even when the semantic content is identical. **Only PDF-based captures (Legion) produce stable hashes.** For web pages:

- Record the **human-readable content** and let the reviewer decide `terms_match_snapshot` based on content inspection, not hash comparison
- Or: extract text content before hashing (strip nonces, scripts, meta tags)
- Or: prefer PDF/static-text endpoints for programs that offer them

### 3. No program in the registry points at a live, concrete round

This is the root finding of Phase-0. The registry is a **taxonomy of program archetypes** — it maps the landscape of incentive types. But for the pilot to advance to Phase-1, at least one program must be: (a) live right now, (b) with a specific distribution round, (c) at a stable, verifiable URL, (d) with a measurable reward value. The 5 active research programs were selected because they matched the inverse-scale thesis structurally, but **none currently has an active, measurable round**.

### 4. Legion is the closest but missing a round

If Legion launches a specific sale — with a named token, explicit allocation per participant, and a disclosed merit formula — it would clear every gate. The PDF TOS is stable and the fixed-per-identity model is exactly what the A1 thesis needs. Everything else in the registry is structurally farther from actionability.

---

## Phase-0 Verdict (by locked thresholds)

Applying the pre-registered Freeze threshold rules from `docs/evidence_portfolio/A1_THRESHOLD_LOCK.md`:

| Program | Verdict | Reason |
|---|---|---|
| coinlist-token-sale | **Kill** (unverifiable) | 100% hash churn, no stable artifact, no live round |
| legion-merit-sale | **Freeze** (no round) | Stable artifact, wrong URL (platform TOS not sale round), merit-score undisclosed |
| layer3-quests | **Freeze** (negative EV) | Unannounced points → realizable_price=0 → net EV ≤ 0 |
| galxe-quests-oat | **Kill** (zero realizable) | OAT/NFT has no realizable market value |
| kaito-yaps | **Freeze** (negative EV) | Unannounced points → realizable_price=0 → net EV ≤ 0 |

Zero-capital mapping applies: cash net profit = $0, economic profit between −$25 and +$25 with operator time. All 5 classify as Freeze or Kill — **no program earns a Phase-1 capital-deployed proposal from this baseline**.

---

## Recommendations

### 1. Do not start Phase-1 from this baseline

No program clears verification + promotion + EV. Deploying capital now would violate the pre-registered caps and gate framework.

### 2. Update the registry with round-specific URLs

For any program type that shows a live round: replace the generic doc URL with the specific sale/round page. This is the minimum to make verification meaningful.

### 3. Add content-stripping for HTML captures

The tooling should strip nonces, scripts, meta tags, and analytics tokens before hashing HTML captures. This would make Coinlist's FAQ hashes stable (all 21 captures are semantically identical — the FAQ hasn't changed). Without this, web-page verification is noise, not signal.

### 4. Watch Legion for a sale announcement

Legion is the structural best-fit for A1. When they announce a specific sale: capture the sale page, confirm the merit formula is disclosed, run verification + EV. This is the program most likely to clear all gates first.

### 5. Remove Galxe-OAT from active research

OAT rewards have no realizable market value. This program cannot produce positive EV under the formula regardless of probability inputs. It's useful as a classification fixture (IN_CONDITIONAL archetype) but should move to controls.

### 6. Consider adding a new active-research program

The A1 thesis (inverse scale, per-identity-capped, capital-independent rewards) is sound. The registry has 5 IN_CORE programs but only 2 were active. The 17-program universe is comprehensive — the constraint is not program discovery but **live, measurable rounds**. Run a periodic sweep (weekly) against the registry to check which programs have active rounds.

### 7. Wait for next systemd tick or re-enable

The baseline is closed — the `systemd` timer at 06:00 UTC will now fail on `baseline status` (no RUNNING baseline exists). No new observations will be captured until a new baseline is started. This is correct — Phase-0 observation is complete.

---

## Next Steps for Operator

1. **Read and accept this report** — it formalizes Phase-0 closure
2. **Watch Legion for a sale announcement** — check `app.legion.cc` periodically for specific sale rounds
3. **Update registry URLs** when concrete rounds appear — edit `research/a1-incentive-farming/starter-registry-v0.yaml` and freeze a new baseline
4. **Do not deploy capital** — Phase-1 is not authorized from this baseline
5. **Decision: keep tooling running?** The `systemd` timer will fail silently until a new baseline is started. Either: (a) disable the timer, (b) start a new baseline with updated URLs, or (c) leave it to fail silently

---

## Appendix: Capture Stability Detail

| Program | Ticks | Changed | Stable | Churn % | Artifact type |
|---|---|---|---|---|---|
| coinlist-token-sale | 19 | 19 | 0 | 100% | HTML (CDN noise) |
| legion-merit-sale | 19 | 1 | 18 | 5% | PDF (stable) |
| layer3-quests | 19 | 13 | 6 | 68% | HTML |
| galxe-quests-oat | 19 | 13 | 6 | 68% | HTML |
| kaito-yaps | 19 | 13 | 6 | 68% | HTML |
