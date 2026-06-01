# Production Drift Sentinel

`production_drift_sentinel` checks parity between local and production environments.

It detects drift in:

- Git branch and commit
- Dirty worktree state
- deploy-relevant `config/settings*.yaml` file hashes
- Docker Compose service runtime status
- Recent signal activity (consensus signal drought/staleness)

Service discovery is dynamic. The sentinel reads
`docker compose -f docker-compose.prod.yml config --services` on the remote host, so newly
added services such as `agent_sol_sparse` are included automatically without code changes.
Research-only configs such as `config/settings.autoresearch.yaml` are excluded from deploy parity checks.

## One Command

```bash
. .venv/bin/activate && python scripts/production_drift_sentinel.py
```

## Common Flags

```bash
python scripts/production_drift_sentinel.py \
  --ssh-config ~/.ssh/config \
  --expected-branch main \
  --remote-host crypto-agent \
  --remote-dir /opt/crypto-agent \
  --watch-service agent_sol_sparse \
  --signal-stale-hours 24 \
  --log-tail 500 \
  --fail-on error
```

For the SOL sparse paper rollout, use watched-service mode so the report includes
`agent_sol_sparse`-specific strategy-cycle, consensus-signal, and paper-order visibility:

```bash
python scripts/production_drift_sentinel.py \
  --ssh-config ~/.ssh/config \
  --expected-branch main \
  --remote-host crypto-agent \
  --remote-dir /opt/crypto-agent \
  --watch-service agent_sol_sparse \
  --signal-stale-hours 72 \
  --log-tail 500 \
  --fail-on error
```

Local-only mode (no SSH):

```bash
python scripts/production_drift_sentinel.py --local-only
```

JSON output:

```bash
python scripts/production_drift_sentinel.py --json
```

## Output Artifacts

By default it writes report files to `docs/reports/`:

- `production-drift-sentinel-<timestamp>.md`
- `production-drift-sentinel-<timestamp>.json`

Override path/prefix with `--output-prefix`.
