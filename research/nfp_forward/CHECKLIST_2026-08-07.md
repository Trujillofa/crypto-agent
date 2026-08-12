# NFP forward capture — 2026-08-07

**Gate:** `docs/evidence_portfolio/NFP_FORWARD_GATE.md`
**Tool:** `scripts/nfp_forward_capture.py`

## Verified 2026-08-05

- BLS (via public schedule / prior release footer): Employment Situation for July 2026 on **Friday, August 7, 2026, 08:30 ET** (12:30 UTC).
- Investing.com NFP page lists **Aug 07, 2026**.
- Unit tests: 17/17 passed.
- `pre` correctly refuses outside 24h window (tested).
- Capture window: **2026-08-06T12:30:00Z → 2026-08-07T12:30:00Z**.

## Automated pre

- Runner: `research/nfp_forward/run_pre_2026-08-07.sh`
- Target fire: **2026-08-06 12:32 UTC** (2 min after window open)
- Log: `research/nfp_forward/pre-2026-08-07.log`
- Pending stash (after success): `research/nfp_forward/pending_capture.json`

## Human after release (2026-08-07 ~12:30 UTC+)

1. Open the Wayback snapshot URL from pending JSON.
2. Read **consensus** (forecast) from that frozen page.
3. Read **actual** headline NFP change (thousands) from BLS Employment Situation.
4. Run:

```bash
cd /home/yderf/Projects/trading/TRADING/crypto-agent
uv run python scripts/nfp_forward_capture.py post \
  --actual <n> \
  --consensus <n>
```

5. Commit the new row in `data/macro_events/nfp_good_news_forward.csv` when ready.

## If pre fails / missed

```bash
uv run python scripts/nfp_forward_capture.py miss --release-date 2026-08-07
```

Three misses cap the sample — do not use miss lightly.
