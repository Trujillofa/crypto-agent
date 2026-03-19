# Paper Validation Report

`paper_validation_report` summarizes the paper trading agents for one UTC day.

It combines:
- event-log activity
- daily closed-trade stats from the database
- cumulative portfolio stats
- risk-state flags

Current monitored paper agents:
- `agent_sol_sparse`
- `agent_sentiment_macro`
- `agent_avax`

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
