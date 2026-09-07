#!/usr/bin/env bash
# LocalNote Phase 0 — local verification gate.
#
# Runs, in order: backend pytest, protocol+web typecheck, frontend Vitest,
# frontend production build. Expects dependencies installed (see README
# quick start / scripts/dev.sh).
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY_VENV="$ROOT/.venv/bin/python"
[[ -x "$PY_VENV" ]] || { echo "error: $PY_VENV missing — run 'uv sync --dev' first" >&2; exit 1; }
[[ -d node_modules ]] || { echo "error: node_modules missing — run 'pnpm install' first" >&2; exit 1; }

echo "== backend pytest =="
"$PY_VENV" -m pytest -q

echo "== typecheck (@localnote/protocol) =="
pnpm --filter @localnote/protocol typecheck

echo "== typecheck (@localnote/graph) =="
pnpm --filter @localnote/graph typecheck

echo "== typecheck (@localnote/web) =="
pnpm --filter @localnote/web typecheck

echo "== frontend tests (vitest run) =="
pnpm --filter @localnote/web test

echo "== frontend build =="
pnpm --filter @localnote/web build

echo "== all checks passed =="
