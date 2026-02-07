# Trading Execution

This document describes the trading execution system that integrates Binance Spot API with risk management.

## Overview

The trading execution system:
1. **Authenticates** with Binance Spot API using API keys
2. **Places** orders (market/limit) with risk manager validation
3. **Cancels** orders (single or bulk)
4. **Monitors** account and order status
5. **Records** all execution metrics to Prometheus

## Architecture

```
Trading Strategy Signal
    ↓
Risk Manager (validation)
    ↓
TradingExecutor
    ↓
BinancePrivateClient (API calls)
    ↓
Order Placement/Account Monitoring
    ↓
Prometheus Metrics + Notifications
```

## Components

### BinancePrivateClient

**Location**: `src/execution/binance_client.py`

Async Binance Spot API client with methods:

#### Account Management
- `get_account_info()` - Get wallet balance info
- `get_asset_balance(asset)` - Get specific asset balance
- `get_open_orders(symbol)` - Get open orders

#### Order Execution
- `place_market_order(symbol, side, quantity)` - Place market order
  - **BUY**: Uses `quoteOrderQty` internally (spend X USDT to acquire base asset)
  - **SELL**: Uses `quantity` parameter (sell Y amount of base asset)
- `place_limit_order(symbol, side, price, quantity)` - Place limit order
- `cancel_order(symbol, order_id)` - Cancel single order
- `cancel_all_orders(symbol)` - Cancel all orders for a symbol

#### Order Query
- `get_order_status(symbol, order_id)` - Query order status

**Important Spot API Behavior**:
- BUY orders require sufficient USDT balance (order uses quoteOrderQty)
- SELL orders require holding the base asset (no short-selling on Spot)
- Executor checks asset balance before SELL signals

**Test Mode**: Set `test_mode=True` to skip actual API calls (for development/testing).

### TradingExecutor

**Location**: `src/execution/executor.py`

Main trading service that:
1. Checks risk manager before placing orders
2. Enforces position limits (risk management for Spot holdings)
3. Records execution metrics
4. Monitors account balance and open orders periodically

#### Key Methods
- `run()` - Main trading loop (monitors account balance every 30s)
- `on_signal(signal)` - Process trading signals from StrategyEngine
- `place_market_order(symbol, side, quantity)` - Place market order with risk checks
- `place_limit_order(symbol, side, price, quantity)` - Place limit order with risk checks
- `cancel_order(symbol, order_id)` - Cancel an order
- `cancel_all_orders(symbol)` - Cancel all orders for a symbol

**Signal Handler**:
- BUY signal: Places market order using `order_size_usdt` (quoteOrderQty)
- SELL signal: Checks asset balance, sells if held (base asset quantity)
- HOLD signal: Ignored (no action taken)

### RiskManager Integration

**Location**: `src/risk/manager.py`

Pre-configured risk controls enforced before every order:

- **Position Limits**:
  - Max position % of portfolio (default: 10%)
  - Max open orders (default: 10)

- **Loss Limits**:
  - Max daily loss % (default: 5%)
  - Max drawdown % (default: 15%)
  - Max single trade loss % (default: 2%)

- **Circuit Breakers**:
  - Consecutive losses (default: 5)
  - API errors (default: 3)
  - Latency spike ms (default: 5000)

- **Kill Switch**:
  - Enabled by default
  - Telegram notification on trigger

If risk check fails, order is blocked and metric is recorded.

## Configuration

### config/settings.yaml

```yaml
trading_execution:
  api_key: ""  # Use BINANCE_API_KEY environment variable
  api_secret: ""  # Use BINANCE_API_SECRET environment variable
  test_mode: true  # Set to false for live trading
  order_size_usdt: 100.0  # Default order size in USDT
```

### Environment Variables

- `BINANCE_API_KEY` - Binance API key
- `BINANCE_API_SECRET` - Binance API secret

**Security Notes**:
- API keys are loaded from environment variables for security
- Never commit API keys to git
- Use test mode until ready for live trading
- Enable IP restrictions on Binance API keys

