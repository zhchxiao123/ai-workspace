# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

CoderFleet runs multiple Claude Code / Codex CLI accounts in isolated Docker containers on a single host, pooling quota across accounts. Each account gets its own container with independent auth, proxied through a gost relay so all outbound traffic flows through the host proxy.

## Key commands

```bash
# Container lifecycle
coderfleet build              # Build the shared Docker image (first time ~5–10 min)
coderfleet apply              # Regenerate docker-compose.yml and restart all containers
coderfleet up / down / restart

# Account & project management (edits accounts.conf / projects.conf then requires apply)
coderfleet account add <name> TYPE=claude|codex [--auth login|env] [--proxy relay|off]
coderfleet project add <name> <account> <host-path>
coderfleet login <account>    # Interactive OAuth; starts a temp container if needed

# Scheduler server (required for task commands and Web UI)
coderfleet server             # Starts FastAPI on :8765; Web UI at http://localhost:8765

# Task submission (requires server running)
coderfleet task run "<prompt>" --project <name> [--auto] [--conversation <id>] [--new-chain <name>]
coderfleet task list
coderfleet task logs <id> [-f]
```

Run the Python test suite with `uv run --with pytest pytest -q`.

## Architecture

### Two modes of operation

1. **Interactive** — `coderfleet enter <project>` drops into a container shell; user runs `claude` or `codex` directly.
2. **Scheduled** — `coderfleet server` runs the FastAPI scheduler; tasks are submitted via CLI or Web UI and executed non-interactively inside containers.

### Component map

| Component | Role |
|-----------|------|
| `coderfleet/cli.py` | Click entry point for all host-side operations. |
| `coderfleet/server/main.py` | FastAPI app. Defines all REST endpoints and WebSocket terminal handler. |
| `coderfleet/server/scheduler.py` | Core task queue. Resolves account/project, spawns async tasks inside containers via `docker exec`, tracks state. |
| `coderfleet/server/models.py` | Pydantic models: `Task`, `Conversation`, `Account`, `Project` and their HTTP request/response shapes. |
| `coderfleet/server/docker_mgr.py` | Docker API wrapper (container status, exec). |
| `coderfleet/server/terminal.py` | WebSocket ↔ `docker exec -it` bridge for the in-browser terminal. |
| `coderfleet/server/static/` | Single-page Web UI (vanilla JS, no build step). JS is split into `chat.js`, `projects.js`, `tasks.js`, `accounts.js`, `nav.js`, `state.js`, `utils.js`. |

### Data storage (all under workspace root)

- `config.conf`, `accounts.conf`, `projects.conf` — authoritative config files; `docker-compose.yml` is **generated** from these, never edit it manually.
- `accounts/<name>/` — per-account CLI auth data mounted into containers.
- `tasks/*.json` — task state records.
- `tasks/*.log` — task execution logs.
- `conversations/*.json` — conversation (task chain) records.
- `builds/*.json`, `builds/*.log` — image build records (shared and per-project; CLI- and Web-triggered builds share this store).
- `telegram_state.json` — Telegram bridge state: getUpdates offset, broadcast message_id → conversation mappings for reply routing, `topics` (project → forum thread id registry) + `topic_hint_sent`, plus per-context sections keyed by chat_id or `chat:thread` composite (`defaults` for /use routing, `chat_lists`/`project_lists` numbering snapshots, `pending_new` for the two-step /new flow).

### Task / Conversation model

A **Task** is a single `claude`/`codex` invocation inside a container. A **Conversation** is a named chain of tasks that share context via `--resume {native_session_id}`. Tasks store `project` (path) and `project_name` (config name) — both are needed because multiple projects can share the same path, and sidebar grouping uses `project_name` for disambiguation.

### Network isolation

Containers join an `internal: true` Docker network; they cannot reach the public internet directly. All traffic is forced through a `coderfleet-proxy-relay` container running gost, which forwards to the host proxy (Clash/v2ray). `PROXY=off` accounts skip the relay and connect to the default bridge.

### Frontend conventions

- Global mutable state lives in `state.js` (e.g. `activeConversationId`, `chatNewSessionProject`, `projectsCache`).
- `conversationBelongsToProject` and `taskBelongsToProject` (in `projects.js`) are used everywhere for sidebar grouping — if `project_name` is present, it takes precedence over path matching.
- SSE (`EventSource`) is used for real-time log streaming; WebSocket for the terminal.
- New chat sessions are always started from a per-project "+" button, so `chatNewSessionProject` is pre-set before the empty chat state renders.

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues in `zhchxiao123/ai-workspace` (via `gh` CLI); external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical role names used as-is (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%). Format flags (-c, -l, -L, -o, -Z) run raw.
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->