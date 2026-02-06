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
| Human | editor | `CLAUDE.md` | (normal git identity) |

## Coordination Summary

1. **Read before write** — Always read a file before editing it.
2. **One agent per file** — Don't edit files with recent uncommitted changes from another agent.
3. **Test before commit** — Run `pytest` and ensure all tests pass.
4. **Conventional commits** — Use the format in `CLAUDE.md` with your `Co-Authored-By`.
5. **Branch for non-trivial work** — Use `feat/<description>` branches off `main`.
6. **No secrets in code** — Use `.env` for secrets, `.env.example` for templates.
