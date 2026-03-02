# Active Agents — crypto-agent

This document lists all AI agents that work on this codebase and the coordination protocol they must follow.

## Source of Truth

All agents MUST read and follow **`CLAUDE.md`** in this project root. It contains:
- Agent coordination protocol (read-before-write, branch discipline, commit conventions)
- Module ownership table
- Python coding standards
- Safety rules

## Active Agents

| Agent | CLI Tool | Instruction File | Co-Authored-By |
|-------|----------|-------------------|----------------|
| Claude Code | `claude` | `CLAUDE.md` (native) | `Claude Opus 4.6 <noreply@anthropic.com>` |
| OpenCode / Codex | `opencode` | `codex-instructions.md` | `OpenCode <noreply@openai.com>` |
| GitHub Copilot | `gh copilot` | `copilot-instructions.md` | `GitHub Copilot <noreply@github.com>` |
| Gemini CLI | `gemini` | `gemini-instructions.md` | `Gemini CLI <noreply@google.com>` |
| Sisyphus | `sisyphus` | `sisyphus-instructions.md` | `Sisyphus <clio-agent@sisyphuslabs.ai>` |
| Human | editor | `CLAUDE.md` | (normal git identity) |

## Coordination Summary

1. **Read before write** — Always read a file before editing it.
2. **One agent per file** — Don't edit files with recent uncommitted changes from another agent.
3. **Test before commit** — Run `pytest` and ensure all tests pass.
4. **Conventional commits** — Use the format in `CLAUDE.md` with your `Co-Authored-By`.
5. **Branch for non-trivial work** — Use `feat/<description>` branches off `main`.
6. **No secrets in code** — Use `.env` for secrets, `.env.example` for templates.
7. **5-Step Engineering Framework** — Apply the framework in `CLAUDE.md` before designing, reviewing, or modifying any component. Challenge requirements, delete first, simplify second, accelerate third, automate last.

## Environments

| Environment | Location | Purpose |
|-------------|----------|---------|
| **Development** | Local (`/home/emilio/crypto-trading-agent`) | Development, testing, experimentation |
| **Production** | Server (`ssh crypto-agent`) | Live trading, real money |

**IMPORTANT**: Always check the crypto-agent server for production state. Local is for development only.

### Server Access

- **SSH alias**: `ssh crypto-agent` (configured in `~/.ssh/config`)
- **Deploy directory**: `/opt/crypto-agent`
- **Current branch**: `main`
- **Fallback SSH**: `ssh root@46.225.119.221` (uses `~/.ssh/id_ed25519`)

### Deployment Commands

```bash
# Deploy to production
ssh crypto-agent "cd /opt/crypto-agent && git pull && docker compose up -d --build agent"

# View logs
ssh crypto-agent "cd /opt/crypto-agent && docker compose logs agent --tail=100 --no-log-prefix"

# Check status
ssh crypto-agent "cd /opt/crypto-agent && docker compose ps"
```
