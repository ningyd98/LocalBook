#!/usr/bin/env bash
# LocalNote Phase 0 — local verification gate.
#
# Runs, in order: backend pytest, protocol+web typecheck, frontend Vitest,
# frontend production build. Expects dependencies installed (see README
# quick start / scripts/dev.sh).
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Match scripts/dev.sh: use the pinned project Node when available, even if the
# invoking shell currently has a different Node major active.
if [[ -f "$ROOT/.nvmrc" && -n "${NVM_DIR:-$HOME/.nvm}" ]]; then
  PINNED_NODE="$(tr -d '[:space:]' < "$ROOT/.nvmrc")"
  PINNED_NODE_BIN="${NVM_DIR:-$HOME/.nvm}/versions/node/v${PINNED_NODE}/bin"
  if [[ -x "$PINNED_NODE_BIN/node" ]]; then
    PATH="$PINNED_NODE_BIN:$PATH"
    export PATH
  fi
fi
export COREPACK_HOME="${COREPACK_HOME:-$ROOT/.cache/corepack}"

PNPM_CMD=()
if command -v pnpm >/dev/null 2>&1; then
  PNPM_CMD=(pnpm)
elif command -v corepack >/dev/null 2>&1; then
  PNPM_CMD=(corepack pnpm)
else
  echo "error: pnpm not found; install/enable pnpm 9.x or provide corepack" >&2
  exit 1
fi

PY_VENV="$ROOT/.venv/bin/python"
[[ -x "$PY_VENV" ]] || { echo "error: $PY_VENV missing — run 'uv sync --dev' first" >&2; exit 1; }
[[ -d node_modules ]] || { echo "error: node_modules missing — run 'pnpm install' first" >&2; exit 1; }

echo "== backend pytest =="
"$PY_VENV" -m pytest -q

echo "== typecheck (@localnote/protocol) =="
"${PNPM_CMD[@]}" --filter @localnote/protocol typecheck

echo "== typecheck (@localnote/graph) =="
"${PNPM_CMD[@]}" --filter @localnote/graph typecheck

echo "== typecheck (@localnote/web) =="
"${PNPM_CMD[@]}" --filter @localnote/web typecheck

echo "== frontend tests (vitest run) =="
"${PNPM_CMD[@]}" --filter @localnote/web test

echo "== frontend build =="
"${PNPM_CMD[@]}" --filter @localnote/web build

echo "== all checks passed =="
