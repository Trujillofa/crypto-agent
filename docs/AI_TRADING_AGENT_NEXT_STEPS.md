# Manual Next Steps (Human-Only Actions)

> Status checked on 2026-07-08. This is an early bootstrap checklist, not the
> current production runbook. Use `CLAUDE.md`, `AGENTS.md`, `docs/DEPLOYMENT.md`,
> and `docker-compose.prod.yml` for current repo and deploy behavior.

This file lists **manual** steps that must be performed outside the codebase. Each item explains **why** it matters, **what to do**, and **how to verify**.

---

## 1) Populate `.env` with real secrets

**Why:** The agent needs real API keys and secure passwords to connect to external services. Empty or default values are unsafe.

**What to do (manual):**
1. Copy `.env.example` to `.env` if you haven’t already.
2. Replace placeholders with real values:
   - `BINANCE_API_KEY`
   - `BINANCE_API_SECRET`
   - `POSTGRES_PASSWORD`
3. Ensure the file is **not** committed to git.

**Verify:**
- `.env` exists and contains no placeholder values.
- `git status` does **not** show `.env` as tracked.

---

## 2) Create and run database migrations (TimescaleDB)

**Why:** The app currently creates tables at runtime. A migration file makes schema changes explicit, repeatable, and auditable.

**What to do (manual):**
1. Create a `migrations/001_initial.sql` file with the schema for `ohlcv`.
2. Run the migration against TimescaleDB using `psql`.
3. Store subsequent changes as new migration files (`002_...`, `003_...`).

**Verify:**
- `psql` shows the `ohlcv` table exists.
- `timescaledb_information.hypertables` includes `ohlcv`.

---

## 3) Decide on persistence mode (TimescaleDB only vs. SQLite fallback)

**Why:** The current code falls back to SQLite if TimescaleDB is unreachable. For production, you may want to **disable fallback** to avoid silent data divergence.

**What to do (manual):**
- Decide which mode you want in production.
  - **Option A:** Require TimescaleDB and fail fast if down.
  - **Option B:** Keep SQLite fallback but add explicit alerts.
- Update your runbook and monitoring accordingly.

**Verify:**
- If TimescaleDB is down, the agent either stops (Option A) or logs the fallback clearly (Option B).

---

## 4) Configure Prometheus/Grafana access controls

**Why:** Exposing monitoring dashboards without auth is a security risk. **Note:** “Prometheus” here refers to the monitoring system, not an AI agent.

**What to do (manual):**
1. Set strong credentials for Grafana in `.env` or `docker-compose`.
2. Add basic auth to Prometheus (or reverse proxy it behind auth).

**Verify:**
- Grafana requires login.
- Prometheus requires authentication before showing metrics.

---

## 5) Enable health checks and production Docker settings

**Why:** Production deployments need health checks, resource limits, and non-dev volumes.

**What to do (manual):**
1. Create a `docker-compose.prod.yml` that:
   - Adds health checks for agent/DB
   - Sets CPU/memory limits
   - Removes bind mounts used for local dev
2. Use the production compose file in your deployment.

**Verify:**
- `docker compose -f docker-compose.prod.yml ps` shows all services healthy.

---

## 6) Add config validation (policy decision)

**Why:** Misconfigured deployments fail late and are hard to debug.

**What to do (manual):**
1. Decide the schema rules for required config values (env + YAML).
2. Decide whether to enforce these as hard failures at startup.

**Verify:**
- The agent exits with a clear error if required config is missing.

---

## 7) Strengthen metrics labeling policy

**Why:** High-cardinality labels can overload Prometheus.

**What to do (manual):**
1. Decide which labels are allowed (e.g., `symbol`, `stream`).
2. Confirm no dynamic/unbounded labels are introduced.

**Verify:**
- Prometheus queries show stable label sets.

---

## 8) Add risk management enforcement rules

**Why:** `risk.yaml` exists but is not enforced yet. Risk controls are required before any live trading.

