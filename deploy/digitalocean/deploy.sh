#!/bin/bash
set -euo pipefail

# DigitalOcean Deployment Script for Crypto Trading Agent

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Default values
DO_TOKEN="${DO_TOKEN:-}"
DO_REGION="${DO_REGION:-nyc1}"
DO_SIZE="${DO_SIZE:-s-2vcpu-2gb}"
DROPLET_NAME="crypto-trading-agent"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_rsa}"
TAG="crypto-agent"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Deploy crypto trading agent to DigitalOcean

OPTIONS:
    -t, --token TOKEN       DigitalOcean API token (or set DO_TOKEN env var)
    -r, --region REGION    Region (nyc1, sfo2, fra1, etc.) [default: nyc1]
    -s, --size SIZE        Droplet size [default: s-2vcpu-2gb]
    -n, --name NAME        Droplet name [default: crypto-trading-agent]
    -h, --help             Show this help message

EXAMPLES:
    $0 -t my-token -r fra1 -s s-2vcpu-4gb
    DO_TOKEN=my-token $0

EOF
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--token) DO_TOKEN="$2"; shift 2 ;;
        -r|--region) DO_REGION="$2"; shift 2 ;;
        -s|--size) DO_SIZE="$2"; shift 2 ;;
        -n|--name) DROPLET_NAME="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

# Validate required parameters
if [[ -z "$DO_TOKEN" ]]; then
    log_error "DigitalOcean token is required. Set DO_TOKEN or use -t flag."
    exit 1
fi

log_info "Starting DigitalOcean deployment..."

# Authenticate with doctl
log_info "Authenticating with DigitalOcean..."
export DO_TOKEN

# Check if droplet exists
EXISTING_DROPLET=$(doctl compute droplet list --format Name,ID --no-header 2>/dev/null | grep "$DROPLET_NAME" || true)

if [[ -n "$EXISTING_DROPLET" ]]; then
    log_warn "Droplet '$DROPLET_NAME' already exists. Updating..."
    DROPLET_ID=$(echo "$EXISTING_DROPLET" | awk '{print $2}')
else
    # Create SSH key if it doesn't exist
    if [[ ! -f "$SSH_KEY_PATH" ]]; then
        log_info "Generating SSH key..."
        ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_PATH" -N "" -C "crypto-agent"
    fi

    # Get SSH key fingerprint
    SSH_KEY_FINGERPRINT=$(doctl compute ssh-key import "$DROPLET_NAME-key" --public-key-file "$SSH_KEY_PATH" --format Fingerprint 2>/dev/null || \
        doctl compute ssh-key list --format Fingerprint,Name --no-header | grep "$DROPLET_NAME-key" | awk '{print $1}' || echo "")

    # Create droplet
    log_info "Creating droplet in $DO_REGION with size $DO_SIZE..."

    DROPLET_ID=$(doctl compute droplet create "$DROPLET_NAME" \
        --region "$DO_REGION" \
        --size "$DO_SIZE" \
        --image docker-20-04 \
        --ssh-keys "$SSH_KEY_FINGERPRINT" \
        --tag-name "$TAG" \
        --format ID \
        --no-header \
        2>/dev/null)

    log_info "Droplet created with ID: $DROPLET_ID"

    # Wait for droplet to be ready
    log_info "Waiting for droplet to be ready..."
    sleep 30
fi

# Get droplet IP
DROPLET_IP=$(doctl compute droplet get "$DROPLET_ID" --format PublicIPv4 --no-header 2>/dev/null)

if [[ -z "$DROPLET_IP" ]]; then
    log_error "Failed to get droplet IP"
    exit 1
fi

log_info "Droplet IP: $DROPLET_IP"

# Wait for SSH to be available
log_info "Waiting for SSH to be available..."
for i in {1..30}; do
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@"$DROPLET_IP" "echo 'ready'" &>/dev/null; then
        log_info "SSH is available"
        break
    fi
    if [[ $i -eq 30 ]]; then
        log_error "SSH not available after 30 attempts"
        exit 1
    fi
    sleep 2
