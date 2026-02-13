# How to Add More AI Agents

This guide explains how to add new AI agents to the crypto-agent project's multi-agent coordination system.

## Overview

Adding a new agent requires three changes:

1. Update `AGENTS.md` with the new agent entry.
2. Create an agent-specific instruction file (`{agent}-instructions.md`) in the root.
3. Use the correct `Co-Authored-By` trailer in commits.

## Step-by-Step Process

### 1. Update AGENTS.md
Add a new row to the **Active Agents** table. Follow the existing format.

### 2. Create Instruction File
Copy the structure from `gemini-instructions.md` or `codex-instructions.md`. It MUST include:
- "Read `CLAUDE.md` first"
- The agent's `Co-Authored-By` identity
- The 5-Step Engineering Framework
- Coordination protocol summary

### 3. Use Co-Authored-By in Commits
Include the trailer at the end of every commit message:
```text
Co-Authored-By: Agent Name <email@domain.com>
```

## Example (Sisyphus)

**AGENTS.md row:**
`| Sisyphus | sisyphus | sisyphus-instructions.md | Sisyphus <clio-agent@sisyphuslabs.ai> |`

**Commit trailer:**
`Co-Authored-By: Sisyphus <clio-agent@sisyphuslabs.ai>`

## Verification
- [ ] Agent appears in `AGENTS.md`
- [ ] Instruction file follows the naming convention
- [ ] Co-Authored-By format matches existing agents
