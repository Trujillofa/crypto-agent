# Grafana Alerting Configuration

This directory contains Grafana alerting rules and notification configurations for the crypto trading agent.

## Overview

The alerting system monitors:
- **Price Threshold Alerts**: Triggers when crypto prices go above/below configured levels
- **Volume Spike Detection**: Alerts when trading volume exceeds historical averages
- **Data Ingestion Health**: Monitors data freshness and agent health

## Alert Rule Files

### Price Alerts (`price_alerts.yml`)
Monitors cryptocurrency price levels:
- **Bitcoin (BTCUSDT)**:
  - Warning: Above $100,000
  - Critical: Below $90,000
- **Ethereum (ETHUSDT)**:
  - Warning: Above $5,000
  - Critical: Below $3,000
- **Solana (SOLUSDT)**:
  - Warning: Above $200
  - Critical: Below $150

### Volume Alerts (`volume_alerts.yml`)
Detects unusual trading volume:
- Alerts when current volume is 2.5x above 1-hour moving average
- Monitors BTC, ETH, and SOL

### Ingestion Alerts (`ingestion_alerts.yml`)
System health monitoring:
- **Data Stale**: No new data received for 5+ minutes
- **Agent Down**: Crypto agent not responding to health checks
- **Ingestion Errors**: High error rate in data ingestion pipeline

## Notification Channels

Configured in `notifiers/alerting.yml`:

### Email Alerts
- Default notification channel
- Grouped by alert severity and symbol
- Repeat intervals: Critical (2m), Price alerts (30m), Volume alerts (1h)

### Webhook Alerts
- For critical and system alerts
- Immediate notification (10s wait time)
- Repeat interval: 2-5 minutes

### Discord Alerts
- For price alerts
- Configured via Discord webhook (URL needs to be added)

## Configuration

### Email Settings
Edit `notifiers/alerting.yml`:
```yaml
settings:
  addresses: trading-alerts@example.com  # Change to your email
```

### Discord Webhook
Edit `notifiers/alerting.yml`:
```yaml
settings:
  url: "https://discord.com/api/webhooks/..."  # Add your Discord webhook URL
```

### Custom Webhook Endpoint
Edit `notifiers/alerting.yml`:
```yaml
settings:
  url: http://localhost:8080/alerts  # Change to your endpoint
```

## Alert Thresholds

### Adjusting Price Thresholds

Edit `alerts/price_alerts.yml`:

```yaml
- alert: BitcoinPriceAbove100k
  expr: |
    SELECT close_price FROM ohlcv
    WHERE symbol = 'BTCUSDT'
    ORDER BY time DESC LIMIT 1
    > 100000  # Change this value
```

### Adjusting Volume Thresholds

Edit `alerts/volume_alerts.yml`:

```yaml
WHERE (v2.volume::FLOAT / v1.avg_volume::FLOAT) > 2.5  # Change 2.5 to desired multiplier
```

### Adjusting Stale Data Thresholds

Edit `alerts/ingestion_alerts.yml`:

```yaml
HAVING EXTRACT(EPOCH FROM (NOW() - MAX(time))) > 300  # Change 300 to desired seconds
```

## Dashboard Annotations

The dashboard includes visual annotations for alerts:
- Yellow markers: Price above high threshold
- Red markers: Price below low threshold
- Orange markers: Data stale (> 5 min)

## Testing Alert Rules

### Via Grafana UI
1. Navigate to Alerting → Alert Rules
2. Review all configured rules
3. Click on an alert to see details and evaluation

### Via Grafana API
```bash
# List all alert rules
curl -u admin:securepass123 \
  http://localhost:3001/api/v1/provisioning/alert-rules

# Test an alert evaluation
curl -u admin:securepass123 \
  http://localhost:3001/api/v1/rules
```

## Notification Policies

### Critical Alerts
- **Severity**: Critical
- **Channel**: Webhook + Email
- **Response Time**: 10 seconds
- **Repeat**: Every 2-5 minutes

### Price Alerts
- **Severity**: Warning/Critical
- **Channel**: Email + Discord (optional)
- **Response Time**: 1 minute
- **Repeat**: Every 30 minutes

### Volume Alerts
- **Severity**: Warning
- **Channel**: Email
- **Response Time**: 5 minutes
- **Repeat**: Every 1 hour

### System Alerts
- **Severity**: Critical
- **Channel**: Webhook
- **Response Time**: 10 seconds
- **Repeat**: Every 2 minutes

## Troubleshooting

### Alerts Not Firing

1. **Check Alert Rule Status**:
   - Navigate to Alerting → Alert Rules
   - Verify rules are in "Normal" state

2. **Check Data Source**:
   - Navigate to Configuration → Data Sources
   - Verify TimescaleDB datasource is healthy

3. **Check Query**:
   - Click on alert rule
   - Check "Preview query" tab
   - Verify data is being returned

### Notifications Not Sending

1. **Check Contact Point**:
   - Navigate to Alerting → Contact points
   - Test each contact point

2. **Check Notification Policies**:
   - Navigate to Alerting → Notification policies
   - Verify routing is correct

3. **Check Grafana Logs**:
   ```bash
   docker-compose logs grafana
   ```

### Too Many Alerts

1. **Adjust Thresholds**: Modify alert rules to be less sensitive
2. **Increase Group Wait**: Increase `group_wait` in `notifiers/alerting.yml`
3. **Increase Repeat Interval**: Increase `repeat_interval` in policies

## Maintenance

### Adding New Alerts

1. Create or edit alert rule file in `alerts/`
2. Follow existing syntax:
   ```yaml
   - alert: AlertName
     expr: "YOUR_SQL_QUERY"
     for: "duration"
     labels:
       severity: warning|critical
     annotations:
       summary: "Brief summary"
       description: "Detailed description"
   ```

3. Restart Grafana to load changes:
   ```bash
   docker-compose restart grafana
   ```

### Disabling Alerts

Temporarily disable alerts by setting `enable: false`:
```yaml
- alert: AlertName
  enable: false
```

### Alert Silence

Create a mute time entry in `notifiers/alerting.yml`:
```yaml
mute_times:
  - name: Maintenance Window
    intervals:
      - start_time: "2024-01-01T02:00:00Z"
        end_time: "2024-01-01T04:00:00Z"
```

## Best Practices

1. **Test Before Production**: Test alert rules in a development environment first
2. **Monitor Alert Fatigue**: Too many alerts reduce effectiveness
3. **Use Severity Levels**: Distinguish between warning and critical alerts
4. **Set Appropriate Thresholds**: Avoid false positives and false negatives
5. **Review Regularly**: Periodically review and adjust alert thresholds
6. **Document Changes**: Keep track of alert rule changes in git

## Related Documentation

- [Grafana Alerting Documentation](https://grafana.com/docs/grafana/latest/alerting/)
- [TimescaleDB Alerting](https://docs.timescale.com/timescaledb/latest/use-cases/monitoring-alerting/)
- [Prometheus Alerting Rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)

## Support

For issues or questions about alerting configuration:
1. Check Grafana logs: `docker-compose logs grafana`
2. Review alert rules in Grafana UI
3. Consult Grafana documentation
