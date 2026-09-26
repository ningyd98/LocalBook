#!/usr/bin/env bash
#
#  verify_standalone.sh — one command that reproduces the standalone verification.
#
#  Purpose: the contract (docs/standalone-requirements.md) refers to a single
#  entry point for "build without Xcode + run the app's own self-tests". This
#  script is that entry point and nothing more: it calls the *existing* pipeline
#  and the *existing* self-test outlets, prints each command with its exit code,
#  and does not re-implement a single check.
#
#  Two rules it follows deliberately:
#    · Counts are never asserted. The suite grows (19 -> 33 -> ... -> 66), so the
#      script only *reports* whatever the run just printed. A hard-coded number
#      here would become a false claim the moment someone adds a case.
#    · A failure is loud. Any non-zero step prints the head of its own output and
#      makes the script exit non-zero — no step is allowed to warn and continue.
#
#  Usage:
#      bash scripts/verify_standalone.sh [path-to-ReadFlow-binary]
#
#  Exit code: 0 when every step exited 0; otherwise the first non-zero exit code
#  observed (or 1 when the mismatch was a shape check, e.g. the unknown-scenario
#  guard not returning 2).
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BIN="${1:-$ROOT/dist/ReadFlow.app/Contents/MacOS/ReadFlow}"
LOG="$(mktemp -t verify_standalone)"
OUT="$(mktemp -t verify_standalone_step)"
trap 'rm -f "$LOG" "$OUT"' EXIT

fail=0
step() { printf '\n=== %s\n' "$1"; }

step "1/3  pipeline — build without Xcode, then the storage self-check"
bash "$ROOT/scripts/build_app.sh" --self-check 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
printf '[exit %s] bash scripts/build_app.sh --self-check\n' "$rc"
if [ "$rc" -ne 0 ]; then
    fail=$rc
    printf '\n!! pipeline failed; head of its output:\n'
    sed -n '1,30p' "$LOG"
fi

# Report — never assert — the case count of the run that just happened.
reported="$(grep -o 'self-check: PASS ([0-9]* cases)' "$LOG" | tail -1 || true)"
printf 'storage self-check reported by this run: %s\n' "${reported:-<not found>}"

if [ ! -x "$BIN" ]; then
    printf '\n!! artifact missing or not executable: %s\n' "$BIN"
    exit 1
fi
printf '\nartifact : %s\n' "$BIN"
printf 'sha256   : %s\n' "$(shasum -a 256 "$BIN" | awk '{print $1}')"
printf '（sha256 是主锚；mtime 会随重交付变化，不作为锚）\n'

step "2/3  built-in self-test outlets (no server, no network needed)"
run() {  # run <label> <scenario> [arg]
    local label="$1"; shift
    "$BIN" --self-test "$@" >"$OUT" 2>&1
    local rc=$?
    printf '%-34s exit=%s\n' "$label" "$rc"
    if [ "$rc" -ne 0 ]; then
        fail=$rc
        printf '   !! head of output:\n'
        sed -n '1,20p' "$OUT" | sed 's/^/   /'
    fi
}
run "db-init"                  db-init
run "roundtrip-nil"            roundtrip-nil
run "roundtrip-sentinel-guard" roundtrip-sentinel-guard
run "attribution-nil"          attribution-nil
run "render-row"               render-row
run "render-search-row"        render-search-row
run "s1-unconfigured"          s1-unconfigured
run "s2-unreachable"           s2-unreachable
run "search-query <text>"      search-query 深度学习

step "3/3  family guard — an unknown scenario must exit 2"
"$BIN" --self-test definitely-not-a-scenario >"$OUT" 2>&1
rc=$?
printf '%-34s exit=%s (expected 2)\n' "definitely-not-a-scenario" "$rc"
if [ "$rc" -ne 2 ]; then
    fail=1
    printf '   !! expected exit 2 for an unknown scenario; head of output:\n'
    sed -n '1,20p' "$OUT" | sed 's/^/   /'
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
    printf 'VERIFY: PASS — every step above exited 0.\n'
    printf 'Not covered here (recorded as未覆盖, not as passed): 视觉验收 (A24–A26),\n'
    printf 'GUI 交互路径, and the cloud success / must-surface paths, which need a\n'
    printf 'stub endpoint — see docs/standalone-verification.md for those commands.\n'
else
    printf 'VERIFY: FAIL — first non-zero exit was %s.\n' "$fail"
fi
exit "$fail"
