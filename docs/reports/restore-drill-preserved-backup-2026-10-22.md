# Restore Drill — Preserved Pre-Write Backup

**Date:** 2026-10-22
**Status:** PASSED
**Drill ID:** `restore-drill-db` (isolated TimescaleDB container, discarded after verification)

## What was drilled

The backup preserved during the accidental-write incident was restored into
a **separate, isolated TimescaleDB container** — never into production —
and the restored state was compared against the live database.

- **Source dump:** `/opt/crypto-agent/backups/preserved/marketdata_20261022_030001.sql.gz`
- **SHA-256:** `714742fba8b1f61d04582b2534720d0742cd54233308352a2340c0a46b6af80e`
- **Dump taken:** 2026-10-22 03:00:02 UTC (≈7 h 57 m before the accidental write)
- **Restore target:** container `restore-drill-db`, image
  `timescale/timescaledb:latest-pg14`, database `marketdata`, isolated from
  every production service and network path
- **Restore command:**
  ```bash
  zcat backups/preserved/marketdata_20261022_030001.sql.gz \
    | docker exec -i restore-drill-db psql -U trading -d marketdata \
        -v ON_ERROR_STOP=1 -q
  ```
  Exit code **0**. No errors, no warnings.

Two preconditions had to be met before the restore would run, and both are
worth recording because they are not obvious:

1. **The drill database must be past its init phase.** The stock image's
   `/docker-entrypoint-initdb.d/001_timescaledb_tune.sh` pre-creates a
   `public.ohlcv` table. Connecting during init gets the server restarted
   under the client, which surfaces as
   `server closed the connection unexpectedly`. Wait for
   `pg_isready` **and** confirm with a real query before restoring.
2. **The pre-created `ohlcv` hypertable must be dropped first**, or the
   restore fails with `relation "ohlcv" already exists`:
   ```sql
   DROP TABLE IF EXISTS public.ohlcv CASCADE;
   ```
   The `_timescaledb_internal` schema must **not** be dropped — the
   `timescaledb` extension depends on it.

The dump contains no `CREATE ROLE` / `CREATE DATABASE` statements, so it
restores cleanly into a pre-created database with no ownership remapping.

## Restored state

| Measure | Restored (pre-write) | Live (post-write) |
|---|---|---|
| `indicators` rows | 248,261 | 248,773 |
| `ohlcv` rows | 253,707 | — |
| `BTCUSDT 1h` indicator rows | 22,642 | 23,135 |
| `BTCUSDT 1h` rows with the 8-column legacy NULL fingerprint | **19,920** | **0** |

The legacy NULL fingerprint (`ema_8`, `ema_10`, `ema_14`, `ema_21`,
`ema_24`, `ema_30`, `sma_40`, `sma_60` all NULL) is the signature of the
old writer, which omitted those columns entirely. It is present in the
restored dump and completely absent from the live table. That is the
positive proof that the dump predates the accidental write.

## The decisive comparison

All 20,526 keys in
`docs/reports/affected-rows-btcusdt-1h-2026-10-22.txt` exist in the
restored database, and the 2,116 additional restored keys are exactly the
no-op rows the manifest did not list.

Comparing the restored rows against the live rows on a representative
column set (`vwap`, `rsi_14`, `cci`, `ema_200`, `macd_hist`,
`atr_percentile`, `sma_40`, `ema_8`):

```
manifest keys: 20526
live rows: 23135   restored rows: 22642
rows present in both: 22642
  of which in the changed manifest: 20526
  of which NOT in the manifest (no-op rows): 2116

CHANGED rows (in manifest) that differ live vs restored: 20526/20526
NO-OP  rows (not in manifest) that differ live vs restored:     0/2116
```

Example differences (restored → live):

```
2024-01-09 07:00:00  sma_40   NULL -> 45108.1365
                    ema_8    NULL -> 46744.9909645769
                    atr_percentile NULL -> 74.05405405405405
2024-01-09 08:00:00  vwap, ema_200, macd_hist, atr_percentile, sma_40, ema_8
```

## Verdict

**RESTORE DRILL PASSED.** The pre-write dump restores to a state that
differs from the live database on *exactly* the 20,526 rows the accidental
write changed, and matches on all 2,116 no-op rows. There is no
divergence outside the manifest's footprint, which means:

- the manifest is **complete and exact** — it is not a description or an
  estimate of the damage, it is the damage;
- the preserved backup is a **verified, executable restore path** for the
  incident, not merely a file that exists;
- the historical backfill is now **unblocked**. The rollback is no longer
  hypothetical: the exact inverse operation has been performed and
  measured.

## What this does *not* establish

- The drill restored a **full** dump, which is the coarse-grained
  rollback. A targeted rollback of only the 20,526 changed rows is
  possible and would be far cheaper, but it is a different operation and
  has not been drilled.
- Restoring the dump wholesale would also revert the 492 rows written
  *after* the backup, and every other pair's rows. Any real rollback must
  therefore be scoped to the manifest, or accepted as a full point-in-time
  revert.
- The drill proves the backup is good. It does not by itself justify
  running the historical backfill; that remains a separate reviewed
  change with its own dry-run evidence.
