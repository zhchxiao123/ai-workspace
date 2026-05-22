# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

AICM (AI Code Manager) runs multiple Claude Code / Codex CLI accounts in isolated Docker containers on a single host, pooling quota across accounts. Each account gets its own container with independent auth, proxied through a gost relay so all outbound traffic flows through the host proxy.

## Key commands

```bash
# Container lifecycle
./aicm.sh build              # Build the shared Docker image (first time ~5–10 min)
./aicm.sh apply              # Regenerate docker-compose.yml and restart all containers
./aicm.sh up / down / restart

# Account & project management (edits accounts.conf / projects.conf then requires apply)
./aicm.sh account add <name> TYPE=claude|codex [AUTH=login|env] [PROXY=relay|off]
./aicm.sh project add <name> <account> <host-path>
./aicm.sh login <account>    # Interactive OAuth; starts a temp container if needed

# Scheduler server (required for task commands and Web UI)
./aicm.sh server             # Starts FastAPI on :8765; Web UI at http://localhost:8765

# Task submission (requires server running)
./aicm.sh task run "<prompt>" --project <name> [--auto] [--conversation <id>] [--new-chain <name>]
./aicm.sh task list
./aicm.sh task logs <id> [-f]
```

No test suite exists. Syntax-check the shell script with `bash -n aicm.sh`.

## Architecture

### Two modes of operation

1. **Interactive** — `aicm enter <project>` drops into a container shell; user runs `claude` or `codex` directly.
2. **Scheduled** — `aicm server` runs the FastAPI scheduler; tasks are submitted via CLI or Web UI and executed non-interactively inside containers.

### Component map

| Component | Role |
|-----------|------|
| `aicm/cli.py` | Click entry point for all host-side operations. |
| `aicm.sh` | Backward-compatible thin wrapper that delegates to the Python CLI. |
| `aicm/server/main.py` | FastAPI app. Defines all REST endpoints and WebSocket terminal handler. |
| `aicm/server/scheduler.py` | Core task queue. Resolves account/project, spawns async tasks inside containers via `docker exec`, tracks state. |
| `aicm/server/models.py` | Pydantic models: `Task`, `Conversation`, `Account`, `Project` and their HTTP request/response shapes. |
| `aicm/server/docker_mgr.py` | Docker API wrapper (container status, exec). |
| `aicm/server/terminal.py` | WebSocket ↔ `docker exec -it` bridge for the in-browser terminal. |
| `aicm/server/static/` | Single-page Web UI (vanilla JS, no build step). JS is split into `chat.js`, `projects.js`, `tasks.js`, `accounts.js`, `nav.js`, `state.js`, `utils.js`. |

### Data storage (all under workspace root)

- `config.conf`, `accounts.conf`, `projects.conf` — authoritative config files; `docker-compose.yml` is **generated** from these, never edit it manually.
- `accounts/<name>/` — per-account CLI auth data mounted into containers.
- `tasks/*.json` — task state records.
- `tasks/*.log` — task execution logs.
- `conversations/*.json` — conversation (task chain) records.

### Task / Conversation model

A **Task** is a single `claude`/`codex` invocation inside a container. A **Conversation** is a named chain of tasks that share context via `--resume {native_session_id}`. Tasks store `project` (path) and `project_name` (config name) — both are needed because multiple projects can share the same path, and sidebar grouping uses `project_name` for disambiguation.

### Network isolation

Containers join an `internal: true` Docker network; they cannot reach the public internet directly. All traffic is forced through an `aicm-proxy-relay` container running gost, which forwards to the host proxy (Clash/v2ray). `PROXY=off` accounts skip the relay and connect to the default bridge.

### Frontend conventions

- Global mutable state lives in `state.js` (e.g. `activeConversationId`, `chatNewSessionProject`, `projectsCache`).
- `conversationBelongsToProject` and `taskBelongsToProject` (in `projects.js`) are used everywhere for sidebar grouping — if `project_name` is present, it takes precedence over path matching.
- SSE (`EventSource`) is used for real-time log streaming; WebSocket for the terminal.
- New chat sessions are always started from a per-project "+" button, so `chatNewSessionProject` is pre-set before the empty chat state renders.