done

# Copy project files and deploy
log_info "Deploying crypto trading agent..."

ssh -o StrictHostKeyChecking=no root@"$DROPLET_IP" << 'ENDSSH'
set -e

# Install Docker if not present
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

# Start Docker if not running
systemctl start docker || service docker start || true
systemctl enable docker || true

# Install Docker Compose if not present
if ! command -v docker-compose &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq docker-compose > /dev/null 2>&1
fi

# Create project directory
mkdir -p /opt/crypto-trading-agent
cd /opt/crypto-trading-agent

# Create .env file
cat > .env << 'EOF'
# Binance API Keys (REQUIRED - Replace with your keys)
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# Database
POSTGRES_PASSWORD=change_me_in_production

# Telegram (optional)
# TELEGRAM_BOT_TOKEN=
# TELEGRAM_CHAT_ID=
# TELEGRAM_ENABLED=false

# Logging
LOG_LEVEL=INFO
EOF

# Copy docker-compose.prod.yml
cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  timescaledb:
    image: timescale/timescaledb:latest-pg14
    restart: always
    environment:
      POSTGRES_DB: marketdata
      POSTGRES_USER: trading
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - timescaledb-data:/var/lib/postgresql/data
    networks:
      - crypto-net

  prometheus:
    image: prom/prometheus:v2.47.0
    restart: always
    volumes:
      - ./config/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--storage.tsdb.retention.time=30d'
    networks:
      - crypto-net

  grafana:
    image: grafana/grafana:10.1.0
    restart: always
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_AUTH_ANONYMOUS_ENABLED=false
    volumes:
      - grafana-data:/var/lib/grafana
    depends_on:
      - timescaledb
      - prometheus
    networks:
      - crypto-net

  agent:
    build:
      context: .
      dockerfile: Dockerfile.prod
    restart: always
    depends_on:
      - timescaledb
      - prometheus
    environment:
      - POSTGRES_HOST=timescaledb
      - POSTGRES_PORT=5432
      - POSTGRES_DB=marketdata
      - POSTGRES_USER=trading
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - BINANCE_API_KEY=${BINANCE_API_KEY}
      - BINANCE_API_SECRET=${BINANCE_API_SECRET}
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
    networks:
      - crypto-net

volumes:
  timescaledb-data:
  prometheus-data:
  grafana-data:

networks:
  crypto-net:
    driver: bridge
EOF

# Copy Dockerfile.prod
cat > Dockerfile.prod << 'EOF'
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.main"]
EOF

# Copy config files
mkdir -p config
cp -r /opt/crypto-trading-agent/config/prometheus.yml config/ 2>/dev/null || true
cp -r /opt/crypto-trading-agent/config/grafana config/ 2>/dev/null || true
cp -r /opt/crypto-trading-agent/config/settings.yaml config/ 2>/dev/null || true
cp -r /opt/crypto-trading-agent/config/risk.yaml config/ 2>/dev/null || true

# Pull latest code (if git is available)
cd /opt/crypto-trading-agent

# Build and start containers
docker-compose build agent
docker-compose up -d

echo "Deployment complete!"
docker-compose ps
ENDSSH

log_info "Deployment complete!"
log_info "Droplet IP: $DROPLET_IP"
log_info ""
log_info "Services:"
log_info "  - Agent API: http://$DROPLET_IP:8000"
log_info "  - Prometheus: http://$DROPLET_IP:9090"
log_info "  - Grafana: http://$DROPLET_IP:3000 (admin/admin)"
log_info ""
log_info "Next steps:"
log_info "  1. Edit .env file on the server: ssh root@$DROPLET_IP 'nano /opt/crypto-trading-agent/.env'"
log_info "  2. Add your Binance API keys"
log_info "  3. Restart the agent: ssh root@$DROPLET_IP 'cd /opt/crypto-trading-agent && docker-compose restart agent'"
