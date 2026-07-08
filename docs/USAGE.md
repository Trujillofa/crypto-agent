# Crypto-Agent Usage Guide

This guide covers everything you need to know about using the crypto-agent, from local development to production deployment. This is a fully automated Binance Spot trading agent with strategy engine, risk management, and execution capabilities.

---

## Project Overview

The crypto-agent provides reliable market data ingestion, technical indicator computation, and intelligent signal-based trading. It's built with Python 3.11+ using an async-first architecture for high-performance trading operations.

### Key Features

- **Market Data Ingestion**: Binance Spot API REST polling for OHLCV data
- **Data Storage**: TimescaleDB hypertables for efficient time-series storage
- **Technical Indicators**: EMA, RSI, MACD, Bollinger Bands, ATR computation
- **Strategy Engine**: Signal generation from indicator-based strategies
- **Trading Execution**: Automated order placement with Binance Spot API
- **Risk Management**: Pre-configured position limits, loss limits, circuit breakers
- **Monitoring**: Prometheus metrics, Grafana dashboards, structured logging

### Architecture

```
Binance Spot API (REST)
    ↓
OHLCV Ingestor → TimescaleDB
    ↓
Indicator Computer → Indicators Table
    ↓
IndicatorReader → StrategyEngine
    ↓
Signal Generation (BUY/SELL/HOLD)
    ↓
Risk Manager (validation)
    ↓
TradingExecutor → Binance Spot API
    ↓
Prometheus Metrics + Telegram Alerts
```

### Trading Capabilities

- **Spot Trading**: BUY/SELL market orders (no leverage)
- **Futures Trading**: USDⓈ-M perpetuals with 1-20x leverage (isolated margin, LONG-only for MVP)
- **Paper Trading Mode**: Test strategies without real capital (default)
- **Multiple Pairs**: Simultaneous trading across 10+ cryptocurrency pairs
- **Strategy Framework**: Extensible strategy architecture with 5 built-in strategies
- **Signal Aggregation**: Multi-strategy consensus before executing trades
- **Trading Mode Routing**: Route signals to spot or futures based on configuration

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Binance account with Spot API keys (for live trading)
- Linux/macOS (recommended) or WSL2 on Windows

### Local Development Setup

1. **Clone and navigate to the repository:**
   ```bash
   git clone https://github.com/Trujillofa/crypto-trading-agent.git
   cd crypto-trading-agent
   ```

2. **Create Python virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Set up environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env and add your Binance API credentials (optional for paper trading)
   ```

4. **Run tests to verify setup:**
   ```bash
   pytest -v
   ```

5. **Start the stack:**
   ```bash
   docker-compose up --build
   ```

6. **Access services:**
   - Prometheus: http://localhost:9090
   - Grafana: http://localhost:3000 (admin/admin)
   - Metrics: http://localhost:8000/metrics

---

## Testing

### Running Tests

Run all tests locally:

```bash
# Inside virtual environment
source .venv/bin/activate
pytest -v
```

Run tests inside Docker:

```bash
docker-compose exec agent pytest -v
```

### Test Coverage

The test suite includes 234 tests covering:

- Settings loading and validation
- Indicator computation accuracy
- Signal generation logic
- Risk management rules
- Order placement (mocked)
- Database connectivity and fallbacks
- Strategy integration

### Integration Tests

Run integration tests to verify the full pipeline:

```bash
# Test database connection
python scripts/test_db.py

# Test end-to-end data flow
python scripts/test_e2e.py

# Test risk management
python scripts/test_risk.py

# Test Binance connectivity
python scripts/test_binance.py
```

### Smoke Tests

Verify all components are working:

```bash
pytest scripts/smoke_test.py -v
```

This tests:
- Data ingestion
- Indicator computation
- Strategy signal generation
- Position tracking
- Telegram notifications
- Full pipeline integration

---

## Configuration

### Environment Variables

Create a `.env` file with your settings:

```bash
# Binance API credentials (optional for paper trading)
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# Database configuration
POSTGRES_HOST=timescaledb
POSTGRES_PORT=5432
POSTGRES_DB=marketdata
POSTGRES_USER=trading
POSTGRES_PASSWORD=your_secure_password

# Telegram notifications (optional)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_ENABLED=true
TELEGRAM_RATE_LIMIT=5

# Prometheus
PROMETHEUS_PORT=8000
```

### Trading Configuration

Edit `config/settings.yaml`:

```yaml
# Trading pairs and timeframe
trading:
  pairs:
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT
    - BNBUSDT
    - ADAUSDT
    - XRPUSDT
    - DOTUSDT
    - DOGEUSDT
    - AVAXUSDT
    - LINKUSDT
  timeframe: 1m  # 1m, 5m, 15m, 1h, etc.

