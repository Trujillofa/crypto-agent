# Thermonuclear Repo Review — Task Brief (for Grok)

**Owner:** Grok (builder)
**Author of brief:** Claude (planner/reviewer)
**Branch:** `chore/thermonuclear-repo-review`
**Scope decision (human-approved 2026-07-01):** **Safe-but-thorough.** Aggressive
cleanup + refactor + doc consolidation, but **no structural moves that break running
production agents or the incentive_ops content-hash baseline.**

---

## Mission

Do a no-mercy pass over the repository: delete dead code, remove stray artifacts,
consolidate documentation, refactor what genuinely needs it, and tighten the layout —
**without touching frozen or prod-critical paths.** Apply the project's own 5-step
framework (Requirements → Delete → Simplify → Accelerate → Automate). Delete first;
refactor second; never refactor something that should be deleted.

Every phase must end with **`uv run pytest -v` green** and
**`uv run ruff check . && uv run ruff format --check .` clean.**

---

## HARD CONSTRAINTS — do not cross these lines

| Frozen / off-limits | Why |
|---|---|
| `src/` import paths (module locations) | Live prod agents import these; `Dockerfile.prod` + `docker-compose.prod.yml` bake them in. Moving a module breaks running agents. |
| `tools/incentive_ops/**` | Under a **content-hash baseline freeze until 2026-07-11**. Any move/edit invalidates the baseline. Leave it completely alone. |
| `Dockerfile.prod`, `docker-compose.prod.yml` paths | Deploy gate. Path changes silently break prod builds. |
| `config/*.yaml` agent configs | Referenced by `AGENT_ID` at runtime; renaming breaks live agents. |
| `migrations/*.sql` | Applied schema; never rename/reorder. |
| Root agent-instruction files: `CLAUDE.md`, `AGENTS.md`, `README.md`, `copilot-instructions.md`, `codex-instructions.md`, `gemini-instructions.md`, `sisyphus-instructions.md` | Agent tooling looks for these at repo root. Keep them at root. |

If you believe a frozen path *must* change, **stop and leave a PR comment** — do not do it.

---

## In-scope work (the checklist)

### 1. Delete stray artifacts
Confirmed clutter to remove from the repo (and add to `.gitignore` where relevant):

- `wfo_results.csv` — **tracked** build artifact at root. Delete + gitignore the pattern.
- `sweep_20260611_151913.log`, `sweep_20260611-1559.log` — stray logs (untracked; ensure `*.log` is ignored, remove from working tree).
- `.coverage` — untracked coverage artifact; ensure ignored.
- `.venv_new/` — a second stray virtualenv; remove from working tree, confirm ignored.
- `backups/` — verify it holds nothing tracked/needed; ignore or remove.
- Sweep for other stray `*.log`, `*.csv`, editor/OS junk at root and in `scripts/`.

### 2. Dead code elimination (the core of the review)
- Unused imports, unreachable branches, commented-out blocks — delete, don't preserve.
- Functions/classes with zero references across `src/`, `scripts/`, `tests/`.
- Redundant wrappers/abstractions with a single implementation — inline them.
- "Just in case" config flags nobody toggles and fallback paths never triggered.
- Use tooling to find candidates, then verify each by hand before deleting:
  - `uv run ruff check . --select F401,F811,F841` (unused imports/vars/redefs)
  - `vulture src scripts --min-confidence 80` (dead code; treat as *candidates*, not truth)
- **Litmus test:** if you didn't have to add something back that you deleted, you didn't delete enough.

### 3. Documentation consolidation
Root has 14 markdown files. Keep the **7 agent-instruction files** at root (see constraints
table). The rest are movable into `docs/` (add stub redirects only if something references
the old path — grep first):

- `AI_TRADING_AGENT_NEXT_STEPS.md` → `docs/`
- `CODE_REVIEW.md` → `docs/`
- `DEPLOYMENT.md` → `docs/` (reconcile with existing deploy docs)
- `QUICKREF.md` → `docs/`
- `SERVER_ACCESS.md` → `docs/`
- `SPEC.md` → `docs/`
- `USAGE.md` → `docs/`

Before moving any doc: `grep -rn "USAGE.md"` (etc.) across the repo and fix references.
Merge overlapping/stale docs rather than moving duplicates.

### 4. Internal refactor (only what survives deletion)
- Simplify without moving module locations: reduce nesting via early returns, extract
  well-named booleans, kill nested ternaries, replace magic numbers with named constants.
- Respect the 100-char line length and existing `snake_case`/`PascalCase` conventions.
- Keep changes surgical — do **not** rewrite modules wholesale. This is cleanup, not a rewrite.

### 5. Folders (allowed, within limits)
- You **may** create folders under `docs/`, `scripts/`, and `tests/` to group loose files,
  as long as no `src/` import path or prod-referenced script path changes.
- Prefer grouping `scripts/` by purpose (e.g. `scripts/rbi/`, `scripts/backtest/`) **only if**
  nothing (CI, systemd timers, `run_rbi_loop_batch.sh`, docs) references the old path — grep and update every hit.

---

## Working method (staged, reviewable commits)

Do this as a **sequence of small commits**, each independently green, so review is possible:

1. `chore: remove stray artifacts + tighten .gitignore`
2. `refactor: delete dead code (unused imports/vars/functions)`
3. `docs: consolidate root docs into docs/`
4. `refactor: simplify <module> internals` (one focused commit per area)

After **each** commit:
```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -v
```

Conventional Commits, scoped per `CLAUDE.md`. Co-author line:
`Co-Authored-By: Grok <noreply@x.ai>` (or the identity Grok uses).

---

## Acceptance criteria

Status checked on 2026-07-08 against the local tree. `uv run pytest -v`,
`uv run ruff check .`, `uv run ruff format --check .`, and `git diff --check`
passed. The current diff is docs-only.

- [x] `uv run pytest -v` green; `ruff check` + `ruff format --check` clean.
- [x] No changes under `tools/incentive_ops/**`; no `src/` module relocations; no prod Docker/config path changes.
- [x] Root artifact files (`wfo_results.csv`, stray logs, `.coverage`, `.venv_new`) gone and gitignored.
- [x] Root markdown reduced to the 7 agent-instruction files + `README.md`; the rest live under `docs/` with references updated.
- [ ] Every deleted symbol verified unreferenced (grep evidence in the PR description or commit body).
- [x] Diff is reviewable: small, purpose-scoped commits — not one giant blob.

## Out of scope (explicitly)
- Behavior changes to any strategy, risk, or execution logic.
- Moving/renaming anything in `src/`, `tools/incentive_ops/`, `migrations/`, or prod config.
- New features, new dependencies, dependency upgrades.
