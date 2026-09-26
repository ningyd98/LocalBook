#!/usr/bin/env bash
# LocalNote Phase 0 — local verification gate.
#
# Runs backend pytest, all workspace typechecks, frontend Vitest/build and Ruff.
# Expects dependencies installed (see README quick start / scripts/dev.sh).
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
# In the sandbox the project-owned shim is the only stable pnpm entry point;
# putting its directory on PATH also lets package scripts recursively invoke
# `pnpm` without depending on a global shim.
if [[ -x "$ROOT/.cache/pnpm-bin/pnpm" ]]; then
  PATH="$ROOT/.cache/pnpm-bin:$PATH"
  export PATH
fi

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

# Enforce the complete Ruff rule set on changed production Python files.
echo "== Ruff (strict changed production files) =="
"$PY_VENV" scripts/check-ruff-diff.py

# Stage 1: enforce syntax-critical rules across all production modules.
echo "== Ruff (production syntax-critical rules) =="
"$ROOT/.venv/bin/ruff" check server --select E4,E7,E9 --exclude '*/tests/*'

# Stage 2: report full historical reader/vault debt with file:line diagnostics.
echo "== Ruff (reader/vault historical debt inventory; non-blocking) =="
"$ROOT/.venv/bin/ruff" check server/reader server/vault --output-format concise --exit-zero

echo "== typecheck (all workspace packages) =="
"${PNPM_CMD[@]}" typecheck

echo "== backend pytest =="
"$PY_VENV" -m pytest -q

echo "== frontend tests (vitest run) =="
"${PNPM_CMD[@]}" --filter @localnote/web test

echo "== frontend build =="
"${PNPM_CMD[@]}" --filter @localnote/web build

echo "== all checks passed =="