# Strategy configuration
strategy:
  evaluation_interval_seconds: 60
  strategies:
    - name: simple_ma
      config:
        ema_short_period: 12
        ema_long_period: 26
    - name: rsi_reversal
      config:
        rsi_period: 14
        oversold_threshold: 30
        overbought_threshold: 70
    # ... more strategies
  aggregator:
    min_agreement: 2
    buy_threshold: 1.5
    sell_threshold: -1.5

# Trading execution
trading_execution:
  enabled: false  # Set to true to enable real trading
  test_mode: true  # Set to false for live trading
  order_size_usdt: 100.0
```

### Risk Management

Edit `config/risk.yaml`:

```yaml
position_limits:
  max_position_pct: 0.10      # Max 10% of portfolio per position
  max_open_positions: 5       # Max 5 simultaneous positions

loss_limits:
  max_daily_loss_pct: 0.05    # Stop trading after 5% daily loss
  max_drawdown_pct: 0.15      # Kill switch at 15% drawdown
  max_single_loss_pct: 0.02   # Max 2% loss per trade

circuit_breakers:
  consecutive_losses: 5       # Stop after 5 consecutive losses
  api_errors: 3               # Stop after 3 API errors
  latency_spike_ms: 5000      # Stop if latency > 5 seconds

kill_switch:
  enabled: true
  telegram_confirm: true      # Require Telegram confirmation to reset
```

---

## Trading Modes

### 1. Paper Trading (Default - Safe)

All components run, but no real orders are placed:

```yaml
trading_execution:
  enabled: false
  test_mode: true
```

**Use case:** Development, testing, strategy validation

### 2. Test Mode (API calls to testnet)

Real API calls to Binance Spot testnet with fake funds:

```yaml
trading_execution:
  enabled: true
  test_mode: true
```

**Use case:** End-to-end testing with fake funds

### 3. Live Trading (REAL MONEY - Use with caution)

Real orders on Binance Spot with real funds:

```yaml
trading_execution:
  enabled: true
  test_mode: false
```

**⚠️ WARNING:** Start with small order sizes and monitor closely.

---

## Futures Trading

**⚠️ EXTREME RISK WARNING ⚠️**

Futures trading involves leverage and carries a high risk of liquidation. You can lose your entire margin balance. Only trade with funds you can afford to lose. This is for advanced traders only.

### Overview

The agent now supports Binance USDⓈ-M perpetual futures trading with leverage. Key features:

- **Leverage**: Configurable 1-20x (default 5x, max 10x)
- **Isolated Margin**: MVP uses isolated margin only (safer than cross margin)
- **One-Way Positions**: LONG-only for MVP (SHORT positions in v2)
- **Liquidation Protection**: 5% buffer prevents orders near liquidation
- **Mark Price Monitoring**: Real-time liquidation risk alerts

### Configuration

Enable futures trading in `config/settings.yaml`:

```yaml
futures:
  enabled: true  # Enable futures trading
  symbols:
    - BTCUSDT
    - ETHUSDT
  default_leverage: 5      # Conservative default (1-20x allowed)
  max_leverage: 10         # Maximum leverage for this account
  margin_mode: isolated      # isolated only (cross margin excluded for safety)
  position_mode: one-way   # one-way only (hedge mode excluded)
  test_mode: true          # Use demo.binance.com for paper trading
  liquidation_buffer_pct: 5.0  # Block orders within 5% of liquidation
```

Add futures risk limits in `config/risk.yaml`:

```yaml
futures_limits:
  max_leverage: 10              # Max leverage allowed (hard cap 20x)
  liquidation_buffer_pct: 5.0   # Block orders near liquidation
  max_daily_loss_pct: 5.0       # Separate futures daily loss limit
  max_margin_usage_pct: 50.0    # Warn if using >50% of margin
  margin_mode: isolated         # isolated only
  position_mode: one-way        # one-way only
```

### Signal Routing

Strategy signals are routed based on `trading_mode`:

```python
# Spot trading (default)
signal = Signal(
    type=SignalType.BUY,
    symbol="BTCUSDT",
    trading_mode="spot",  # Routes to spot executor
    ...
)

