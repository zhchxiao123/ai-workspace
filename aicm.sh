#!/usr/bin/env bash
# aicm.sh — thin wrapper; delegates to the 'aicm' Python CLI.
# Kept for backward compatibility with existing workflows.
# New users: pipx install aicm

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Use the repo directory as workspace so existing configs/accounts work as-is.
# New pipx installs get ~/.aicm/ by default (no AICM_WORKSPACE needed).
export AICM_WORKSPACE="${AICM_WORKSPACE:-$SCRIPT_DIR}"

# Prefer locally installed editable package, then system-installed aicm
if python3 -c "import aicm" 2>/dev/null; then
    exec python3 -m aicm "$@"
fi

if command -v aicm &>/dev/null; then
    exec aicm "$@"
fi

echo "Error: 'aicm' CLI not found." >&2
echo "Install with:  pipx install aicm" >&2
echo "Or dev install: pip install -e ." >&2
exit 1
