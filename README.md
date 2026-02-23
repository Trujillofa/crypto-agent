# Crypto Trading AI/ML Agent

This project implements a fully automated Binance Spot trading agent with strategy engine, risk management, and execution capabilities. It provides reliable market data ingestion, technical indicator computation, and intelligent signal-based trading.

## Features

### Core Components
- **Market Data Ingestion**: Binance Spot API REST polling for OHLCV data
- **Data Storage**: TimescaleDB hypertables for efficient time-series storage
- **Technical Indicators**: EMA, RSI, MACD, Bollinger Bands, ATR computation
- **Strategy Engine**: Signal generation from indicator-based strategies
- **Trading Execution**: Automated order placement with Binance Spot API
- **Risk Management**: Pre-configured position limits, loss limits, circuit breakers
- **Monitoring**: Prometheus metrics, Grafana dashboards, structured logging

### Trading Capabilities
- **Spot Trading Only**: BUY/SELL market orders (no futures, margin, or leverage)
- **Paper Trading Mode**: Test strategies without real capital
- **Multiple Pairs**: Simultaneous trading across 10+ cryptocurrency pairs
- **Strategy Framework**: Extensible strategy architecture (currently: Simple MA Crossover)
- **Signal Filtering**: Only actionable signals (BUY/SELL) trigger orders

## Core Principles

The development and operation of this agent follow a strict three-step cycle to ensure safety and performance:

1.  **Research**: Analyze market data, identify potential strategies, and understand the underlying logic before writing code.
2.  **Backtest**: Rigorously test strategies against historical data using the `scripts/run_backtest.py` tool. verify performance metrics (win rate, drawdown, PnL) before enabling them.
3.  **Implement**: Only after a strategy has proven itself in backtesting is it deployed to the live (or paper) environment via `config/settings.yaml`.

## Architecture

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
Prometheus Metrics + Logs
```

## Quick Start

### Prerequisites
- Docker and Docker Compose
- Binance account with Spot API keys (for live trading)
- Linux/macOS (recommended) or WSL2 on Windows

### Setup
```bash
# Clone repository (if not already cloned)
cd /home/yderf/TRADING/crypto-agent

# Local environment setup
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Create environment file
cp .env.example .env
## Update .env with real secrets before running
# Set Binance API credentials (optional for paper trading)
export BINANCE_API_KEY="your_api_key_here"
export BINANCE_API_SECRET="your_api_secret_here"

# Start the stack
docker-compose up --build
```

### Access Services
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (admin/admin)
- **Metrics Endpoint**: http://localhost:8000/metrics

## Configuration

### Trading Pairs
Edit `config/settings.yaml`:
```yaml
trading:
  pairs:
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT
    # Add more pairs as needed
  timeframe: 1m  # 1m, 5m, 15m, 1h, etc.
```

### Trading Execution
```yaml
trading_execution:
  enabled: false  # Set to true to enable real trading
  test_mode: true  # Set to false for live trading (CAUTION!)
  order_size_usdt: 100.0  # Default order size
```

### Strategy Settings
```yaml
strategy:
  evaluation_interval_seconds: 60  # How often to evaluate strategies
```

### Risk Management
Edit `config/risk.yaml` to adjust position limits, loss limits, and circuit breakers. See `docs/TRADING_EXECUTION.md` for details.

## Trading Modes

### 1. Paper Trading (Default - Safe)
```yaml
# config/settings.yaml
trading_execution:
  enabled: false  # No orders placed
  test_mode: true
```
- **Effect**: All components run, but no real orders are placed
- **Use Case**: Development, testing, strategy validation

### 2. Test Mode (API calls, but test endpoint)
```yaml
trading_execution:
  enabled: true
  test_mode: true  # Uses Binance testnet
```
- **Effect**: Real API calls to Binance Spot testnet
- **Use Case**: End-to-end testing with fake funds

### 3. Live Trading (REAL MONEY)
```yaml
trading_execution:
  enabled: true
  test_mode: false  # CAUTION: Real trades!
```
- **Effect**: Real orders on Binance Spot with real funds
- **Use Case**: Production trading (start with small order sizes!)

## Monitoring & Metrics

### Key Metrics
- `ingest_messages_total{symbol,stream}` - OHLCV ingestion count
- `ingest_insert_latency_seconds` - Database write latency
- `indicator_compute_duration_seconds` - Indicator calculation time
- `execution_orders_placed_total{symbol,status}` - Orders placed
- `execution_risk_blocks_total{symbol,reason}` - Orders blocked by risk manager
- `strategy_signals_total{symbol,strategy,signal}` - Trading signals generated

### View Metrics
```bash
# Via Prometheus UI
open http://localhost:9090

# Via curl
curl http://localhost:8000/metrics | grep execution

# View logs
docker-compose logs -f agent
```

## Strategy Development

The system supports custom trading strategies. Current strategies:
- **SimpleMACrossoverStrategy**: EMA crossover signals (12/26 periods)

### Add Custom Strategy
1. Create new strategy in `src/strategy/strategies/`
2. Inherit from `TradingStrategy` base class
3. Implement `evaluate()` method returning `Signal`
4. Register in `src/main.py`:
```python
from src.strategy import YourCustomStrategy