# Futures trading
signal = Signal(
    type=SignalType.BUY,
    symbol="BTCUSDT",
    trading_mode="futures",  # Routes to futures executor
    ...
)
```

### LONG-Only MVP

The futures MVP is **LONG-only**:

- **BUY signal**: Opens or adds to LONG position
- **SELL signal**: Closes LONG position with `reduceOnly`

SHORT positions will be added in v2 after validating the LONG-only pipeline.

### Liquidation Protection

The system enforces multiple safety layers:

1. **20x Hard Cap**: Code-enforced maximum leverage
2. **Liquidation Buffer**: Blocks orders within 5% of liquidation price
3. **Isolated Margin**: Limits loss to position margin (not entire account)
4. **Real-time Monitoring**: Mark price stream tracks liquidation proximity
5. **Automatic Alerts**: Telegram notifications for liquidation risks

### Paper Trading (Testnet)

Test futures trading on demo.binance.com:

```yaml
futures:
  enabled: true
  test_mode: true  # Uses demo.binance.com (same keys as spot)
```

**Note**: On demo.binance.com, the same API keys work for both spot and futures.

### Going Live (Production)

**⚠️ EXTREME CAUTION REQUIRED ⚠️**

Before enabling live futures trading:

1. **Validate on testnet for 1-2 weeks minimum**
2. **Start with minimum leverage (2-3x)**
3. **Use small position sizes (reduce order_size_usdt)**
4. **Set conservative liquidation_buffer_pct (10%+)**
5. **Monitor 24/7 for first week**
6. **Have stop-loss / exit plan ready**

```yaml
futures:
  enabled: true
  test_mode: false  # ⚠️ LIVE TRADING - REAL MONEY
  default_leverage: 2  # Start conservative
  liquidation_buffer_pct: 10.0  # Extra safety buffer
```

### Funding Rates

Perpetual futures have funding payments every 8 hours:

- **Positive rate**: Longs pay shorts (bearish sentiment)
- **Negative rate**: Shorts pay longs (bullish sentiment)

The system tracks funding costs in position PnL. High funding costs can erode profits over time.

### Troubleshooting

**"Leverage exceeds hard cap"**
- Reduce `max_leverage` in config (max 20x hard coded)

**"Within X% of liquidation"**
- Position is too close to liquidation - add margin or reduce size

**"Insufficient margin"**
- Need more USDT in futures wallet or reduce leverage

**No futures orders executing**
- Check `futures.enabled: true` in settings.yaml
- Verify API keys have futures permissions
- Ensure `trading_mode: "futures"` in signals

### Differences from Spot Trading

| Feature | Spot | Futures |
|---------|------|---------|
| Leverage | 1x only | 1-20x |
| Liquidation | None | Yes, if margin < maintenance |
| Funding fees | None | Every 8 hours |
| Margin type | Full payment | Isolated (MVP) |
| Short selling | No | LONG-only for MVP |

---

## Production Deployment

### Reusable pre-flight requirements

Before going live with a new deployment or promotion, verify these items against
the current target environment. Use a dated release or deployment note to record
a specific run.

- All tests pass (`pytest`).
- Paper trading has been tested thoroughly.
- Test mode has been validated with small amounts.
- Risk limits have been reviewed and adjusted.
- Grafana dashboards are configured.
- Telegram alerts are tested.
- Binance API keys have IP restrictions.
- Server resources are adequate: CPU, RAM, and disk.
- Monitoring and alerting are set up.

### Step-by-Step Production Deployment

1. **Set up production server:**
   ```bash
   # Ubuntu/Debian
   sudo apt-get update
   sudo apt-get install -y docker.io docker-compose
   ```

2. **Clone repository:**
   ```bash
   git clone <repository-url>
   cd crypto-agent
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with production values:
   # - Strong POSTGRES_PASSWORD
   # - Real BINANCE_API_KEY and BINANCE_API_SECRET
   # - Telegram credentials for alerts
   ```

4. **Configure trading:**
   ```bash
   # Edit config/settings.yaml
   # - Set trading_execution.enabled: true
   # - Set trading_execution.test_mode: false
   # - Adjust order_size_usdt (start small: 20.0)
   ```

5. **Review risk settings:**
   ```bash
   # Edit config/risk.yaml
   # - Verify position limits
   # - Set appropriate loss thresholds
   # - Enable kill switch
   ```

6. **Start production stack:**
   ```bash
   docker-compose -f docker-compose.yml up -d
   ```

7. **Verify health:**
   ```bash
   # Check container status
   docker-compose ps

   # Check logs
   docker-compose logs -f agent

   # Verify metrics endpoint
   curl http://localhost:8000/metrics
   ```

8. **Monitor closely for first 24 hours:**
   - Watch Grafana dashboards
   - Check Telegram alerts
   - Monitor position sizes and PnL
   - Verify orders are executing correctly

### Scaling Gradually

After verifying stability:

1. **Week 1:** $20-50 per trade
2. **Week 2:** $50-100 per trade
3. **Week 3+:** Increase based on performance

Never increase position sizes beyond your risk tolerance.

---

## Monitoring

### Key Metrics

Monitor these metrics in Grafana:

- `ingest_messages_total{symbol,stream}` - OHLCV ingestion count
- `ingest_insert_latency_seconds` - Database write latency
- `indicator_compute_duration_seconds` - Indicator calculation time
- `execution_orders_placed_total{symbol,status}` - Orders placed
- `execution_risk_blocks_total{symbol,reason}` - Orders blocked by risk manager
- `strategy_signals_total{symbol,strategy,signal}` - Trading signals generated

### Alerting

Set up Prometheus Alertmanager or Grafana alerts for:

- High error rates
- Circuit breaker triggers
- Kill switch activation
- Database connection failures
- API latency spikes
- Unusual trading volume

### Telegram Notifications

The bot sends alerts for:

- Trade executions (BUY/SELL)
- Circuit breaker triggers
- Kill switch activation
- Daily PnL summaries
- System errors

---

## Troubleshooting

### Common Issues

#### Tests failing

```bash
# Ensure virtual environment is activated
source .venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt

