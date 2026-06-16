# Builder brief — Fresh-DB migration bootstrap (diagnose before patching)

**Status:** QUEUED for builder (Grok). Created 2026-06-15 by reviewer (Claude).
**Type:** bug fix / schema reconciliation. **Branch:** `fix/migration-fresh-db-bootstrap`.
**Origin:** higher-tf-regime lane needed a clean local DB; the migration chain would not
bootstrap an empty database.

## Symptoms (observed on a fresh TimescaleDB)

1. `scripts/migrate.py` fails at `migrations/006_normalize_position_market_labels.sql`
   with `column "market" does not exist`, which **blocks 007–010** from applying. The
   regime lane had to apply 007–010 by hand (skipping 006) to proceed.
2. Migration `002_add_indicators_table.sql` does **not** reproduce the canonical
   `indicators` schema. The real/production schema is defined in
   `src/features/writer.py::_ensure_schema` (full EMA set incl. `ema_8`, all standard
   indicators, plus regime features). `scripts/compute_historical_indicators.py` (and
   the live writer) target that richer schema, so a migration-built table is missing
   columns (`ema_8`, extended EMAs, etc.) and inserts fail.

## Task — diagnose FIRST, then propose

This is live-adjacent schema; follow CLAUDE.md "diagnose before patching":

1. Determine **why 006 assumes a `market` column on a fresh DB** — which earlier
   migration was expected to create/rename it, and why that doesn't happen from empty.
   State the root cause and the 1–2 candidate fixes with evidence.
2. Determine the intended source of truth for `indicators` — migrations vs
   `writer.py::_ensure_schema` — and how they drifted.
3. **Post the diagnosis for review BEFORE editing any migration.** Do not land schema
   edits until the root cause is confirmed.

## Proposed direction (subject to review, not pre-approved)

- Make `scripts/migrate.py` run end-to-end on an empty DB (fix or guard 006).
- Reconcile the migration-built `indicators` table with `writer.py::_ensure_schema`
  (a migration that adds the missing columns, or a documented note that writer owns it).
- Verify: fresh DB + `scripts/migrate.py` + `compute_historical_indicators.py` for an
  arbitrary symbol/timeframe succeeds with regime features populated.

## Constraints (CLAUDE.md)

- Its own branch; **do not** touch `feat/higher-tf-regime-probe` or the probe.
- Migrations are append-only and idempotent (`IF NOT EXISTS`); don't rewrite history of
  already-applied migrations on prod — add a new corrective migration if needed.
- No live trading path changes. Tests + ruff green.
- Conventional commit, scope `db`; `Co-Authored-By: OpenCode <noreply@openai.com>`.

## Acceptance criteria

- Diagnosis reviewed and approved before any migration edit.
- After the fix, a clean DB bootstraps fully via `scripts/migrate.py` and the
  `indicators` schema matches `writer.py`.
