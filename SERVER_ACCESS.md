# Server Access Guide — trujillo-server (Hetzner)

## Server Details

| Property | Value |
|----------|-------|
| Hostname | `trujillo-server` |
| Public IP | `46.225.119.221` |
| Tailscale IP | `100.103.209.50` |
| SSH User | `root` |
| SSH Key | `~/.ssh/hetzner_deploy` |
| SSH Alias | `crypto-agent` |

---

## Scenario 1: Corporate Network (Fortinet firewall)

Tailscale is blocked on corporate networks with Fortinet TLS inspection. Use the public IP with SSH key.

**Prerequisite:** Your corporate IP must be in the UFW allowlist on the server.

```bash
ssh crypto-agent
```

`~/.ssh/config` must point to the public IP:

```
Host crypto-agent
    HostName 46.225.119.221
    User root
    IdentityFile ~/.ssh/hetzner_deploy
    StrictHostKeyChecking no
```

### If your corporate IP changes

1. Connect via Hetzner web console (see Scenario 3)
2. Update the UFW rule:

```bash
# Remove old rule (find rule number first)
ufw status numbered

# Add new IP
ufw allow from <NEW_IP> to any port 22 proto tcp

# Remove old IP rule by number
ufw --force delete <RULE_NUMBER>
```

---

## Scenario 2: Home / Non-Corporate Network

Tailscale works on networks without TLS inspection. Use the Tailscale IP for a fully private connection (no public port exposure).

### One-time setup (first time on this machine)

```bash
# Arch Linux
sudo pacman -S tailscale
sudo systemctl enable --now tailscaled
sudo tailscale up
# Follow the auth URL in your browser to join the tailnet
```

### Connect

Update `~/.ssh/config` to use the Tailscale IP:

```
Host crypto-agent
    HostName 100.103.209.50
    User root
    IdentityFile ~/.ssh/hetzner_deploy
    StrictHostKeyChecking no
```

```bash
ssh crypto-agent
```

### Switch between IPs quickly

```bash
# Corporate (public IP)
sed -i 's/HostName .*/HostName 46.225.119.221/' ~/.ssh/config

# Home (Tailscale)
sed -i 's/HostName .*/HostName 100.103.209.50/' ~/.ssh/config
```

---

## Scenario 3: Emergency — No SSH Access

Use the Hetzner web console. This is always available regardless of UFW, Tailscale, or network restrictions.

1. Go to [console.hetzner.cloud](https://console.hetzner.cloud)
2. Select **trujillo-server**
3. Click the **`>_`** console icon (top right)
4. Log in as `root`

### If you don't know the root password

Reset it via the Hetzner API:

```bash
curl -s -X POST \
  -H "Authorization: Bearer <HETZNER_API_TOKEN>" \
  -H "Content-Type: application/json" \
  "https://api.hetzner.cloud/v1/servers/121027788/actions/reset_password"
```

The response includes a new `root_password`. Use it in the web console.

### Common emergency fixes

**Re-allow SSH from your IP:**
```bash
ufw allow from <YOUR_IP> to any port 22 proto tcp
```

**Check your current public IP (run locally):**
```bash
curl ifconfig.me
```

**Fully open SSH temporarily (less secure):**
```bash
ufw allow 22/tcp
```

---

## Current UFW Rules on Server

```
[ 1] 22/tcp on tailscale0       ALLOW IN    Anywhere        ← Tailscale access
[ 2] 22/tcp                     ALLOW IN    190.68.153.238  ← Corporate IP
[ 3] 22/tcp (v6) on tailscale0  ALLOW IN    Anywhere (v6)   ← Tailscale IPv6
```

To view current rules on the server:
```bash
ssh crypto-agent "ufw status numbered"
```

---

## Hetzner Infrastructure

### API (for emergency automation)

Store your API token securely (password manager or secrets vault — not in `.env` files tracked by git).

Server ID: `121027788`

Useful API calls:

```bash
# List servers
curl -H "Authorization: Bearer <TOKEN>" https://api.hetzner.cloud/v1/servers

# Request VNC console (one-time URL, expires quickly)
curl -X POST \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  "https://api.hetzner.cloud/v1/servers/121027788/actions/request_console"

# Reset root password
curl -X POST \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  "https://api.hetzner.cloud/v1/servers/121027788/actions/reset_password"
```

### Hetzner Cloud Firewall

A cloud-level firewall (`ssh-tailscale-fallback`) is attached to the server allowing:
- Port 22 from `190.68.153.238/32` (corporate IP)
- Port 22 from `100.0.0.0/8` (Tailscale range)

This operates at the hypervisor level. Note: UFW on the server applies **in addition** to this firewall — both must allow traffic for SSH to work.

---

## Docker Stack Access

Ports are bound to `127.0.0.1` only — accessible only after SSH:

| Service | Tunnel Command | URL |
|---------|---------------|-----|
| TimescaleDB | `ssh -L 25432:127.0.0.1:25432 crypto-agent` | `localhost:25432` |
| Prometheus | `ssh -L 29091:127.0.0.1:29091 crypto-agent` | `localhost:29091` |
| Grafana | `ssh -L 23001:127.0.0.1:23001 crypto-agent` | `localhost:23001` |

Or open all at once:

```bash
ssh -L 25432:127.0.0.1:25432 \
    -L 29091:127.0.0.1:29091 \
    -L 23001:127.0.0.1:23001 \
    crypto-agent
```

---

## Security Checklist

- [ ] Rotate Hetzner API token after any session where it was shared
- [ ] Rotate Tailscale auth keys after use
- [ ] Update UFW allowlist if corporate IP changes
- [ ] Never commit API tokens, SSH keys, or passwords to git
