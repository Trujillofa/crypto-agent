# Context Hub CLI — Python Implementation Spec

## What is Context Hub?

Context Hub is a CLI tool (Python port of `@aisuite/chub`) that lets AI coding agents and humans search and fetch curated, versioned API documentation and skills from a registry. It bridges the gap between rapidly-evolving APIs and LLM knowledge cutoffs.

## Two Content Types

- **Docs** ("what to know") — API/SDK reference documentation. Large, versioned, per-language variants.
- **Skills** ("how to do it") — Behavioral instructions and coding patterns. Smaller, language-agnostic, can be installed into agent skill directories.

## Architecture

```
Content repo (source of truth)
  ↓ chub build → registry.json + content tree
CDN or local folder ← remote/local source
  ↓ CLI fetches from here
~/.chub/ (local cache)
  ↓ CLI reads from here
Agent/Human (consumes docs via stdout or -o file)
  ↑ CLI also reads directly from
Local folders (private/internal docs)
```

## Commands

| Command | Purpose |
|---|---|
| `chub search [query]` | Search docs/skills; no query lists all |
| `chub get <ids...>` | Fetch docs or skills by ID |
| `chub annotate [id] [note]` | Attach persistent local notes |
| `chub feedback [id] [rating]` | Upvote/downvote (sent to registry) |
| `chub update` | Refresh cached registry |
| `chub cache status\|clear` | Manage local cache |
| `chub build <content-dir>` | Build registry from content directory |

## Global Flags

- `--json` — Structured JSON output
- `--version` — Print CLI version
- `--help` — Show help

## `chub search` Flags

- `--tags <csv>` — Filter by comma-separated tags
- `--lang <language>` — Filter by language
- `--limit <n>` — Max results (default: 20)

## `chub get` Flags

- `--lang <language>` — Language variant (js, py, ts)
- `--version <version>` — Specific doc version
- `--full` — Fetch all files, not just entry point
- `--file <paths>` — Fetch specific file(s) by path
- `-o, --output <path>` — Write to file or directory

## `chub annotate` Flags

- `--clear` — Remove annotation for this entry
- `--list` — List all annotations

## `chub feedback` Flags

- `--label <label>` — Feedback label (repeatable)
- `--lang <language>` — Language variant
- `--file <file>` — Specific file within entry
- `--agent <name>` — AI tool name
- `--model <model>` — LLM model name
- `--status` — Show feedback/telemetry status

Valid labels: `accurate`, `well-structured`, `helpful`, `good-examples`, `outdated`, `inaccurate`, `incomplete`, `wrong-examples`, `wrong-version`, `poorly-structured`.

## `chub update` Flags

- `--force` — Re-download even if cache is fresh
- `--full` — Download full bundle for offline use

## `chub build` Flags

- `-o, --output <path>` — Output directory
- `--base-url <url>` — Base URL for remote serving
- `--validate-only` — Validate content without building

## Local Cache Layout

```
~/.chub/
├── config.yaml              # User config (optional)
├── annotations.json         # Local annotations
└── sources/
    └── community/
        ├── registry.json    # Cached index
        ├── meta.json        # { lastUpdated, registryHash }
        └── data/            # Cached content files
```

## Registry Schema

Registry JSON has:
- `version`, `base_url`, `generated`
- `docs[]` — each with `id`, `name`, `description`, `source`, `tags`, `languages[]`
- `skills[]` — each with `id`, `name`, `description`, `source`, `tags`, `path`, `files`

## Config (`~/.chub/config.yaml`)

```yaml
sources:
  - name: community
    url: https://cdn.aichub.org/v1

source: "official,maintainer,community"   # trust policy
refresh_interval: 86400                   # cache TTL in seconds
telemetry: true
feedback: true
```

## Implementation Plan

1. **Package structure**: `src/chub/` as an installable package; `scripts/chub` as entry point
2. **Dependencies**: `click` (CLI), `PyYAML` (already in project), `httpx` (async HTTP)
3. **Core modules**:
   - `config.py` — load config.yaml, merge env vars, defaults
   - `cache.py` — registry fetch, file fetch, bundle extract
   - `registry.py` — load/filter/query registry
   - `frontmatter.py` — YAML frontmatter parser
   - `output.py` — dual-mode output (human/JSON)
   - `annotations.py` — local annotations read/write
   - `normalize.py` — language aliases
4. **Commands**: each as a Click group/subcommand
5. **Python version**: 3.11+
6. **Entry point**: `chub` command installed via `pip install -e .`
