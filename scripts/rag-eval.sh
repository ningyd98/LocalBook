#!/usr/bin/env bash
# LocalNote M14 — RAG golden-dataset gate.
#
# Runs the RAG test suite (unit/integration/E2E/eval/perf) and then the
# evaluation harness with its regression floors, printing the Recall@K / MRR
# table so a quality drop is visible in CI logs and not only in a failed assert.
#
# Usage:
#   ./scripts/rag-eval.sh              # gates + printed report
#   ./scripts/rag-eval.sh --no-tests   # report only (fast, for local iteration)
#
# The vault argument is a throwaway directory: the harness never touches a real
# user Vault, and the RAG index it builds lives in that directory.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_TESTS=1
for arg in "$@"; do
  case "$arg" in
    --no-tests) RUN_TESTS=0 ;;
    *) echo "error: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

PY_VENV="$ROOT/.venv/bin/python"
[[ -x "$PY_VENV" ]] || { echo "error: $PY_VENV missing — run 'uv sync --dev' first" >&2; exit 1; }

if [[ "$RUN_TESTS" == "1" ]]; then
  echo "== RAG test suite =="
  "$PY_VENV" -m pytest tests/rag -q
fi

EVAL_DIR="$(mktemp -d)"
trap 'rm -rf "$EVAL_DIR"' EXIT

echo "== RAG golden-dataset evaluation =="
PYTHONPATH="$ROOT" "$PY_VENV" -m tests.rag.eval.run --vault "$EVAL_DIR" --check

echo "== RAG gate passed =="
