# Complete Hetzner Cloud Setup Guide for Crypto Trading Agent

## Overview

Complete step-by-step guide to purchase and configure Hetzner cloud server for your crypto trading agent.

---

## Step 1: Create Account

1. Go to https://console.hetzner.com/signup
2. Sign up with email (requires credit card for verification)
3. Verify identity (passport/ID) - usually takes 1-2 hours
4. Create a new project: "crypto-trading"

---

## Step 2: SSH Key Setup (IMPORTANT)

### Option A: Create SSH Key (Recommended)

```bash
ssh-keygen -t ed25519 -C "your-email@example.com"
```

### Add SSH Key to Hetzner

1. Go to **Security** → **SSH Keys** in Hetzner Console
2. Click **Add SSH Key**
3. Paste your public key (from `~/.ssh/id_ed25519.pub`)
4. Name it: "my-laptop"

---

## Step 3: Create Server

### Configuration

| Setting | Value |
|---------|-------|
| **Name** | crypto-agent |
| **Location** | Hillsboro (hil) - USA |
| **Image** | Ubuntu 22.04 LTS |
| **Type** | CPX22 (2 vCPU, 4GB RAM, 80GB SSD) |
| **SSH Key** | Select your SSH key |
| **Networks** | Default (Public IPv4) |

### Cost

| Item | Price |
|------|-------|
| CPX22 | $6.49/month |
| Backup (optional) | +20% ($1.30) |
| **Total** | **$6.49-7.79/month** |

---

## Step 4: Firewall Setup (CRITICAL)

### Create Firewall

1. Go to **Firewalls** → **Create Firewall**
2. Name: `crypto-agent-firewall`

### Inbound Rules

| Port | IP | Description |
|------|-----|-------------|
| 22 | YOUR_IP/32 | SSH (YOUR IP only!) |
| 80 | 0.0.0.0/0 | HTTP |
| 443 | 0.0.0.0/0 | HTTPS |
| 8000 | 0.0.0.0/0 | Prometheus metrics |

---

## Step 5: Backup Configuration

### Enable Backups

1. Go to your server → **Backups**
2. Enable **Automatic Backups**
3. Cost: +20% of server price (~$1.30/mo)

---

## Step 6: Deploy

```bash
ssh root@YOUR_SERVER_IP

git clone https://github.com/Trujillofa/crypto-trading-agent.git
cd crypto-trading-agent

cp .env.example .env
nano .env

docker-compose -f docker-compose.prod.yml up -d
```

---

## Security Checklist

- [x] SSH key only (no password)
- [x] Firewall with limited SSH access
- [x] Automatic backups enabled
- [x] Regular system updates

---

## Cost Summary

| Item | Monthly |
|------|---------|
| CPX22 Server | $6.49 |
| Backups (+20%) | $1.30 |
| **Total** | **$7.79/month** |
