#!/usr/bin/env bash
# Run everything CI runs, locally, in the same order.
#
# Exists because "I ran the tests" and "CI is green" diverged: `ruff check` passes on a
# file that `ruff format --check` rejects, and the failure only shows up after a push.
# If a check matters, it belongs here, not in someone's memory.

set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failed=0

step() {
    local label="$1"
    shift
    printf '\n=== %s\n' "$label"
    if "$@"; then
        printf '    ok\n'
    else
        printf '    FAILED: %s\n' "$label"
        failed=1
    fi
}

# The project interpreter is 3.12 in apps/api/.venv; the machine default is not used.
python_bin="$root/apps/api/.venv/Scripts/python.exe"
[ -x "$python_bin" ] || python_bin="$root/apps/api/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
    printf 'error: no virtualenv at apps/api/.venv — run `cd apps/api && uv sync --python 3.12`\n' >&2
    exit 2
fi

cd "$root/apps/api" || exit 2
step "api · lint" "$python_bin" -m ruff check .
step "api · format" "$python_bin" -m ruff format --check .
step "api · tests" "$python_bin" -m pytest -o addopts="" -q

cd "$root/apps/web" || exit 2
step "web · typecheck" npm run --silent typecheck
step "web · tests" npm test --silent
step "web · build" npm run --silent build

cd "$root" || exit 2
step "secrets · bundle scan" "$python_bin" scripts/check_bundle_secrets.py apps/web/dist
step "compose · config" docker compose config -q

printf '\n'
if [ "$failed" -ne 0 ]; then
    printf 'one or more checks failed\n'
    exit 1
fi
printf 'all checks passed — CI should agree\n'
