#!/usr/bin/env bash
# Launch ecctui (ECOS-themed TUI for the ecc CLI) from this checkout, without installing it.
# Usage: ./run.sh [projects_root] [extra ecctui args]
# projects_root defaults to the current directory.
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${1:-$PWD}"; shift || true
ROOT="$(cd "$ROOT" 2>/dev/null && pwd)" || { echo "run.sh: no such directory: $ROOT" >&2; exit 1; }
cd "$HOME" && PYTHONPATH="$HERE" exec uv run -q --with textual python -m ecctui --root "$ROOT" "$@"