**What to do (manual):**
1. Decide the concrete enforcement rules (max loss, max positions, circuit breaker thresholds).
2. Decide whether breaches stop the agent, pause trading, or notify.

**Verify:**
- Simulated breach triggers the expected behavior.

---

## 9) Add authentication for Binance private endpoints

**Why:** Live trading, account state, and order placement require signed requests.

**What to do (manual):**
1. Confirm API key permissions are set (read-only vs. trading).
2. Decide if this agent will ever place real orders or remain paper-only.

**Verify:**
- Private endpoint calls succeed with correct signatures.

---

## 10) Decide on async HTTP library upgrade (aiohttp)

**Why:** `urlopen` works but isn’t optimal for high concurrency or rate limits.

**What to do (manual):**
1. Decide whether to switch to `aiohttp` for true async I/O.
2. If yes, add a rate-limit strategy.

**Verify:**
- Concurrent symbol fetches work without thread pool pressure.

---

## 11) Expand test coverage targets

**Why:** Current tests are minimal and don’t cover error paths or DB writes.

**What to do (manual):**
1. Define a coverage target (e.g., 80%).
2. Add tests for:
   - Binance parsing
   - Config loading
   - Metrics labels
   - DB inserts/upserts
   - Error handling paths

**Verify:**
- `pytest --cov=src` meets the target.

---

## 12) Create a production deployment runbook

**Why:** You’ll need consistent steps to deploy safely.

**What to do (manual):**
1. Write `docs/DEPLOYMENT.md` with server requirements, ports, SSL, backups, and rollbacks.
2. Include validation commands for each step.

**Verify:**
- A new team member can deploy using the runbook without tribal knowledge.

---

## Bootstrap status

Status checked on 2026-07-08. Runtime-only items are listed as requirements
instead of checkboxes because secrets and exposure checks must not be recorded as
git-tracked completion without a dated deployment audit.

- Runtime requirement: `.env` must be filled with real secrets on the deployed
  host and must not be tracked by git.
- [x] DB migration files created and applied locally. The repo has migrations
  `000` through `007`; production application still needs live DB verification.
- [x] Persistence mode decision documented. The current architecture uses
  TimescaleDB; no SQLite fallback is part of the active runtime path.
- Runtime requirement: monitoring exposure and authentication must be verified
  on the deployed host. `docker-compose.prod.yml` currently notes nginx is
  disabled because Tailscale owns the host HTTPS listener.
- [x] Production compose file + health checks. `docker-compose.prod.yml` exists.
- [x] Config validation policy set. Current loading and validation live in
  `src/main.py`.
- [x] Metrics label policy defined. Label validation lives in
  `src/ingest/metrics.py`.
- [x] Risk rules enforced. Execution paths use risk guards/managers; live behavior
  still needs runtime checks before any promotion.
- [x] Binance private auth plan decided. Private auth is env-driven and documented
  in the current execution/deployment docs.
- [x] Async HTTP upgrade decision made. The codebase is async-first and uses
  `aiohttp`.
- [x] Tests expanded + coverage target met. Verified on 2026-07-08 with
  `uv run pytest -v`: 1203 passed.
- [x] Deployment runbook created. Current deploy guidance exists in
  `docs/DEPLOYMENT.md`, `AGENTS.md`, and `CLAUDE.md`.

---

## Evaluation Required: Use of OpenCode AI Agents in Trading Ops

**Why:** If you plan to use OpenCode agents (including any agent named “Prometheus”) in trading operations, you must evaluate safety, compliance, and operational boundaries first.

**What to do (manual):**
1. Define **exactly** what AI agents are allowed to do (read-only analysis vs. live actions).
2. Establish a **human-in-the-loop** approval policy for any trading-impacting changes.
3. Document **audit logging** for all AI-initiated actions.
4. Confirm the AI agent naming to avoid confusion with monitoring (e.g., “Prometheus (monitoring)”).
5. Decide if AI agents are allowed in production, staging-only, or sandbox-only.

**Verify:**
- A written policy exists and is approved by stakeholders.
- The deployment runbook references the AI policy and enforcement steps.
