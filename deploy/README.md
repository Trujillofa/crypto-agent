# Cloud Deployment Scripts

This directory contains deployment scripts for various cloud providers.

## Supported Providers

- [DigitalOcean](#digitalocean)
- [Hetzner](#hetzner)

## Prerequisites

1. Install Docker on your local machine
2. Install the CLI tool for your cloud provider
3. Configure API credentials

---

## DigitalOcean

### Prerequisites

```bash
# Install doctl (DigitalOcean CLI)
brew install doctl  # macOS
# or download from https://github.com/digitalocean/doctl/releases

# Authenticate
doctl auth init

# Create API token at https://cloud.digitalocean.com/account/api/tokens
```

### Deploy

```bash
# 1. Set environment variables
export DO_TOKEN="your-digitalocean-token"
export DO_REGION="nyc1"  # or sfo2, fra1, etc.
export DO_SIZE="s-2vcpu-2gb"  # or s-2vcpu-4gb, etc.

# 2. Run deployment
cd deploy
chmod +x digitalocean/deploy.sh
./digitalocean/deploy.sh
```

### What's Created

- 1 Droplet with Docker pre-installed
- Firewall rules for ports 80, 443, 22
- DNS record (optional)
- Docker container with the trading agent

### Cost Estimate

- **s-2vcpu-2gb**: ~$15/month
- **s-2vcpu-4gb**: ~$25/month
- **Transfer**: Free (1TB included)

---

## Hetzner

### Prerequisites

```bash
# Install hcloud (Hetzner CLI)
brew install hcloud  # macOS
# or download from https://github.com/hetznercloud/cli/releases

# Authenticate
hcloud context create my-project

# Create API token at https://console.hetzner.cloud/settings/tokens
```

### Deploy

```bash
# 1. Set environment variables
export HCLOUD_TOKEN="your-hetzner-token"
export HCLOUD_REGION="fsn1"  # or nbg1, ash
export HCLOUD_SERVER_TYPE="cx22"  # or cx32, cx42, etc.

# 2. Run deployment
cd deploy
chmod +x hetzner/deploy.sh
./hetzner/deploy.sh
```

### What's Created

- 1 Cloud Server with Docker pre-installed
- Firewall rules for ports 80, 443, 22
- Docker container with the trading agent

### Cost Estimate

- **cx22**: ~$4.29/month (1vCPU, 2GB RAM)
- **cx32**: ~$8.58/month (2vCPU, 4GB RAM)
- **cx42**: ~$17.16/month (4vCPU, 8GB RAM)
- **Transfer**: Free (20TB included)

---

## Quick Comparison

| Feature | DigitalOcean | Hetzner |
|---------|--------------|---------|
| Starting Price | ~$15/mo | ~$4.29/mo |
| Locations | 14 | 9 |
| Free Transfer | 1TB | 20TB |
| Docker Support | Yes | Yes |
| API CLI | doctl | hcloud |

---

## Security Considerations

1. **Never commit API tokens** - Use environment variables
2. **Enable 2FA** on your cloud provider account
3. **Restrict SSH key** access only from your IP
4. **Use firewall** - Only allow ports 80, 443, 22
5. **Enable backups** for production

---

## Troubleshooting

### DigitalOcean

```bash
# Check droplet status
doctl compute droplet list

# View logs
doctl compute ssh <droplet-id> --command "docker logs crypto-agent"

# SSH into droplet
doctl compute ssh <droplet-id>
```

### Hetzner

```bash
# Check server status
hcloud server list

# View logs
hcloud server ssh <server-name> -- "docker logs crypto-agent"

# SSH into server
hcloud server ssh <server-name>
```