# Run tests with verbose output
pytest -v --tb=short
```

#### Database connection errors

```bash
# Check TimescaleDB is running
docker-compose ps timescaledb

# Check logs
docker-compose logs timescaledb

# Verify credentials in .env
grep POSTGRES .env
```

#### No signals generated

- Verify indicators are being computed (check Grafana)
- Check strategy configuration in settings.yaml
- Ensure sufficient historical data (200+ candles)
- Review strategy logs for errors

#### Orders not executing

- Verify `trading_execution.enabled: true`
- Check risk manager isn't blocking trades
- Ensure sufficient balance in Binance account
- Review executor logs for errors

### Debug Logging

Enable debug logging in `config/settings.yaml`:

```yaml
log_level: DEBUG
```

View logs:

```bash
# All logs
docker-compose logs -f agent

# Last 100 lines
docker-compose logs --tail=100 agent

# Filter for errors
docker-compose logs -f agent | grep ERROR
```

### Getting Help

1. Check this documentation first
2. Review logs for error messages
3. Verify configuration files
4. Run smoke tests: `pytest scripts/smoke_test.py -v`
5. Check system resources: `docker stats`

---

## Development Workflow

### Adding a New Strategy

1. Create strategy file in `src/strategy/strategies/`:
   ```python
   from src.strategy.base import BaseStrategy
   from src.strategy.signals import Signal, SignalType

   class MyStrategy(BaseStrategy):
       async def evaluate(self, symbol, indicators):
           # Your logic here
           return Signal(
               type=SignalType.BUY,
               symbol=symbol,
               price=indicators["close_price"],
               confidence=0.8,
               reason="My strategy triggered",
               indicators=indicators,
           )
   ```

2. Register in `src/main.py`:
   ```python
   from src.strategy import MyStrategy

   strategy_registry = {
       # ... existing strategies
       "my_strategy": MyStrategy,
   }
   ```

3. Add to `config/settings.yaml`:
   ```yaml
   strategies:
     - name: my_strategy
       config:
         param1: value1
   ```

4. Write tests in `tests/test_my_strategy.py`

5. Run tests: `pytest tests/test_my_strategy.py -v`

### Running in Development Mode

```bash
# Start only database
docker-compose up -d timescaledb

# Run agent locally with hot reload
source .venv/bin/activate
python -m src.main
```

### Code Quality

Before committing:

```bash
# Run all tests
pytest

# Check types (if using basedpyright)
basedpyright

# Review changes
git diff
```

---

## Security Best Practices

- **Never commit secrets** - Use `.env` file (already in `.gitignore`)
- **Use IP restrictions** on Binance API keys
- **Enable 2FA** on all accounts (Binance, Telegram, server)
- **Regular backups** of database and configuration
- **Monitor for unauthorized access** via logs
- **Test kill switch** regularly
- **Start small** - Never risk more than you can afford to lose

---

## Resources

- **Architecture**: See `CLAUDE.md` for agent coordination
- **API Docs**: See `docs/TRADING_EXECUTION.md`
- **Indicators**: See `docs/INDICATORS.md`
- **Deployment**: See [DEPLOYMENT.md](DEPLOYMENT.md)
- **Code Review**: See [CODE_REVIEW.md](CODE_REVIEW.md)

---

## Support

For issues or questions:

1. Review logs and metrics
2. Check configuration files
3. Run diagnostic scripts in `scripts/`
4. Verify all tests pass

Remember: This is trading software that handles real money. Always test thoroughly, start small, and never disable safety features without understanding the risks.
