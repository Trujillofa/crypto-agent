# Production Drift Sentinel

`production_drift_sentinel` checks parity between local and production environments.

It detects drift in:

- Git branch and commit
- Dirty worktree state
- `config/settings*.yaml` file hashes
- Docker Compose service runtime status
- Recent signal activity (consensus signal drought/staleness)

## One Command

```bash
. .venv/bin/activate && python scripts/production_drift_sentinel.py
```

## Common Flags

```bash
python scripts/production_drift_sentinel.py \
  --expected-branch main \
  --remote-host crypto-agent \
  --remote-dir /opt/crypto-agent \
  --signal-stale-hours 24 \
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
