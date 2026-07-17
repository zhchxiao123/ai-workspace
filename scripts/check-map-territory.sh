#!/usr/bin/env bash
# Map–territory consistency: every doc path CLAUDE.md points at must exist.
# A moved or deleted doc that leaves a dangling pointer fails here, at commit
# time — not at the moment an agent needs the material.
#
# Scans backtick-quoted paths like `docs/.../file.md` in CLAUDE.md.
# Extend the prefix list (docs|skills) to match your repo's layout.

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

[ -f CLAUDE.md ] || { echo "OK: no CLAUDE.md yet"; exit 0; }

fail=0
for p in $(grep -oE '`(docs|skills)/[A-Za-z0-9._/-]+\.md`' CLAUDE.md | tr -d '`' | sort -u); do
  if [ ! -f "$p" ]; then
    fail=1
    echo "FAIL: CLAUDE.md points at missing $p" >&2
    echo "      Fix the pointer or restore the doc — a dangling map row" >&2
    echo "      silently strands the agent." >&2
  fi
done

[ "$fail" -eq 0 ] && echo "OK: all map pointers resolve"
exit "$fail"
