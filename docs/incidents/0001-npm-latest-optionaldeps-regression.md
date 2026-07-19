# 0001 — `npm@latest` silently broke `claude`'s native binary install

**Date**: 2026-07-19
**Trigger**: user report — `claude` inside a freshly built container failed with
`Error: claude native binary not installed.`

## What happened

A change bumped the shared image's Node.js from 20.x to 22.x (to satisfy a new
CLI's `engines.node >= 22.19.0`). That edit sits in an early Dockerfile layer,
so it invalidated Docker's build cache for every layer after it — including
`RUN npm install -g npm@latest`, which had always floated to whatever `npm`
resolved to on the registry *at build time*. The previous image never
re-executed that layer (cache hit), so it stayed frozen on an old, working
npm version indefinitely. The rebuild forced a fresh resolution, which landed
on npm 12.0.1.

npm has a confirmed regression from **npm/cli >=11.5.0** onward
([#8464](https://github.com/npm/cli/issues/8464),
[#8628](https://github.com/npm/cli/issues/8628)): it can silently skip
installing a package's `optionalDependencies` while still exiting 0.
`@anthropic-ai/claude-code` ships its native binary as an `optionalDependency`
per host platform — exactly the mechanism the bug breaks. `claude` installed
"successfully" with no error, but the binary it execs at runtime was never
placed on disk.

## Why the Node bump gets blamed but isn't the cause

Node 20 and Node 22 both bundle npm 10.x; the actual moving part is the
Dockerfile's own `npm install -g npm@latest` line, unrelated to Node's major
version. Any edit to an earlier layer — not just a Node bump — would have
triggered the same cache invalidation and surfaced the same latent bug. The
Node bump was the trigger this time, not the root cause.

## Fix

Pinned to `npm@11.4.2` — the last published version before the regression
window (11.5.0, 2025-07-24) — with a comment explaining why, so a future
"helpful" bump back to `@latest` doesn't reintroduce this silently.

## What grep would have prevented this

A Dockerfile that never lets its own npm self-upgrade float to `@latest` or a
bare unversioned `npm`. Added as `scripts/check-docker-npm-pin.sh`, wired into
`scripts/verify.sh`'s `CHECKS`.

This guard is scoped to npm's *own* version, not every CLI installed via npm
in this Dockerfile — `codex`/`claude`/`opencode`/`pi` are installed unpinned
(no version, or `@latest`) intentionally, so operators always get each
vendor's newest CLI without a Dockerfile edit. That tradeoff stands; only
npm's own version — which silently changes how *every other* npm install in
the image behaves — needs to stay pinned.
