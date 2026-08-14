# NFP forward capture — 2026-09-04

**Gate:** `docs/evidence_portfolio/NFP_FORWARD_GATE.md`
**Tool:** `scripts/nfp_forward_capture.py`
**Prior:** 2026-08-07 booked as `NFP_MISSED_CAPTURE` (1 of 3) after Wayback SPN
`Connection reset by peer`. Capture tool now retries SPN and accepts `--snapshot-url`.

## Verify before the window (human)

- [ ] BLS Employment Situation schedule confirms **Friday, September 4, 2026, 08:30 ET**
  (12:30 UTC): https://www.bls.gov/schedule/news_release/empsit.htm
- [ ] Investing.com NFP page lists **Sep 04, 2026** (or equivalent).
- [ ] Smoke-test Wayback Save Page Now *before* the window (or have archive.ph /
  manual SPN ready for `--snapshot-url`).
- Capture window: **2026-09-03T12:30:00Z → 2026-09-04T12:30:00Z**.

## Automated pre

- Runner: `research/nfp_forward/run_pre_2026-09-04.sh`
- Target fire: **2026-09-03 12:32 UTC** (2 min after window open)
- Log: `research/nfp_forward/pre-2026-09-04.log`
- Pending stash (after success): `research/nfp_forward/pending_capture.json`

If SPN still fails after retries, capture a PIT mirror manually (Wayback UI or
archive.ph), then:

```bash
uv run python scripts/nfp_forward_capture.py pre \
  --release-date 2026-09-04 \
  --snapshot-url '<frozen-url>'
```

## Human after release (2026-09-04 ~12:30 UTC+)

1. Open the snapshot URL from pending JSON.
2. Read **consensus** (forecast) from that frozen page.
3. Read **actual** headline NFP change (thousands) from BLS Employment Situation.
4. Run:

```bash
uv run python scripts/nfp_forward_capture.py post \
  --actual <n> \
  --consensus <n>
```

5. Commit the new row in `data/macro_events/nfp_good_news_forward.csv` when ready.

## If pre fails / missed

```bash
uv run python scripts/nfp_forward_capture.py miss \
  --release-date 2026-09-04 \
  --note 'reason'
```

Three misses cap the sample — do not use miss lightly. Current budget used: **1/3**.
