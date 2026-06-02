# Production Drift Sentinel

`production_drift_sentinel` checks parity between local and production environments.

It detects drift in:

- Git branch and commit
- Dirty worktree state
- deploy-relevant `config/settings*.yaml` file hashes
- Docker Compose service runtime status
- Required systemd timer status, oneshot result, and per-timer report artifact freshness
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

Production-local mode runs the full inspection directly on the server without nested SSH:

```bash
python scripts/production_drift_sentinel.py \
  --production-local \
  --remote-dir /opt/crypto-agent \
  --watch-service agent_sol_sparse \
  --signal-stale-hours 72 \
  --fail-on error \
  --output-prefix data/reports/production-drift-sentinel
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

## Hourly Production Audit

Install and enable the systemd timer on the production host:

```bash
sudo install -m 0644 ops/systemd/crypto-agent-production-drift-sentinel.service /etc/systemd/system/
sudo install -m 0644 ops/systemd/crypto-agent-production-drift-sentinel.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-agent-production-drift-sentinel.timer
```

The service expects production to deploy from `main`. During an intentional feature-branch
rollout, set an explicit override before starting the service:

```bash
printf 'EXPECTED_DEPLOY_BRANCH=%s\n' fix/example-rollout |
  sudo tee /etc/default/crypto-agent-production-drift-sentinel
```

Inspect the timer and its latest report:

```bash
systemctl status crypto-agent-production-drift-sentinel.timer
journalctl -u crypto-agent-production-drift-sentinel.service --no-pager -n 100
ls -1t data/reports/production-drift-sentinel-*.json | head
```

Each hourly audit also verifies its own timer and latest artifact. A missing artifact or an
artifact older than two hours fails the audit. Daily paper-validation artifacts retain a
36-hour freshness window.