## Prometheus Metrics

The execution system exports the following metrics:

### Counters
- `execution_orders_placed_total{symbol, order_type, status}` - Total orders placed
- `execution_orders_cancelled_total{symbol, reason}` - Orders cancelled
- `execution_orders_filled_total{symbol, side}` - Orders filled
- `execution_orders_rejected_total{symbol, reason}` - Orders rejected
- `execution_api_errors_total{endpoint, error_code}` - API errors
- `execution_risk_blocks_total{symbol, reason}` - Orders blocked by risk manager

### Gauges
- `execution_open_orders_count{symbol}` - Current open orders per symbol
- `execution_account_balance{type}` - Account balance (type: total_wallet, available)
- `execution_trading_active` - Whether trading is active (1/0)

### Histograms
- `execution_order_latency_seconds{symbol, order_type}` - Order placement latency

### Summary
- `execution_realized_pnl_total{symbol}` - Realized PnL summary

Access metrics: `http://localhost:8000/metrics`

## Usage

### Manual Order Placement

```python
from src.execution import TradingExecutor, TradingConfig, ExecutionMetrics
from src.risk.manager import RiskManager

async def main():
    # Configuration
    config = TradingConfig(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET"),
        test_mode=True,  # IMPORTANT: True for testing
        symbols=["BTCUSDT", "ETHUSDT"],
        order_size_usdt=100.0,
    )

    # Initialize
    risk_manager = RiskManager()
    metrics = ExecutionMetrics()

    async with TradingExecutor(config, risk_manager, metrics) as executor:
        # Place a market order
        order = await executor.place_market_order(
            symbol="BTCUSDT",
            side="BUY",
        )
        print(f"Order placed: {order.order_id}")
```

### Order Cancellation

```python
# Cancel single order
await executor.cancel_order("BTCUSDT", order_id=123456789)

# Cancel all orders for a symbol
count = await executor.cancel_all_orders("BTCUSDT")
print(f"Cancelled {count} orders")
```

### Risk Manager Testing

```python
from src.risk.manager import RiskManager

risk_manager = RiskManager()

# Check if trading is allowed
is_allowed, reason = risk_manager.is_trading_allowed()
print(f"Trading allowed: {is_allowed}, Reason: {reason}")

# Check position limits
allowed, reason = risk_manager.check_position_limit(
    symbol="BTCUSDT",
    size=100.0,
    portfolio_value=10000.0,
)
print(f"Position allowed: {allowed}, Reason: {reason}")
```

## Testing

### Test Binance Private API

Run the private API test script:

```bash
# Set environment variables
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"

# Run tests
python scripts/test_binance_private.py
```

This tests (Binance Spot API endpoints):
1. **Account info**: `GET /api/v3/account` - Get wallet balances
2. **Asset balance**: `GET /api/v3/account` - Query specific asset balance
3. **Open orders**: `GET /api/v3/openOrders` - List open orders
4. **Order placement**: `POST /api/v3/order` - Place market/limit orders
5. **Order cancellation**: `DELETE /api/v3/order` - Cancel orders
6. **API key permissions**: Validates Spot trading permissions

**Note**: Spot API only. No Futures, margin, or leverage endpoints.

### Test Trading Execution

1. **Set test mode** in `config/settings.yaml`:
   ```yaml
   trading_execution:
     test_mode: true  # IMPORTANT!
   ```

2. **Set API keys** (optional for test mode):
   ```bash
   export BINANCE_API_KEY="your_key"
   export BINANCE_API_SECRET="your_secret"
   ```

3. **Run the agent**:
   ```bash
   cd /home/yderf/TRADING/crypto-agent
   docker-compose up --build
   ```

4. **Verify metrics**:
   ```bash
   curl http://localhost:8000/metrics | grep execution
   ```

## Production Deployment

### Before Going Live

1. **Test thoroughly in test mode**:
   - Ensure all risk controls work
   - Verify order placement/cancellation
   - Check metrics recording

2. **Review risk limits**:
   - Adjust position limits to match your risk tolerance
   - Set appropriate loss limits
   - Configure circuit breakers

