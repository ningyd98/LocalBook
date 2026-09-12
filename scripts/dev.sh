#!/usr/bin/env bash
# LocalNote Server Phase 0 — one-command dev launcher.
#
# Behaviour:
#   * checks python3 >= 3.12, Node >=22 <23 and pnpm (never silently falls
#     back to npm);
#   * installs Python deps via `uv sync --dev` (or instructs a manual venv when
#     uv is missing) and Node deps via `pnpm install` unless already present or
#     LOCALNOTE_SKIP_INSTALL=1;
#   * refuses to start when a target port is already in use and tells you the
#     override variables — it never kills foreign processes;
#   * starts the FastAPI backend (127.0.0.1:3780) and the Vite frontend
#     (127.0.0.1:5173) and forwards SIGINT/SIGTERM, waits for children, and
#     force-kills stragglers after a grace period;
#   * does not exit because Vault/oMLX/AI is unconfigured.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Prefer the project's pinned Node when nvm is installed. This makes the
# launcher deterministic even when the interactive shell currently points at
# another Node major (for example after opening a new terminal).
if [[ -f "$ROOT/.nvmrc" && -n "${NVM_DIR:-$HOME/.nvm}" ]]; then
  PINNED_NODE="$(tr -d '[:space:]' < "$ROOT/.nvmrc")"
  PINNED_NODE_BIN="${NVM_DIR:-$HOME/.nvm}/versions/node/v${PINNED_NODE}/bin"
  if [[ -x "$PINNED_NODE_BIN/node" ]]; then
    PATH="$PINNED_NODE_BIN:$PATH"
    export PATH
  fi
fi

# Keep Corepack's cache inside the project when it is available. This avoids
# failures on machines where the global Corepack cache is not writable.
export COREPACK_HOME="${COREPACK_HOME:-$ROOT/.cache/corepack}"

LOCALNOTE_HOST="${LOCALNOTE_HOST:-127.0.0.1}"
LOCALNOTE_PORT="${LOCALNOTE_PORT:-3780}"
VITE_HOST="${VITE_HOST:-127.0.0.1}"
VITE_PORT="${VITE_PORT:-5173}"
SKIP_INSTALL="${LOCALNOTE_SKIP_INSTALL:-0}"
RELOAD="${LOCALNOTE_UVICORN_RELOAD:-0}" # set to 1 to enable uvicorn --reload

