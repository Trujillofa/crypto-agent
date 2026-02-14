#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

HCLOUD_TOKEN="${HCLOUD_TOKEN:-}"
HCLOUD_REGION="${HCLOUD_REGION:-fsn1}"
HCLOUD_SERVER_TYPE="${HCLOUD_SERVER_TYPE:-cx22}"
SERVER_NAME="crypto-trading-agent"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_rsa}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Deploy crypto trading agent to Hetzner Cloud

OPTIONS:
    -t, --token TOKEN       Hetzner API token (or set HCLOUD_TOKEN env var)
    -r, --region REGION     Region (fsn1, nbg1, ash) [default: fsn1]
    -s, --type TYPE         Server type (cx22, cx32, cx42) [default: cx22]
    -n, --name NAME         Server name [default: crypto-trading-agent]
    -h, --help              Show this help message

EXAMPLES:
    $0 -t my-token -r nbg1 -s cx32
    HCLOUD_TOKEN=my-token $0

EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--token) HCLOUD_TOKEN="$2"; shift 2 ;;
        -r|--region) HCLOUD_REGION="$2"; shift 2 ;;
        -s|--type) HCLOUD_SERVER_TYPE="$2"; shift 2 ;;
        -n|--name) SERVER_NAME="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$HCLOUD_TOKEN" ]]; then
    log_error "Hetzner token is required. Set HCLOUD_TOKEN or use -t flag."
    exit 1
fi

export HCLOUD_TOKEN
log_info "Starting Hetzner deployment..."

hcloud context create default &>/dev/null || true

EXISTING_SERVER=$(hcloud server list --format name,id --no-header 2>/dev/null | grep "$SERVER_NAME" || true)

if [[ -n "$EXISTING_SERVER" ]]; then
    log_warn "Server '$SERVER_NAME' already exists."
    SERVER_ID=$(echo "$EXISTING_SERVER" | awk '{print $2}')
else
    if [[ ! -f "$SSH_KEY_PATH" ]]; then
        log_info "Generating SSH key..."
        ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_PATH" -N "" -C "crypto-agent"
    fi

    SSH_KEY_NAME="${SERVER_NAME}-key"
    hcloud ssh-key create --name "$SSH_KEY_NAME" --public-key-file "${SSH_KEY_PATH}.pub" &>/dev/null || \
        hcloud ssh-key list --format name --no-header | grep -q "$SSH_KEY_NAME" || true

    log_info "Creating server in $HCLOUD_REGION with type $HCLOUD_SERVER_TYPE..."

    SERVER_ID=$(hcloud server create \
        --name "$SERVER_NAME" \
        --location "$HCLOUD_REGION" \
        --type "$HCLOUD_SERVER_TYPE" \
        --image ubuntu-22.04 \
        --ssh-key "$SSH_KEY_NAME" \
        --format id \
        --no-header \
        2>/dev/null)

    log_info "Server created with ID: $SERVER_ID"
    sleep 20
fi

SERVER_IP=$(hcloud server get "$SERVER_ID" --format ipv4 --no-header 2>/dev/null)

if [[ -z "$SERVER_IP" ]]; then
    log_error "Failed to get server IP"
    exit 1
fi

log_info "Server IP: $SERVER_IP"

log_info "Waiting for SSH to be available..."
for i in {1..30}; do
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@"$SERVER_IP" "echo 'ready'" &>/dev/null; then
        log_info "SSH is available"
        break
    fi
    if [[ $i -eq 30 ]]; then
        log_error "SSH not available after 30 attempts"
        exit 1
    fi
    sleep 2
done

log_info "Deploying crypto trading agent..."

ssh -o StrictHostKeyChecking=no root@"$SERVER_IP" << 'ENDSSH'
set -e

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

systemctl start docker || service docker start || true
systemctl enable docker || true

mkdir -p /opt/crypto-trading-agent
cd /opt/crypto-trading-agent

cat > .env << 'EOF'
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
POSTGRES_PASSWORD=change_me_in_production
LOG_LEVEL=INFO
EOF

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

cat > Dockerfile.prod << 'EOF'
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*
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

mkdir -p config
docker-compose build agent
docker-compose up -d

echo "Deployment complete!"
docker-compose ps
ENDSSH

log_info "Deployment complete!"
log_info "Server IP: $SERVER_IP"
log_info ""
log_info "Services:"
log_info "  - Agent API: http://$SERVER_IP:8000"
log_info "  - Prometheus: http://$SERVER_IP:9090"
log_info "  - Grafana: http://$SERVER_IP:3000 (admin/admin)"
log_info ""
log_info "Next steps:"
log_info "  1. Edit .env file on the server: ssh root@$SERVER_IP 'nano /opt/crypto-trading-agent/.env'"
log_info "  2. Add your Binance API keys"
log_info "  3. Restart the agent: ssh root@$SERVER_IP 'cd /opt/crypto-trading-agent && docker-compose restart agent'"
