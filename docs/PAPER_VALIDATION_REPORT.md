# Paper Validation Report

`paper_validation_report` summarizes the paper trading agents for one UTC day.

It combines:

- event-log activity
- daily closed-trade stats from the database
- cumulative portfolio stats
- risk-state flags
- campaign metrics scoped to each validator's explicit rollout timestamp
- conservative paper-to-live review readiness based on campaign age and closed-trade count

Promotion decisions must use campaign metrics. Lifetime database metrics are retained only for
audit context because they can include rows from older strategy settings.

`ready_for_review` is not automatic promotion. A validator reaches that state only after at
least `28` days and `10` campaign closed trades. Promotion still requires human review of
PnL, win rate, drawdown, trade quality, and operational logs.

Current monitored paper agents:

- `agent_sol_sparse`
- `agent_sol_panic_block_paper`

`agent_sentiment_macro` is intentionally excluded because it routes live SOL futures orders.
`agent_avax` is disabled because its prior walk-forward edge did not persist in live trading.

## Manual Run

From the repo root:

```bash
scripts/run_paper_validation_report.sh
```

By default the wrapper reports on the previous UTC day and writes:

- `data/reports/paper-validation-report-YYYY-MM-DD.md`
- `data/reports/paper-validation-report-YYYY-MM-DD.json`

Override the day:

```bash
scripts/run_paper_validation_report.sh 2026-03-19
```

## Production Timer

Tracked systemd unit templates live in:

- `ops/systemd/crypto-agent-paper-validation-report.service`
- `ops/systemd/crypto-agent-paper-validation-report.timer`

Install on Hetzner:

```bash
sudo install -m 0644 ops/systemd/crypto-agent-paper-validation-report.service /etc/systemd/system/
sudo install -m 0644 ops/systemd/crypto-agent-paper-validation-report.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-agent-paper-validation-report.timer
```

The timer runs daily at `00:15 UTC` and generates the report for the prior UTC day.

Check status:

```bash
systemctl status crypto-agent-paper-validation-report.timer
systemctl list-timers crypto-agent-paper-validation-report.timer
journalctl -u crypto-agent-paper-validation-report.service --no-pager -n 100
```
