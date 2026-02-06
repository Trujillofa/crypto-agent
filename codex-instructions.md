# OpenCode / Codex instructions (crypto-agent)

**Read `CLAUDE.md` first.** It is the shared source of truth for all agents on this project.

## Your identity

When committing, use this Co-Authored-By line:
```
Co-Authored-By: OpenCode <noreply@openai.com>
```

## Quick reference

- **Language**: Python 3.11+ (async-first)
- **Tests**: `pytest` (asyncio auto mode via `pyproject.toml`)
- **Config**: `config/settings.yaml` + env vars for secrets
- **Safety**: Paper trading is the default. Never bypass risk checks.

## Before editing any file

1. Read the file first to check for recent changes.
2. Check `git status` to see if another agent has uncommitted edits.
3. Run `pytest` before committing.

## Coordination protocol

See `CLAUDE.md` for the full protocol. Key points:
- Conventional commits: `<type>(<scope>): <description>`
- Branch for non-trivial work: `feat/<short-description>`
- Stage specific files, not `git add -A`.
- All tests must pass before committing.

## Module map

See the module ownership table in `CLAUDE.md` for the full layout. Key entry points:
- `src/main.py` — Application entrypoint
- `src/ingest/binance.py` — Market data ingestion
- `src/execution/executor.py` — Trade execution
- `src/risk/manager.py` — Risk management
