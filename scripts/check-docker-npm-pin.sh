#!/usr/bin/env bash
# Guards against a recurrence of docs/incidents/0001-npm-latest-optionaldeps-
# regression.md: the shared Dockerfile's npm self-upgrade must stay pinned to
# a specific version, never `npm@latest` or a bare unversioned `npm`.
#
# Why this matters: npm/cli >=11.5.0 has a confirmed bug (npm/cli #8464,
# #8628) that can silently skip installing a package's optionalDependencies
# — e.g. @anthropic-ai/claude-code's native binary — while still exiting 0.
# `npm@latest` is a moving target; an unrelated edit to an earlier Dockerfile
# layer (invalidating the build cache) is enough to re-resolve it into the
# regression window with no Dockerfile diff to blame.
#
# Env:
#   MAP_DOCKERFILE   path to the Dockerfile to check (default: coderfleet/data/Dockerfile)

set -uo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

DOCKERFILE="${MAP_DOCKERFILE:-coderfleet/data/Dockerfile}"
[ -f "$DOCKERFILE" ] || { echo "OK: no Dockerfile at $DOCKERFILE"; exit 0; }

fail=0

if grep -qE 'npm install -g npm@latest' "$DOCKERFILE"; then
  fail=1
  echo "FAIL: $DOCKERFILE pins npm's own upgrade to @latest" >&2
fi

if grep -qE 'npm install -g npm([[:space:]]|\\|$)' "$DOCKERFILE"; then
  fail=1
  echo "FAIL: $DOCKERFILE upgrades npm without a version pin (bare 'npm install -g npm')" >&2
fi

if [ "$fail" -eq 1 ]; then
  echo "      Pin to a specific version instead — see docs/incidents/0001-npm-latest-optionaldeps-regression.md" >&2
  exit 1
fi

echo "OK: npm self-upgrade in $DOCKERFILE is version-pinned"
