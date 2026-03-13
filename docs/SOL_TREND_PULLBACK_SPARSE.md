# SOL Trend Pullback Sparse

This document is the operator runbook for the validated sparse-trend paper strategy.

## Baseline

- Config: `config/settings.sol_trend_pullback_sparse.yaml`
- Symbol/timeframe: `SOLUSDT 4h`
- Evaluator regime:
  - `--train-months 3`
  - `--test-months 2`
  - `--gate-profile sparse_trend_3_2`

Validated result:

- OOS windows: `7`
- Aggregate WFO trades: `4`
- OOS return: `7.72%`
- OOS mean Sharpe: `0.38`
- Max drawdown: `5.76%`
- Bootstrap P(loss): `24.60%`
- Profit concentration: `61.39%`

## Local Validation

```bash
./scripts/run_autoresearch.sh \
  --config config/settings.sol_trend_pullback_sparse.yaml \
  --description "sol trend pullback sparse preset" \
  --train-months 3 \
  --test-months 2 \
  --gate-profile sparse_trend_3_2
```

If you are running from the host instead of inside Docker, export the local DB overrides first:

```bash
set -a && source .env && set +a
export DB_HOST=127.0.0.1 DB_PORT=15432 DB_PASSWORD="$POSTGRES_PASSWORD"
```

## Paper Rollout

The dedicated paper service is `agent_sol_sparse` in [docker-compose.yml](/home/yderf/TRADING/crypto-agent/docker-compose.yml).

Deploy on the server after syncing the repo:

```bash
ssh crypto-agent "cd /opt/crypto-agent && git pull && docker compose up -d agent_sol_sparse"
```

Verify:

```bash
ssh crypto-agent "cd /opt/crypto-agent && docker compose ps agent_sol_sparse"
ssh crypto-agent "cd /opt/crypto-agent && docker compose logs agent_sol_sparse --tail=100 --no-log-prefix"
```

Drift and activity check:

```bash
python scripts/production_drift_sentinel.py \
  --expected-branch main \
  --remote-host crypto-agent \
  --remote-dir /opt/crypto-agent \
  --watch-service agent_sol_sparse \
  --signal-stale-hours 72 \
  --log-tail 500 \
  --fail-on error
```

Stop or remove the paper validation service:

```bash
ssh crypto-agent "cd /opt/crypto-agent && docker compose stop agent_sol_sparse"
ssh crypto-agent "cd /opt/crypto-agent && docker compose rm -f agent_sol_sparse"
```

## Guardrails

- Keep `mode: paper`
- Keep `trading_execution.test_mode: true`
- Do not reuse `agent` or `agent_2` for this strategy
- Do not evaluate this strategy under the standard gate profile

## Observation Window

This is a sparse strategy. A useful paper-validation window is:

- at least `4-6` weeks, or
- at least `10` completed trades,

whichever is later.

## Promotion Criteria

Promote only if paper behavior stays directionally aligned with the validated baseline:

- trade frequency remains sparse but non-zero
- realized drawdown stays below the research envelope
- paper entries remain consistent with the trend-pullback thesis
- no infrastructure drift or execution anomalies appear in logs
