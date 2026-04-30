#!/bin/bash
# AVAX Regime Check — runs weekly via cron
# Checks if AVAX has established a bullish regime change (price above 200-day MA)
# If yes, sends Telegram alert to re-enable the agent

BOT_TOKEN=$(grep TELEGRAM_BOT_TOKEN /opt/crypto-agent/.env | cut -d= -f2)
CHAT_ID=$(grep TELEGRAM_CHAT_ID /opt/crypto-agent/.env | cut -d= -f2)

# Get current AVAX price from Binance
CURRENT_PRICE=$(curl -s "https://api.binance.com/api/v3/ticker/price?symbol=AVAXUSDT" | python3 -c "import sys,json; print(json.load(sys.stdin)['price'])" 2>/dev/null)

if [ -z "$CURRENT_PRICE" ]; then
    echo "Failed to fetch AVAX price" >&2
    exit 1
fi

# Get 200 daily candles for EMA200 calculation
EMA200=$(curl -s "https://api.binance.com/api/v3/klines?symbol=AVAXUSDT&interval=1d&limit=200" | \
    python3 -c "
import sys, json
candles = json.load(sys.stdin)
if len(candles) < 200:
    print('INSUFFICIENT_DATA')
    sys.exit(0)
closes = [float(c[4]) for c in candles]
ema = sum(closes) / len(closes)
print(f'{ema:.4f}')
" 2>/dev/null)

if [ -z "$EMA200" ] || [ "$EMA200" = "INSUFFICIENT_DATA" ]; then
    echo "Failed to calculate EMA200" >&2
    exit 1
fi

PRICE_ABOVE_EMA=$(python3 -c "print('YES' if $CURRENT_PRICE > $EMA200 else 'NO')")

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) AVAX=$CURRENT_PRICE EMA200=$EMA200 Above=${PRICE_ABOVE_EMA}"

# Only alert if price crosses ABOVE EMA200 (regime change)
if [ "$PRICE_ABOVE_EMA" = "YES" ]; then
    MSG="🟢 *AVAX REGIME CHANGE DETECTED*

Current Price: \`$CURRENT_PRICE\`
200-day EMA: \`$EMA200\`
Price is NOW ABOVE the 200-day EMA.

This signals a potential bullish regime change.
Consider re-enabling the AVAX agent and re-running autoresearch.

Agent container: \`crypto-agent-agent_avax-1\` (currently stopped)
To re-enable:
\`\`\`
docker update --restart=unless-stopped crypto-agent-agent_avax-1
docker start crypto-agent-agent_avax-1
\`\`\`"

    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="$CHAT_ID" \
        -d text="$MSG" \
        -d parse_mode="Markdown" > /dev/null 2>&1
    
    echo "ALERT SENT: Regime change detected!"
fi