engine_config = EngineConfig(
    strategy_classes=[YourCustomStrategy],
    strategy_configs=[{"param1": value1, "param2": value2}],
    # ...
)
```

See `src/strategy/strategies/simple_ma.py` for reference implementation.

## Binance Spot API Details

### Order Types Supported
- **Market Orders**: Immediate execution at current market price
  - BUY: Uses `quoteOrderQty` parameter (spend X USDT to buy asset)
  - SELL: Uses `quantity` parameter (requires checking asset balance first)

### Key Differences vs Futures
- ✅ **Spot**: Buy/sell actual crypto assets (no leverage)
- ❌ **Futures**: Not supported (no positions, margin, or leverage)
- ✅ **SELL orders**: Require holding the asset (no short-selling)
- ✅ **BUY orders**: Require USDT balance

### API Endpoints Used
- `/api/v3/account` - Get account information
- `/api/v3/order` - Place/cancel orders
- `/api/v3/openOrders` - Get open orders
- `/api/v3/klines` - Get OHLCV data

For detailed API documentation, see `docs/TRADING_EXECUTION.md`.

## Testing

### Run All Tests
```bash
# Inside docker container
docker-compose exec agent pytest -v

# Or locally (requires Python 3.11+)
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && pytest
```

### Test Coverage
- Settings loading and validation
- Indicator computation
- Signal generation
- Risk management rules
- Order placement (mocked)

### Integration Test
The `test_settings_integration.py` verifies:
- Safe defaults (enabled=false, test_mode=true)
- Strategy configuration loading
- Complete data flow from indicators → signals → executor

## Production Deployment

### Pre-Flight Checklist
- [ ] Test thoroughly in paper mode
- [ ] Test with small order sizes in test mode
- [ ] Review and adjust risk limits in `config/risk.yaml`
- [ ] Set up Grafana dashboards for monitoring
- [ ] Enable IP restrictions on Binance API keys
- [ ] Set up alerting for critical metrics

### Going Live
1. **Start conservatively**:
   ```yaml
   trading_execution:
     enabled: true
     test_mode: false
     order_size_usdt: 20.0  # Start SMALL!
   ```

2. **Monitor closely**: Watch metrics and logs for first 24 hours

3. **Scale gradually**: Increase order size only after verifying stability

4. **Set up alerts**: Use Prometheus Alertmanager or Grafana alerts

## Troubleshooting

### "Trading blocked: Kill switch enabled"
- Check `config/risk.yaml` kill_switch settings
- Review recent trades for losses exceeding limits
- Manually disable kill switch if appropriate

### "BINANCE_API_KEY not set"
```bash
export BINANCE_API_KEY="your_key"
export BINANCE_API_SECRET="your_secret"
```

### "No trading pairs configured"
- Ensure `config/settings.yaml` has `trading.pairs` list

### High Database Latency
- Check TimescaleDB container health: `docker-compose ps`
- Review hypertable compression settings
- Consider upgrading TimescaleDB instance size

## Documentation

- **[TRADING_EXECUTION.md](docs/TRADING_EXECUTION.md)**: Detailed execution system documentation
- **Risk Management**: See `config/risk.yaml` comments
- **Strategy Framework**: See `src/strategy/README.md` (if exists)

## Security Best Practices

1. **Never commit API keys**: Use environment variables only
2. **Enable IP restrictions**: Whitelist your server IP on Binance
3. **Start with test mode**: Verify everything works before live trading
4. **Use separate API keys**: Different keys for test/production
5. **Monitor API key usage**: Check Binance dashboard regularly
6. **Rotate keys periodically**: Change API keys every 90 days

## Roadmap

Future enhancements:
- [ ] Multi-strategy portfolio allocation
- [ ] ML-based signal generation (LSTM, RL agents)
- [ ] Backtesting framework
- [ ] Advanced order types (limit orders, stop-loss)
- [ ] Telegram/Discord notifications
- [ ] Performance analytics dashboard

## Support & Contributing

For issues or questions:
1. Check logs: `docker-compose logs agent`
2. Review metrics: http://localhost:8000/metrics
3. Consult documentation in `docs/`

## License

MIT - Use at your own risk. Cryptocurrency trading is highly risky. Only trade with funds you can afford to lose.

---

**⚠️ DISCLAIMER**: This software is provided for educational purposes. Trading cryptocurrencies carries significant financial risk. The authors are not responsible for any financial losses incurred while using this software. Always do your own research and consult with financial advisors before trading.
```

Prometheus: http://localhost:9090
Grafana: http://localhost:3000

## Metrics

- `ingest_messages_total{symbol,stream}`
- `ingest_insert_latency_seconds`
- `ingest_last_open_time{symbol}`

## Training Utilities

```bash
python scripts/train.py --input data/ohlcv.csv --output data/indicators.csv
```
