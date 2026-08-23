# Quick Reference — Crypto Trading Agent

> Single-page cheat sheet for rapid development. See `CLAUDE.md` for full rules.

---

## 🚀 Common Operations

### Run Tests
```bash
pytest                          # All tests
pytest tests/test_foo.py -v    # Single file
pytest -k "test_name"          # By pattern
```

### Run Agent
```bash
docker-compose up --build       # Full stack
python -m src.main              # Local (requires .env)
```

### Deploy to Production
```bash
ssh crypto-agent "cd /opt/crypto-agent && git pull"
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml build <service>"
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml up -d <service>"
```

### View Logs
```bash
docker-compose logs -f agent --tail=100
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml logs <service> --tail=100 --no-log-prefix"
```

### Check Status
```bash
docker-compose ps
curl http://localhost:8000/metrics  # Local metrics
```

---

## 📁 File Locations

| Need | Path |
|------|------|
| Entry point | `src/main.py` |
| Config | `config/settings.yaml`, `config/risk.yaml` |
| Environment | `.env` |
| Tests | `tests/test_*.py` |
| Strategies | `src/strategy/*.py` |
| Indicators | `src/features/technical.py` |
| Execution | `src/execution/executor.py` |
| Risk Manager | `src/risk/manager.py` |
| Telegram | `src/notifications/telegram.py` |
| Backtest | `scripts/run_backtest.py` |
| Math models roadmap | `docs/MATH_MODELS_ROADMAP.md` |

---

## 🧪 Testing Patterns

### Basic Test Structure
```python
import pytest
from src.module import ClassToTest

@pytest.mark.asyncio
async def test_feature_xyz():
    # Arrange
    subject = ClassToTest(param="value")

    # Act
    result = await subject.method()

    # Assert
    assert result == expected
```

### Mocking Binance API
```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_order_placement():
    with patch("src.execution.binance_client.BinanceClient") as mock:
        mock_instance = AsyncMock()
        mock_instance.place_order = AsyncMock(return_value={"orderId": 123})
        mock.return_value = mock_instance
        # test logic
```

### Fixtures (from `tests/conftest.py`)
```python
@pytest.fixture
def mock_settings():
    return Settings(...)

@pytest.fixture
async def clean_db():
    # Setup
    yield db
    # Cleanup
```

---

## ⚙️ Configuration Quick Ref

### Enable Live Trading
```yaml
# config/settings.yaml
trading_execution:
  enabled: true
  test_mode: false  # ⚠️ REAL MONEY
  order_size_usdt: 20.0  # Start small!
```

### Add Trading Pair
```yaml
trading:
  pairs:
    - BTCUSDT
    - ETHUSDT
    - NEWPAIR  # Add here
```

### Add Strategy
```yaml
strategy:
  strategies:
    - name: simple_ma
      config:
        ema_short_period: 12
        ema_long_period: 26
    - name: my_strategy  # Add here
      config:
        param1: value1
```

### Risk Limits
```yaml
# config/risk.yaml
position_limits:
  max_position_pct: 0.10    # 10% max per position
  max_open_positions: 4

loss_limits:
  max_daily_loss_pct: 0.05  # 5% daily loss stop
  max_drawdown_pct: 0.15    # 15% drawdown kill switch
```

---

## 🔧 Add New Strategy

1. Create `src/strategy/strategies/my_strategy.py`:
```python
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType

class MyStrategy(BaseStrategy):
    name = "my_strategy"

    async def evaluate(self, symbol: str, indicators: dict) -> Signal | None:
        # Your logic
        if indicators["rsi"] < 30:
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=indicators["close"],
                confidence=0.8,
                reason="RSI oversold",
                indicators=indicators,
            )
        return None
```

2. Register in `src/strategy/__init__.py`:
```python
from src.strategy.strategies.my_strategy import MyStrategy

__all__ = [..., "MyStrategy"]
```

3. Add to `config/settings.yaml`:
```yaml
strategy:
  strategies:
    - name: my_strategy
      config:
        param: value
```

4. Add tests in `tests/test_my_strategy.py`

---

## 🐛 Debug Common Issues

| Symptom | Fix |
|---------|-----|
| `ImportError` | Check `__init__.py` exports |
| Tests failing | Run/test_foo `pytest tests.py -v --tb=short` |
| No signals | Check indicators computed, strategy registered |
| Orders not executing | Check `trading_execution.enabled: true` |
| Risk blocked | Check risk limits in `config/risk.yaml` |
| DB errors | Check TimescaleDB running: `docker-compose ps` |
| API errors | Check `.env` has valid `BINANCE_API_KEY` |

### Enable Debug Logging
```yaml
# config/settings.yaml
log_level: DEBUG
```

---

## 📊 Key Metrics

| Metric | Description |
|--------|-------------|
| `ingest_messages_total` | OHLCV data ingested |
| `indicator_compute_duration_seconds` | Indicator calculation time |
| `execution_orders_placed_total` | Orders placed |
| `execution_risk_blocks_total` | Orders blocked by risk |
| `strategy_signals_total` | Signals generated |

---

## 🔄 Trading Modes

| Mode | `enabled` | `test_mode` | Effect |
|------|-----------|-------------|--------|
| Paper | `false` | `true` | No orders placed (default) |
| Test | `true` | `true` | Binance testnet (fake funds) |
| Live | `true` | `false` | Real money ⚠️ |

---

## 📋 Commit Checklist

```bash
pytest                    # All tests pass
git diff --stat           # Review changes
git add <files>           # Stage specific files
git commit -m "feat(strategy): add new strategy"
```

---

## 📞 Useful Commands

```bash
# Database
docker-compose exec timescaledb psql -U trading -d marketdata

# Check Prometheus
curl http://localhost:9090/api/v1/query?query=ingest_messages_total

# Reset risk state
python scripts/reset_risk_state.py

# Run smoke test
pytest scripts/smoke_test.py -v

# Backtest
python scripts/run_backtest.py --symbol BTCUSDT --timeframe 5m
```