3. **Enable IP restrictions** on Binance API keys

4. **Set up monitoring**:
   - Grafana dashboards for execution metrics
   - Telegram notifications for critical events

5. **Start with small size**:
   - Reduce `order_size_usdt` initially
   - Monitor results closely
   - Gradually increase size as confidence grows

### Going Live

1. **Update config/settings.yaml**:
   ```yaml
   trading_execution:
     test_mode: false  # LIVE TRADING!
     order_size_usdt: 50.0  # Start smaller
   ```

2. **Restart agent**:
   ```bash
   docker-compose restart agent
   ```

3. **Monitor closely** for first few hours

## Troubleshooting

### API Key Not Found

```
RuntimeError: BINANCE_API_KEY and BINANCE_API_SECRET must be set
```

**Solution**:
```bash
export BINANCE_API_KEY="your_key"
export BINANCE_API_SECRET="your_secret"
```

### Risk Blocks Orders

```
RuntimeError: Position limit check failed: Max open positions (5) reached
```

**Solution**:
1. Close existing positions
2. Adjust risk limits in `config/risk.yaml`
3. Wait for circuit breaker reset

### Order Placement Fails

```
RuntimeError: Binance API error [-1021]: Timestamp for this request is outside of the recvWindow
```

**Solution**:
- Check system time is synced (NTP)
- Increase recvWindow in client if needed

### High Latency

**Symptoms**: Order latency > 1 second

**Solutions**:
1. Check network connectivity to Binance
2. Reduce distance to exchange server
3. Use VPS closer to Binance servers
4. Review circuit breaker latency threshold

## Best Practices

### Risk Management
1. **Always use test mode first** - Verify orders work as expected
2. **Monitor position limits** - Don't overexpose account
3. **Set appropriate stop losses** - Use ATR or percentage-based stops
4. **Review risk settings regularly** - Adjust as conditions change

### Order Management
1. **Use limit orders for better fills** - Market orders can slip significantly
2. **Set reasonable order size** - Don't use large % of portfolio
3. **Monitor open orders** - Cancel stale orders regularly
4. **Track order latency** - High latency can cause missed opportunities

### API Usage
1. **Respect rate limits** - Binance has strict limits
2. **Handle errors gracefully** - Retry transient errors
3. **Use async patterns** - Don't block on API calls
4. **Log everything** - Essential for debugging and audit

## Integration Points

### For Strategy Integration

The `TradingExecutor` can be extended to accept strategy signals:

```python
class TradingExecutor:
    async def place_strategy_order(
        self,
        signal: dict,  # {symbol, side, type, price, quantity}
    ) -> OrderInfo:
        """Place an order from a trading strategy."""
        # Validate signal format
        # Check risk limits
        # Place order
        # Record metrics
```

### For Backtesting Integration

Use `BinancePrivateClient` in backtesting mode:

```python
client = BinancePrivateClient(
    api_key="dummy",
    api_secret="dummy",
    test_mode=True,  # No actual API calls
)

# Simulate orders
order = await client.place_market_order("BTCUSDT", "BUY", 1.0)
```

## Security Considerations

1. **Never hardcode API keys** - Always use environment variables
2. **Enable IP restrictions** - Whitelist your server IP on Binance
3. **Use test mode initially** - Verify everything works before live trading
4. **Monitor for unauthorized access** - Check logs for unexpected activity
5. **Rotate API keys regularly** - Change keys periodically
6. **Use separate API keys** - Different keys for test/production

## References

- [Binance Spot API Docs](https://binance-docs.github.io/apidocs/spot/en/)
- [Binance Trading Rules](https://www.binance.com/en/trade-rule)
- [Risk Management Best Practices](https://www.investopedia.com/terms/r/risk-management.asp)

## Support

For issues or questions:
1. Check logs: `docker-compose logs agent`
2. Review metrics: `http://localhost:8000/metrics`
3. Test API: `python scripts/test_binance_private.py`
4. Consult Binance API documentation
