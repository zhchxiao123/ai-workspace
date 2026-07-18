#!/usr/bin/env bash
# Aggregator for all guards. The CHECKS array is the single source of truth:
# CI calls this script, and so do you, before every push.
#
# Keep total wallclock under 30s — a slow gate gets skipped, and a skipped
# gate doesn't exist.
#
# Adding a guard: append one line to CHECKS. If the new rule has legacy
# violators, ship it with a shrink-only allowlist (violators enumerated in
# scripts/check-<x>.allowlist, header "MUST shrink — never add"; new
# violations fail immediately).

set -uo pipefail

# Layout params: env → scripts/.map.conf → the built-in defaults below.
_MAP_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -f "$_MAP_DIR/.map.conf" ] && . "$_MAP_DIR/.map.conf"

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

CHECKS=(
  "scripts/check-doc-discipline.sh"
  "scripts/check-map-territory.sh"
  "scripts/check-entry-freshness.sh"
)

if [ "${#CHECKS[@]}" -eq 0 ]; then
  echo "ERROR: CHECKS is empty in verify.sh" >&2
  exit 2
fi

if [ "${1:-}" = "--dry-list" ]; then
  printf '%s\n' "${CHECKS[@]}"
  exit 0
fi

fail=0
for c in "${CHECKS[@]}"; do
  if out=$(bash "$c" 2>&1); then
    # soft warns must survive a green run
    warns=$(printf '%s\n' "$out" | grep -A 20 '^WARN' || true)
    [ -n "$warns" ] && printf '%s\n' "$warns" | sed "s|^|[$c] |" >&2
  else
    fail=1
    echo "FAIL: $c" >&2
    printf '%s\n' "$out" | sed 's/^/  /' >&2
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "OK: all ${#CHECKS[@]} guards green"
fi
exit "$fail"
