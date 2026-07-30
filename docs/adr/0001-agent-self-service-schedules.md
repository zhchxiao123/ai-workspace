---
status: accepted
---

# Agent-created Schedules skip the human-approval gate and have no frequency floor

`Continuation` (the existing MCP tool `schedule_continuation`) is deliberately one-shot and self-expiring — it resumes the same native Conversation session and terminates on first fire. Recurring work belongs to `Schedule` instead (see `CONTEXT.md`), so we're adding a `create_schedule` MCP tool that lets a running agent self-serve a standing `daily`/`weekly`/`hourly`/`cron` job, scoped to its own `project_name`/`account`.

Unlike `Continuation`, `Schedule` has no expiry or run-count cap — once created it runs until a human disables it. We decided agent-created Schedules save with `enabled=True` and no minimum interval, identical to what a human already gets via the Web UI, rather than adding an agent-specific approval gate or frequency floor. The compensating control is a `created_by: human | agent` provenance field (plus `created_by_conversation_id`) so a human reviewing the schedule list can tell which ones an agent self-created, and mutation (update/delete/toggle) is restricted to the Conversation that created it — but creation itself is unsupervised.

## Considered options

- **Default `enabled=False`, require a human to flip it on.** Rejected: defeats the point of self-service — every recurring check would still need a human round-trip before it does anything.
- **Enforce a minimum interval (e.g. no more frequent than hourly).** Rejected: a legitimate use case may need higher frequency than that; the guardrail would block correct behavior to catch a hypothetical mistake.
- **Let agents update/delete any Schedule in the project, not just their own.** Rejected: would let one agent session silently mutate or delete a schedule a human configured (or another session created), which is the same class of risk the isolation model exists to prevent, just moved from account/container boundaries to schedule ownership.

## Consequences

A misconfigured `cron_expr` (e.g. an agent meaning "daily" but writing a sub-hourly expression) will fire unsupervised and unbounded until someone notices — there is no automatic circuit breaker. The `created_by` field is the only mitigation, and it's observability, not prevention: a human still has to go look at the schedule list.
