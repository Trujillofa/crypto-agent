# How to Add More AI Agents for PR Workflow

This guide explains how to add new AI agents to the crypto-agent project's multi-agent coordination system.

## Overview

Adding a new agent requires three changes:

1. Update `AGENTS.md` with the new agent entry
2. Create an agent-specific instruction file
3. Use the correct `Co-Authored-By` trailer in commits

## Prerequisites

Before adding a new agent, ensure you have:

- The agent's CLI tool name (e.g., `sisyphus`, `momus`)
- The agent's official name and email for Co-Authored-By trailers
- Understanding of the project's coordination protocol (read `CLAUDE.md`)

## Step-by-Step Process

### Step 1: Update AGENTS.md

Add a new row to the **Active Agents** table in `AGENTS.md`:

```markdown
| Agent | CLI Tool | Instruction File | Co-Authored-By |
|-------|----------|-------------------|----------------|
| Claude Code | `claude` | `CLAUDE.md` (native) | `Claude Opus 4.6 <noreply@anthropic.com>` |
| OpenCode / Codex | `opencode` | `codex-instructions.md` | `OpenCode <noreply@openai.com>` |
| GitHub Copilot | `gh copilot` | `copilot-instructions.md` | `GitHub Copilot <noreply@github.com>` |
| Gemini CLI | `gemini` | `gemini-instructions.md` | `Gemini CLI <noreply@google.com>` |
| Human | editor | `CLAUDE.md` | (normal git identity) |
| Your New Agent | `your-cli` | `your-agent-instructions.md` | `Agent Name <email@domain.com>` |
```

**Table columns:**
- **Agent**: Human-readable name of the AI agent
- **CLI Tool**: Command used to invoke the agent
- **Instruction File**: Filename for agent-specific instructions
- **Co-Authored-By**: Exact trailer format for git commits

### Step 2: Create Instruction File

Create a new file named `{agent}-instructions.md` in the project root. Follow this template:

```markdown
# {Agent Name} instructions (crypto-agent)

**Read `CLAUDE.md` first.** It is the shared source of truth for all agents on this project.

## Your identity

When committing, use this Co-Authored-By line:
```
Co-Authored-By: {Agent Name} <{email}>
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
```

**Required sections:**
- Header with agent name
- "Read CLAUDE.md first" statement
- Your identity with Co-Authored-By line
- Quick reference (language, tests, config, safety)
- Before editing any file (3-step checklist)
- Coordination protocol (conventional commits, branching)
- Module map (key entry points)

### Step 3: Use Co-Authored-By in Commits

When committing changes as the new agent, include the Co-Authored-By trailer:

```bash
git commit -m "feat(docs): add new agent to coordination system

Add {Agent Name} to AGENTS.md and create instruction file.

Co-Authored-By: {Agent Name} <{email}>"
```

**Important:** The Co-Authored-By line must match exactly what's in `AGENTS.md`.

## Examples

### Example 1: Adding Sisyphus

**AGENTS.md entry:**
```markdown
| Sisyphus | `sisyphus` | `sisyphus-instructions.md` | `Sisyphus <noreply@anthropic.com>` |
```

**Create `sisyphus-instructions.md`:**
```markdown
# Sisyphus instructions (crypto-agent)

**Read `CLAUDE.md` first.** It is the shared source of truth for all agents on this project.

## Your identity

When committing, use this Co-Authored-By line:
```
Co-Authored-By: Sisyphus <noreply@anthropic.com>
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
```

### Example 2: Adding Momus

**AGENTS.md entry:**
```markdown
| Momus | `momus` | `momus-instructions.md` | `Momus <noreply@anthropic.com>` |
```

**Create `momus-instructions.md`:**
```markdown
# Momus instructions (crypto-agent)

**Read `CLAUDE.md` first.** It is the shared source of truth for all agents on this project.

## Your identity

When committing, use this Co-Authored-By line:
```
Co-Authored-By: Momus <noreply@anthropic.com>
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
```

## PR Workflow for Agent Changes

Follow the standard PR workflow when adding a new agent:

### 1. Create a Feature Branch

```bash
git checkout main
git pull origin main
git checkout -b feat/add-{agent-name}-agent
```

### 2. Make Changes

Edit `AGENTS.md` and create the instruction file:

```bash
# Edit AGENTS.md to add the new agent row
# Create {agent}-instructions.md
```

### 3. Commit Changes

Use conventional commit format with the new agent's Co-Authored-By trailer:

```bash
git add AGENTS.md {agent}-instructions.md
git commit -m "feat(docs): add {Agent Name} to multi-agent coordination

- Add {Agent Name} to AGENTS.md active agents table
- Create {agent}-instructions.md with coordination protocol
- Include Co-Authored-By trailer for attribution

Co-Authored-By: {Agent Name} <{email}>"
```

### 4. Push Branch

```bash
git push -u origin feat/add-{agent-name}-agent
```

### 5. Create Pull Request

Using GitHub CLI:

```bash
gh pr create --title "feat(docs): add {Agent Name} to multi-agent coordination" --body "Add {Agent Name} to the active agents list and create instruction file for multi-agent coordination."
```

### 6. Code Review

- Request review from at least 1 team member
- Ensure the Co-Authored-By format matches existing agents
- Verify the instruction file follows the template

### 7. Merge

Once approved:

```bash
gh pr merge --squash
```

## Verification Checklist

Before submitting your PR, verify:

- [ ] `AGENTS.md` table includes the new agent row
- [ ] Instruction file follows the naming convention: `{agent}-instructions.md`
- [ ] Instruction file includes all required sections
- [ ] Co-Authored-By format is consistent with other agents
- [ ] No trailing whitespace in new files
- [ ] Files are saved in the project root (not in subdirectories)

## Related Documentation

- `AGENTS.md` — Full list of active agents
- `CLAUDE.md` — Shared coordination protocol and coding standards
- `docs/PR_WORKFLOW.md` — Complete PR workflow documentation
- `codex-instructions.md` — Example instruction file (OpenCode)
- `copilot-instructions.md` — Example instruction file (GitHub Copilot)
- `gemini-instructions.md` — Example instruction file (Gemini CLI)