log() { printf '[dev.sh] %s\n' "$*"; }
die() { printf '[dev.sh] ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
PY_BIN=""
for candidate in python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY_BIN="$(command -v "$candidate")"
    break
  fi
done
[[ -n "$PY_BIN" ]] || die "python3 (>=3.12) not found. Install Python 3.12+ and retry."

if ! "$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  die "python3 is too old ($("$PY_BIN" --version 2>&1)); Python >= 3.12 is required."
fi
log "python: $("$PY_BIN" --version 2>&1) ($PY_BIN)"

if ! command -v node >/dev/null 2>&1; then
  die "node not found. Install Node 22 LTS (>=22 <23), e.g. via nvm: nvm install 22."
fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [[ "$NODE_MAJOR" -lt 22 || "$NODE_MAJOR" -ge 23 ]]; then
  die "node $(node --version) is outside the required range >=22 <23. Use Node 22 LTS (nvm use 22)."
fi
log "node: $(node --version)"

PNPM_CMD=()
if command -v pnpm >/dev/null 2>&1; then
  PNPM_CMD=(pnpm)
elif command -v corepack >/dev/null 2>&1; then
  # Corepack can run the package manager without modifying global PATH.
  PNPM_CMD=(corepack pnpm)
else
  die "pnpm not found. Install it with corepack ('corepack enable') or npm i -g pnpm@9; dev.sh never falls back to npm."
fi
log "pnpm: $("${PNPM_CMD[@]}" --version)"

# ---------------------------------------------------------------------------
# Dependencies (install only when missing or explicitly requested)
# ---------------------------------------------------------------------------
if [[ "$SKIP_INSTALL" == "1" ]]; then
  log "LOCALNOTE_SKIP_INSTALL=1 — skipping dependency installation."
else
  if [[ -x .venv/bin/python ]]; then
    log ".venv already exists — reusing it (delete .venv to reinstall)."
  elif command -v uv >/dev/null 2>&1; then
    log "uv found — running 'uv sync --dev' (project .python-version: 3.12)."
    uv sync --dev
  else
    die "uv not found. Install uv, or prepare a venv manually: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]', then retry."
  fi

  if [[ -d node_modules ]]; then
    log "node_modules exists — skipping pnpm install."
  else
    log "running 'pnpm install'."
    "${PNPM_CMD[@]}" install
  fi
fi

[[ -x .venv/bin/python ]] || die "Python environment missing (.venv). Run uv sync --dev or the pip fallback above."
[[ -d node_modules ]] || die "Node dependencies missing. Run 'pnpm install' (or LOCALNOTE_SKIP_INSTALL=1 after installing)."

# ---------------------------------------------------------------------------
# Port preflight — never start a second server on the same port.
# Probes by actually binding 127.0.0.1:<port> (EADDRINUSE ⇒ taken). This works
# in restricted environments where `lsof` cannot enumerate other sockets.
# ---------------------------------------------------------------------------
port_free() {
  "$PY_BIN" - "$1" <<'PYEOF'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
else:
    sys.exit(0)
finally:
    sock.close()
PYEOF
}

if ! port_free "$LOCALNOTE_PORT"; then
  die "backend port $LOCALNOTE_PORT is already in use. Free it or override: LOCALNOTE_PORT=3790 $0"
fi
if ! port_free "$VITE_PORT"; then
  die "frontend port $VITE_PORT is already in use. Free it or override: VITE_PORT=5174 $0"
fi

# ---------------------------------------------------------------------------
# Launch backend + frontend
# ---------------------------------------------------------------------------
PY_VENV="$ROOT/.venv/bin/python"
UVICORN_ARGS=(
  -m uvicorn server.api.main:app
  --host "$LOCALNOTE_HOST"
  --port "$LOCALNOTE_PORT"
)
if [[ "$RELOAD" == "1" ]]; then
  UVICORN_ARGS+=(--reload)
fi

export VITE_API_PROXY_TARGET="${VITE_API_PROXY_TARGET:-http://127.0.0.1:$LOCALNOTE_PORT}"
export VITE_PORT
export VITE_HOST

log "starting backend: $PY_VENV ${UVICORN_ARGS[*]}"
"$PY_VENV" "${UVICORN_ARGS[@]}" &
BACKEND_PID=$!

log "starting frontend: ${PNPM_CMD[*]} --filter @localnote/web dev --host $VITE_HOST --port $VITE_PORT (proxy /api -> $VITE_API_PROXY_TARGET)"
"${PNPM_CMD[@]}" --filter @localnote/web dev --host "$VITE_HOST" --port "$VITE_PORT" &
FRONTEND_PID=$!

log "LocalNote dev running — web: http://$VITE_HOST:$VITE_PORT  api: http://$LOCALNOTE_HOST:$LOCALNOTE_PORT/api/v1"
log "press Ctrl-C to stop both (SIGINT/SIGTERM are forwarded; stragglers are killed after a grace period)."

# ---------------------------------------------------------------------------
# Lifecycle: forward signals, wait for children, report unexpected exits
# ---------------------------------------------------------------------------
STOPPED=0
CLEANED=0

cleanup() {
  local signame="${1:-signal}"
  if [[ "$CLEANED" == "1" ]]; then
    return
  fi
  CLEANED=1
  STOPPED=1
  log "received $signame — stopping backend ($BACKEND_PID) and frontend ($FRONTEND_PID)"
  kill -TERM "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true

  local waited=0
  while (( waited < 20 )); do
    local alive=0
    kill -0 "$BACKEND_PID" 2>/dev/null && alive=1
    kill -0 "$FRONTEND_PID" 2>/dev/null && alive=1
    if [[ "$alive" == "0" ]]; then
      break
    fi
    sleep 0.25
    waited=$((waited + 1))
  done

  kill -KILL "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" 2>/dev/null || true
  log "all children stopped — bye."
}

trap 'cleanup INT' INT
trap 'cleanup TERM' TERM
trap 'cleanup EXIT' EXIT

# Wait until one of the children exits on its own (crash), or cleanup() runs.
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 0.5
done

if [[ "$STOPPED" != "1" ]]; then
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    wait "$BACKEND_PID"
    BACKEND_CODE=$?
    log "backend exited unexpectedly (code $BACKEND_CODE)"
  else
    wait "$FRONTEND_PID"
    FRONTEND_CODE=$?
    log "frontend exited unexpectedly (code $FRONTEND_CODE)"
  fi
  cleanup "child-exit"
  exit 1
fi

exit 0
