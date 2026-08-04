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

### Network isolation

Containers join an `internal: true` Docker network; they cannot reach the public internet directly. All traffic is forced through a `coderfleet-proxy-relay` container running gost, which forwards to the host proxy (Clash/v2ray). `PROXY=off` accounts skip the relay and connect to the default bridge.

`--runtime local` accounts (see `docs/accounts.md`, `docs/network.md`) are an explicit, narrower exception to this invariant, not an equivalent alternative: they run as a plain host subprocess, so this network-layer enforcement doesn't exist for them — whether traffic actually goes through the relay depends entirely on the target CLI honoring proxy env vars. Only `TYPE=claude` is allowed to combine `--runtime local` with `--proxy relay`; `TYPE=codex` is rejected outright (`check_account_runtime_proxy_compat` in `coderfleet/server/models.py`) because Codex's own docs don't confirm proxy support (see `docs/research/local-execution-mode.md`). Never extend this allowlist without re-verifying the target CLI's proxy behavior against a primary source first.

### Frontend conventions

- Global mutable state lives in `state.js` (e.g. `activeConversationId`, `chatNewSessionProject`, `projectsCache`).
- `conversationBelongsToProject` and `taskBelongsToProject` (in `projects.js`) are used everywhere for sidebar grouping — if `project_name` is present, it takes precedence over path matching.
- SSE (`EventSource`) is used for real-time log streaming; WebSocket for the terminal.
- New chat sessions are always started from a per-project "+" button, so `chatNewSessionProject` is pre-set before the empty chat state renders.

## North Star

CoderFleet's job is to let one operator run more AI-coding-CLI work than any single account's quota allows, by pooling several accounts (Codex/Claude Code/OpenCode/Hermes/Grok/Kimi) behind strict per-account isolation — container, auth, and proxy egress never leak into each other — coordinated through a shared scheduler. When a trade-off isn't covered by an explicit rule, prefer whichever option (1) keeps account/container/proxy isolation intact over one that's more convenient to build, (2) keeps CLI and Web UI at equal capability rather than shipping a UI-only or CLI-only feature, and (3) never loses or silently corrupts task/conversation state a real user is depending on to resume work.

## Core mental model

1. **One execution atom, two orchestration modes, one read-only overlay.** A **Task** is the only thing that actually runs — one CLI invocation inside one container, with its own state machine. **Conversation** chains Tasks serially via `--resume {native_session_id}`, for interactive/manual work; **Workflow** (`WorkflowTemplate` → `WorkflowRun`) is a declarative DAG for automation (branching, approval, retry). **Board** is a pure overlay: a card points at *either* a Conversation *or* a WorkflowRun for human progress tracking — it never executes and never holds its own task list. Full concept map and "which one do I use" table: `CONTEXT.md`.
2. **Interactive and Scheduled are two different entry points into the same containers, not two products.** `coderfleet enter <project>` drops into a shell where the user runs `claude`/`codex` by hand — no Task record, no scheduler involvement. `coderfleet server` (the FastAPI scheduler) is what turns work into a tracked Task, whether submitted via CLI or Web UI. Code that assumes "there's a Task for this" breaks against interactive sessions, and vice versa.

## Cross-cutting invariants

- **State-file mutations must be a single synchronous read-modify-write, never a separate read then a later write.** `telegram_bridge.py`'s `_advance_offset` vs `notify_task` race (fixed in `b315ea7`): a long-poll offset writeback captured at poll-start clobbered a mapping another coroutine had persisted while the poll was suspended. Correct form: load → mutate in a callback with no `await` inside → save (`_update_state`'s own pattern). Applies to every JSON store under Data storage (`tasks/`, `builds/`, `conversations/`, `telegram_state.json`). No guard script (behavioral); full detail in `docs/agents/key-files.md`'s `telegram_bridge.py` entry.

- **Don't duplicate cross-entry-point logic or constants — extract one shared helper/constant.** The per-conversation pending-task cap was independently reimplemented in `main.py` and `telegram_bridge.py` and drifted; `TASK_STATUS_LABELS` was independently hardcoded in two notifier paths and drifted too (both fixed in the same commit, `b315ea7`). Correct form: `scheduler.conversation_queue_full()` and `models.TASK_STATUS_LABELS` are the single source each entry point calls/reads — any new task-submission or notification entry point must reuse these, not recount/relabel locally.

- **Never silently fall back to a stale or "most recent" default when a reference is invalid — reject or warn explicitly.** Recurred across the Telegram bridge's #46-49 and #51-53 review passes: replying to an untracked message, clicking an expired conversation button, and a deleted topic thread all previously risked silent misrouting. Correct form: return an explicit rejection/warning and log it; see the `telegram_bridge.py` entry in `docs/agents/key-files.md`.

- **A list rerender must not discard an in-progress inline edit; inline-edit input handlers must `stopPropagation()` on click.** Fixed three times in the chat sidebar before the pattern was named (`d6b471e` stale highlight, `bec0523` rename-click bubbling into row select, `40e3e85` rerender destroying an in-flight rename/queue edit). Correct form: check an editing-in-progress flag before any rerender that could touch the item being edited — see `_isRenamingConversation`/`_isEditingQueueItem` in `docs/agents/key-files.md`'s `chat.js` entry.

- **A model field must be fully wired end-to-end (read + write + UI) before it ships — no half-wired "write-only" placeholder fields.** `Board.project_name`, `WorkflowRun.template_version`, and the `artifact_sync` workspace-policy value were added but never connected to real logic, then had to be found and stripped in a dedicated cleanup pass (`0aca8a3`, #32). Correct form: wire the field's read path in the same change that adds it, or don't add it yet.

- **Every user-facing feature must ship with CLI and Web UI at equal capability — never Web-UI-only.** Violated three times: schedules/digest (#12), boards/cards (#18), and workflows (#19) all shipped through the Web UI with zero CLI commands, each requiring a dedicated "CLI/UI 对等" retrofit issue to close the gap. Correct form: when adding a new REST-backed capability, add its `coderfleet <noun> <verb>` CLI counterpart in the same change, output style matching existing `task list/logs` — not as follow-up work.

## Iron rules

- **Run the full test suite after every change, before reporting the work done.** `uv run --with pytest pytest -q`. Correct form: treat a change as incomplete until the full suite has been run and passes (or any failures are understood and called out) — not just the tests touching the edited file.

## Reference map

| When you're working on... | Read first |
|---|---|
| any file in `coderfleet/` | `docs/agents/key-files.md` — find the file's entry |
| triaging or filing GitHub issues, applying labels | `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md` |
| domain modeling, `CONTEXT.md`, ADRs | `docs/agents/domain.md` |

## Maintaining this map

The map grows only on triggers — never speculatively:

1. **After an incident** — write the postmortem in `docs/incidents/`
   (append-only is legal there), then ask "what grep would have prevented
   this" → a new guard, or an append to an existing one.
2. **After editing a load-bearing file** — rewrite its `docs/agents/key-files.md`
   entry in place. Behaviour changed means the entry changes; never append
   history.
3. **Same mistake twice** — triage into exactly one layer:
   machine-checkable → guard in `scripts/verify.sh`'s CHECKS;
   cross-cutting → quad bullet above;
   domain detail → docs/ file + a Reference-map row.

Guards: `bash scripts/verify.sh` before every push (kept under 30s).

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