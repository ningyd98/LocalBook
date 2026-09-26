#!/usr/bin/env bash
#
# build_app.sh — build ReadFlow into a runnable macOS .app bundle *without Xcode*.
#
# Only Command Line Tools are required (`swift`, `plutil`, `file`, and optionally
# `codesign`). Nothing here calls xcodebuild / xcrun / actool / ibtool.
#
# Usage:
#   scripts/build_app.sh                    # release build  → dist/ReadFlow.app
#   scripts/build_app.sh -c debug           # debug build (faster iteration)
#   scripts/build_app.sh --clean            # wipe .build and dist first
#   scripts/build_app.sh --smoke            # also launch the app briefly to prove it stays up
#   scripts/build_app.sh --self-check       # also run the in-binary storage self-check (no XCTest needed)
#   scripts/build_app.sh --verify-stability # re-verify the export after a delay (proves it is on a stable path)
#   scripts/build_app.sh --no-sign          # skip the best-effort ad-hoc signature
#   (dist/ is produced by copying the export that already passed
#    `codesign --verify`; the two executables are asserted byte-identical, so the
#    delivered bundle is provably the one that was verified. The export lives
#    outside the file-provider-managed workspace; pointing READFLOW_EXPORT_DIR
#    inside it is refused — see docs §3.12.)
#   scripts/build_app.sh -o /tmp/out        # custom output directory
#   scripts/build_app.sh --print-env        # show the redirected cache/toolchain paths and exit
#
# Verifying behaviour — the binary is its own test runner (`--self-test`).
#
# A small Foundation-free `ReadFlowTests` target uses swift-testing. Command
# Line Tools do not ship XCTest, and their `_Testing_Foundation` module is
# incomplete for Foundation-heavy assertions. The CLI self-tests remain the
# primary data-layer route: each scenario's exit code is the verdict (0 = pass,
# 1 = assertion failure, 2 = unknown scenario or missing argument).
#
#   APP=dist/ReadFlow.app/Contents/MacOS/ReadFlow     # or the debug binary
#   "$APP" --self-test                                # prints every name, exit 2
#   "$APP" --self-test <scenario>                     # exit 0 = as expected
#
# Scenario names are stable API, not prose: the strings are the ones
# `--self-test` with no argument prints, and the list below is kept in step with
# them (a mismatch between this list and the binary is itself a defect).
# Exit codes are the same for every scenario, so no caller has to parse prose:
#   0  the scenario's expectations held
#   1  at least one assertion failed (each failing line is printed as FAIL)
#   2  unknown scenario name, or a required argument is missing
#
# Scenario           Expected: exit / output you can grep                   Contract
# db-init            exit 0 / "db-init inUse=<the path you asked for>",     A11
#                    "file exists=yes size=<n>", "fts highlights_fts=yes
#                    notes_fts=yes reader_items_fts=yes"
#                    exit 1 when READFLOW_DB_PATH is not the store in use
# roundtrip-nil      exit 0 / "PASS (10 assertions)": Citation(title: nil,  A30/C26
#                    snippet: nil) → addMessage → reopen on a *separate*
#                    connection → both fields still nil; the fixture additionally
#                    covers title/snippet = "" and "   " (the values a naive
#                    `?? ""` would persist) and asserts all three inputs render
#                    as 「无标题来源」/ no-snippet.
#                    NEGATIVE CONTROL, same command, only the fixture changed:
#                      READFLOW_SELFTEST_POISON=1 <binary> --self-test roundtrip-nil
#                    → exit 1 (the fixture writes the placeholder/empty values)
#                    (the older wording here said "PASS (5 assertions)")
#                    snippet: nil) → addMessage → reopen on a *separate*
#                    connection → both fields still nil
# roundtrip-sentinel-guard
#                    exit 0 / "PASS (10 assertions)": THE NEGATIVE CONTROL.
#                    Feeds the detector a poisoned fixture (title = 无标题来源,
#                    snippet = "" / "   ") and requires it to flag each one, both
#                    in memory and after a real round trip through citationsJSON.
#                    A detector that never complains fails this scenario — so
#                    `roundtrip-nil` above cannot be a command that only ever
#                    exits 0. Verified by falsification: patching the detector to
#                    return no violations makes this scenario exit 1.   A30/C26
# render-row         exit 0 / one "info — [<state>] title=[…]               C27/A35
#                    showsSnippet=… snippet=[…] url=[…]" line per state
# render-search-row  exit 0 / "info — [sourceTitle nil + createdAt nil] …   C25/C29
# cjk-recall         exit 0 / prints the whole C7 proof: on a FRESH store, every
#                    ≥2-character CJK substring recalls (深度/学习/练习 → 1) with
#                    negatives at 0; the index holds single-character tokens;
#                    English stemming survives. Then it REWINDS the store to the
#                    pre-v4 shape, shows raw MATCH '"深 度"' = 0, reopens it
#                    (v4 reindex) and shows recall back to 1 with the 6 triggers
#                    restored — i.e. existing rows are reindexed, not just new
#                    ones. (10 assertions)                              C7/A13
#                    displaysDate=false date=[<omitted>] content=[…]"
# s1-unconfigured    exit 0 / "text=未配置 AI 服务：请在「设置 → AI 供应商」" A15
#                    (verbatim §6.2 S1) + a negative control that the
#                    S1 and S2 copy differ
# s2-unreachable     exit 0 / "text=AI 服务当前不可达（…）。已切换为本地检索结果。"
#                    (verbatim §6.2 S2) + "fallbacks=1 {cloud->offline}"  A15
# success            exit 0 / "text=OK" — the mock content fixed by the      A16
#                    contract; override with READFLOW_SELFTEST_EXPECT
# unauthorized       exit 0 / "threw unauthorized" (surfaced, 0 fallbacks)  A17
# timeout            exit 0 / "requestTimeout=<n>s … elapsed=<n>s" then a    A18
#                    .timeout degradation (never .unreachable)
# bad-response       exit 0 / "threw badResponse"                           A19
# rate-limited       exit 0 / "threw serverError(429, …)"                   A19
# attribution-nil    exit 0 / badge copy 未配置 / 不可达 / cloud             C29
#
# Two scenarios take an argument and are owned by other roles; listed here so
# this table keeps matching the binary's own scenario list:
# search-query <text>
#                    exit 0 / the local search path ran and made ZERO outbound
#                    requests (measured, not assumed); 1 = a request was observed
#                    or the store failed; 2 = usage                      A14
# popover-send <text>
#                    exit 0 / drives the LIVE popover answer path and reports
#                    which backend actually answered; 1 = failed; 2 = usage A15-A19
#
# The AI scenarios need a local OpenAI-compatible stub; point them at one with
# READFLOW_SELFTEST_BASE / _KEY / _MODEL (README-free defaults: key `stub-key`,
# model `stub-model`). `s2-unreachable` needs no stub — it dials a dead port.
#
#   build_app.sh --self-check   additionally runs `--self-check-storage` (48 cases:
#                               migrations, round trips, UUID/BLOB keys, CJK recall,
#                               keychain failure + success seams) and fails the
#                               build when it fails.
#
#   READFLOW_SELFCHECK_KEEP=1 build_app.sh --self-check
#                               keeps the self-check's store instead of deleting
#                               it, and prints its path. Needed to judge the FTS
#                               work with system `sqlite3`: the store can only be
#                               *written* through this process (the v4 triggers
#                               call `readflow_fts_transform`, a function no bare
#                               sqlite3 session has), so an independent check has
#                               to be handed an app-written fixture. See
#                               docs/standalone-build.md §3.10.
# --- end of usage block ---
#
# If you need to run `swift build` directly instead of this script, this is the
# exact command it uses — verified on this machine: exit 0, zero "not accessible
# or not writable" / "readonly database" warnings. Copy it verbatim:
#
#     WS=/path/to/LocalBook
#     C="$WS/.cache/readflow-build"
#     mkdir -p "$C/swiftpm-cache" "$C/swiftpm-config" "$C/swiftpm-security" \
#              "$C/clang-module-cache" "$C/swiftpm-module-cache" "$C/home"
#     export CLANG_MODULE_CACHE_PATH="$C/clang-module-cache"
#     export SWIFTPM_MODULECACHE_OVERRIDE="$C/swiftpm-module-cache"
#     cd "$WS/ReadFlow" && swift build \
#       --scratch-path .build \
#       --cache-path "$C/swiftpm-cache" \
#       --config-path "$C/swiftpm-config" \
#       --security-path "$C/swiftpm-security" \
#       --manifest-cache local \
#       --disable-automatic-resolution \
#       --disable-sandbox
#
# (The bare `swift build --disable-sandbox --disable-automatic-resolution` form
# also builds, but still probes ~/Library and prints those warnings.)
#
# Why the odd cache flags below: SwiftPM and clang default to user-level caches in
# ~/Library and /var/folders. Inside a sandboxed/agent run those are not writable,
# which shows up as "Operation not permitted" or "readonly database" errors. The
# module caches and SwiftPM's shared directories are therefore redirected into
# <workspace>/.cache.
#
# Scope of that isolation — deliberately stated precisely, because it is easy to
# over-claim:
#   · ISOLATED: the clang/swiftpm module caches, SwiftPM's cache/config/security
#     paths, the manifest cache and the scratch (build product) directory all live
#     under <workspace>.
#   · NOT isolated: the build still uses the operating system temp directory
#     (SwiftPM's manifest temporary directory, and this script's staged
#     verification copy and smoke-test directory), and SwiftPM still *probes* the
#     real passwd home (~/Library/org.swift.swiftpm) before falling back.
#   · `HOME` is exported below for tidiness, but it does NOT relocate
#     Foundation-resolved paths: measured on this machine with
#     HOME=/tmp/fake-home, `NSHomeDirectory()` still returns /Users/ningyedong and
#     `.applicationSupportDirectory` still resolves to
#     /Users/ningyedong/Library/Application Support. Do not cite HOME as an
#     isolation mechanism.
#   · At runtime the app stores its data in ~/Library/Application Support/ReadFlow
#     (normal user-domain behaviour, overridable with READFLOW_DB_PATH). The build
#     script neither controls nor needs to control that.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve locations
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # directory holding Package.swift
WORKSPACE_DIR="$(cd "$PACKAGE_DIR/.." && pwd)"       # LocalBook workspace root

APP_NAME="ReadFlow"
BUNDLE_ID="com.localbook.readflow"
CONFIGURATION="release"
OUT_DIR="$PACKAGE_DIR/dist"
DO_CLEAN=0
DO_SIGN=1
DO_SMOKE=0
DO_SELF_CHECK=0
VERIFY_STABILITY=0
SMOKE_SECONDS="${READFLOW_SMOKE_SECONDS:-4}"
PRINT_ENV_ONLY=0

# Print the usage block: from the first content line down to the explicit
# `# --- end of usage block ---` marker.
#
# Why a marker and not a rule like "stop at the first bare #": the header's very
# first separator (line 4) is a bare `#`, so such a rule printed a single line —
# and it was signed off as "fixed" because the check only looked at the tail.
# Same class of silent breakage as the earlier hard-coded line range. The marker
# has one job and is visible in diffs.
usage() { awk 'NR < 3 { next } /^# --- end of usage block ---$/ { exit } { print }' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--configuration) CONFIGURATION="${2:?missing value for $1}"; shift 2 ;;
        -o|--output)        OUT_DIR="${2:?missing value for $1}"; shift 2 ;;
        --clean)            DO_CLEAN=1; shift ;;
        --no-sign)          DO_SIGN=0; shift ;;
        --smoke)            DO_SMOKE=1; shift ;;
        --self-check)       DO_SELF_CHECK=1; shift ;;
        --verify-stability) VERIFY_STABILITY=1; shift ;;
        --print-env)        PRINT_ENV_ONLY=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *) echo "build_app.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "$CONFIGURATION" != "debug" && "$CONFIGURATION" != "release" ]]; then
    echo "build_app.sh: -c must be 'debug' or 'release' (got '$CONFIGURATION')" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Redirected caches (module caches + SwiftPM's shared dirs live in the workspace;
# see the scope note at the top of this file for what is NOT isolated)
# ---------------------------------------------------------------------------
CACHE_ROOT="${READFLOW_CACHE_ROOT:-$WORKSPACE_DIR/.cache/readflow-build}"
export CLANG_MODULE_CACHE_PATH="$CACHE_ROOT/clang-module-cache"
export SWIFTPM_MODULECACHE_OVERRIDE="$CACHE_ROOT/swiftpm-module-cache"
# Exported for tidiness only: it does NOT relocate Foundation paths
# (`NSHomeDirectory()` follows passwd — measured), so it is not an isolation
# mechanism. The real redirection is the explicit SwiftPM flags below.
export HOME="$CACHE_ROOT/home"
SWIFTPM_SCRATCH="$PACKAGE_DIR/.build"

# SwiftPM still probes the *passwd* home (~/Library/org.swift.swiftpm and
# ~/Library/Caches/org.swift.swiftpm) regardless of $HOME, which produces
# "… is not accessible or not writable" and "failed storing manifest … attempt
# to write a readonly database" warnings. Pointing --cache-path/--config-path/
# --security-path at the workspace and using --manifest-cache local is what
# actually silences them; $HOME alone does not.
#
# Measured twice, independently (t2 build; verifier A5 check): with this full
# flag set those warnings are **zero**. Hand-running `swift build` without them
# brings them back — so they indicate a missing flag, not a broken environment.
#
# "Nothing is written outside the workspace" is a claim with evidence, measured
# by sampling the real home's SwiftPM directories before and after a full build:
#
#   ~/Library/Caches/org.swift.swiftpm   parent mtime 09:57:59, files 09:59:15
#   ~/Library/org.swift.swiftpm          config 09:57, security fingerprints 09:58
#
#   -> identical before and after, with the same entries (manifests/,
#      repositories/, configuration/, security/), while build products under the
#      workspace were being written. So SwiftPM probes those paths, but the build
#      leaves no artifact there.
#
# (Note for anyone re-measuring: these directories are NOT empty — they hold
# SwiftPM's setup-time content. The evidence is that nothing in them changes, not
# that they are empty. Do not compare a parent directory's mtime either: `..` of
# ~/Library/Caches is written by unrelated software and will differ.)
#
# Scope caveat that keeps the claim honest: the OS temp dir is still used for
# intermediate files, and this says nothing about paths outside the build (the
# app's own store honours READFLOW_DB_PATH / Application Support — see
# `--self-test db-init`).
SWIFTPM_FLAGS=(
    --scratch-path "$SWIFTPM_SCRATCH"
    --cache-path "$CACHE_ROOT/swiftpm-cache"
    --config-path "$CACHE_ROOT/swiftpm-config"
    --security-path "$CACHE_ROOT/swiftpm-security"
    --manifest-cache local
    --disable-automatic-resolution
)

# Extra flags for `swift build`, appended verbatim (word-split on whitespace).
# Example: SWIFT_BUILD_FLAGS="-Xswiftc -warnings-as-errors".
EXTRA_BUILD_FLAGS="${SWIFT_BUILD_FLAGS:-}"
EXTRA_FLAGS=()
if [[ -n "$EXTRA_BUILD_FLAGS" ]]; then
    # shellcheck disable=SC2206  # intentional word splitting of a flag list
    EXTRA_FLAGS=($EXTRA_BUILD_FLAGS)
fi

# --- SwiftPM's own sandbox -------------------------------------------------
# SwiftPM compiles Package.swift through sandbox-exec. When this script runs
# from inside another sandbox (an agent/CI harness, or a container that already
# restricts the process), that nested sandbox cannot be applied and the build
# dies before it starts with:
#
#     sandbox-exec: sandbox_apply: Operation not permitted
#     error: 'readflow': Invalid manifest (compiled with: [...])
#
# That is an environment property, *not* a code defect: running the same command
# in a normal terminal works. The fix is the `--disable-sandbox` flag, but it
# must not be a default — on an unrestricted machine SwiftPM's package sandbox
# is a real protection we do not want to switch off silently (contract C14).
#
# READFLOW_SWIFTPM_SANDBOX controls it:
#   auto     (default) keep SwiftPM's sandbox; if the very first attempt fails
#            with the sandbox_apply/Invalid-manifest signature above, retry once
#            with --disable-sandbox and say so loudly.
#   enabled  never pass the flag (always keep SwiftPM's sandbox).
#   disabled always pass --disable-sandbox.
#
# Whenever the flag is used, the build ran WITHOUT SwiftPM's package sandbox and
# the summary says so, which callers are expected to carry into their reports.
#
# `SWIFTPM_DISABLE_SANDBOX=1` does NOT do this — measured twice, the flag is the
# only lever. Setting that env var changes nothing and silently leaves the nested
# sandbox in place; do not reach for it.
#
# A5 note (contract C14): `--print-env` says which mode is *configured* and that
# the flag is applied only when the probe rejects it. Whether the flag was
# actually used is reported after a real build, in the summary line
# `swiftpm : sandbox ...`, plus a warning — a run that used --disable-sandbox has
# no SwiftPM package sandbox and its reports must say so.
SANDBOX_MODE="${READFLOW_SWIFTPM_SANDBOX:-auto}"
case "$SANDBOX_MODE" in
    auto|enabled|disabled) ;;
    *) fail "READFLOW_SWIFTPM_SANDBOX must be auto|enabled|disabled (got '$SANDBOX_MODE')" ;;
esac

SANDBOX_FLAGS=()
USED_NO_SANDBOX=0
SWIFTPM_SANDBOX_NOTE="auto (probing)"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ "$PRINT_ENV_ONLY" == "1" ]]; then
    cat <<EOF
package dir            : $PACKAGE_DIR
workspace dir          : $WORKSPACE_DIR
swiftpm scratch        : $SWIFTPM_SCRATCH
CLANG_MODULE_CACHE_PATH: $CLANG_MODULE_CACHE_PATH
SWIFTPM_MODULECACHE_OVERRIDE: $SWIFTPM_MODULECACHE_OVERRIDE
HOME                   : $HOME
swiftpm cache/config/security: $CACHE_ROOT/swiftpm-{cache,config,security}
manifest cache         : local (inside .build)
swiftpm sandbox        : $SANDBOX_MODE  (auto = keep it, fall back to --disable-sandbox only if sandbox-exec is denied)
sandbox caveat         : --print-env does not build. After a build, the summary reports the sandbox
                         actually used; a build that used --disable-sandbox ran WITHOUT the SwiftPM
                         package sandbox (contract C14) and its reports must say so.
                         SWIFTPM_DISABLE_SANDBOX=1 is inert - only the --disable-sandbox flag works.
extra swift build flags: ${EXTRA_BUILD_FLAGS:-<none>}
toolchain              : $(command -v swift || echo 'NOT FOUND')
developer dir          : $(xcode-select -p 2>/dev/null || echo 'unavailable')
EOF
    exit 0
fi

command -v swift >/dev/null 2>&1 || fail "'swift' not found on PATH; install Command Line Tools (xcode-select --install)."
[[ -f "$PACKAGE_DIR/Package.swift" ]] || fail "no Package.swift in $PACKAGE_DIR"

# ---------------------------------------------------------------------------
# Fail fast: the export location is known now, so it is validated now — before a
# full build is paid for. See the longer note at the export section for the
# measurements behind this rule.
# ---------------------------------------------------------------------------
RESOLVED_EXPORT_DIR="${READFLOW_EXPORT_DIR:-${TMPDIR:-/tmp}/readflow-export}"
RESOLVED_EXPORT_DIR="${RESOLVED_EXPORT_DIR%/}"
if [[ -n "${READFLOW_ALLOW_INSIDE_WORKSPACE_EXPORT:-}" ]]; then
    case "$RESOLVED_EXPORT_DIR" in
        "$WORKSPACE_DIR"|"$WORKSPACE_DIR"/*)
            warn "READFLOW_EXPORT_DIR=$RESOLVED_EXPORT_DIR is inside $WORKSPACE_DIR;"
            warn "  proceeding only because READFLOW_ALLOW_INSIDE_WORKSPACE_EXPORT is set."
            warn "  A signature verdict taken there may depend on WHEN it is read."
            ;;
    esac
else
    case "$RESOLVED_EXPORT_DIR" in
        "$WORKSPACE_DIR"|"$WORKSPACE_DIR"/*)
            fail "READFLOW_EXPORT_DIR=$RESOLVED_EXPORT_DIR is inside $WORKSPACE_DIR, which is file-provider managed on this machine: the provider re-applies com.apple.FinderInfo to a bundle root within seconds, so the export's verdict would depend on WHEN it is read — the very thing the export exists to avoid. Point it outside the workspace (READFLOW_EXPORT_DIR=/tmp/rf-export), drop the override to use the default (${TMPDIR:-/tmp}/readflow-export), or set READFLOW_ALLOW_INSIDE_WORKSPACE_EXPORT=1 if you accept a time-dependent result."
            ;;
    esac
fi

# Create every cache directory up front so a restricted environment cannot fail
# on a missing parent, and so the redirected paths are visible on disk.
mkdir -p \
    "$CLANG_MODULE_CACHE_PATH" \
    "$SWIFTPM_MODULECACHE_OVERRIDE" \
    "$HOME" \
    "$CACHE_ROOT" \
    "$CACHE_ROOT/swiftpm-cache" \
    "$CACHE_ROOT/swiftpm-config" \
    "$CACHE_ROOT/swiftpm-security"

info "ReadFlow app build"
echo "    package       : $PACKAGE_DIR"
echo "    configuration : $CONFIGURATION"
echo "    swift         : $(swift --version 2>/dev/null | head -1)"
echo "    developer dir : $(xcode-select -p 2>/dev/null || echo 'unavailable')"
echo "    caches        : $CACHE_ROOT"
echo "    swiftpm       : sandbox mode '$SANDBOX_MODE'${EXTRA_BUILD_FLAGS:+ (+ SWIFT_BUILD_FLAGS=\"$EXTRA_BUILD_FLAGS\")}"

# ---------------------------------------------------------------------------
# Optional clean
# ---------------------------------------------------------------------------
if [[ "$DO_CLEAN" == "1" ]]; then
    # Only derived products are removed. `.build/checkouts` and
    # `.build/repositories` are left alone on purpose: they hold the vendored,
    # Package.resolved-pinned dependencies, and a sandboxed/offline run has no
    # way to fetch them again.
    info "cleaning build products (keeping vendored dependencies)"
    rm -rf \
        "$SWIFTPM_SCRATCH"/arm64-apple-macosx \
        "$SWIFTPM_SCRATCH"/x86_64-apple-macosx \
        "$SWIFTPM_SCRATCH"/bin \
        "$SWIFTPM_SCRATCH"/debug \
        "$SWIFTPM_SCRATCH"/release \
        "$SWIFTPM_SCRATCH"/artifacts \
        "$SWIFTPM_SCRATCH"/index \
        "$SWIFTPM_SCRATCH"/ModuleCache \
        "$SWIFTPM_SCRATCH"/clang-module-cache \
        "$SWIFTPM_SCRATCH/build.db" \
        "$SWIFTPM_SCRATCH"/manifest.db \
        "$SWIFTPM_SCRATCH"/*.yaml \
        "$OUT_DIR"
fi

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
# The dependencies are pinned by Package.resolved and already vendored under
# .build/checkouts, so --disable-automatic-resolution keeps this fully offline.
[[ -f "$PACKAGE_DIR/Package.resolved" ]] || warn "Package.resolved missing; SwiftPM may try to reach the network."

BUILD_LOG="$CACHE_ROOT/last-swift-build.log"

# Run `swift build` with the current SANDBOX_FLAGS ($@ = extra args). Output is
# streamed and captured so the sandbox failure can be told apart from a real
# compile error.
swift_build() {
    local rc
    ( cd "$PACKAGE_DIR" && swift build \
        "${SWIFTPM_FLAGS[@]}" \
        "${SANDBOX_FLAGS[@]}" \
        "${EXTRA_FLAGS[@]}" \
        "$@" ) 2>&1 | tee "$BUILD_LOG"
    rc="${PIPESTATUS[0]}"
    return "$rc"
}

# Abort with the tail of the build log, so CI does not silently swallow the
# actual diagnostic.
fail_with_log() {
    local reason="$1"
    echo "---- last swift build output ($BUILD_LOG) ----" >&2
    tail -40 "$BUILD_LOG" | sed 's/^/    | /' >&2
    echo "--------------------------------------------" >&2
    fail "$reason"
}

case "$SANDBOX_MODE" in
    enabled)
        SANDBOX_FLAGS=()
        SWIFTPM_SANDBOX_NOTE="enabled (SwiftPM package sandbox kept; no flag passed)"
        info "swift build"
        swift_build -c "$CONFIGURATION" --product "$APP_NAME" || fail_with_log "swift build failed"
        ;;
    disabled)
        SANDBOX_FLAGS=(--disable-sandbox)
        USED_NO_SANDBOX=1
        SWIFTPM_SANDBOX_NOTE="disabled (--disable-sandbox, requested via READFLOW_SWIFTPM_SANDBOX=disabled)"
        info "swift build"
        swift_build -c "$CONFIGURATION" --product "$APP_NAME" || fail_with_log "swift build failed"
        ;;
    auto)
        SANDBOX_FLAGS=()
        info "swift build"
        if swift_build -c "$CONFIGURATION" --product "$APP_NAME"; then
            SWIFTPM_SANDBOX_NOTE="enabled (SwiftPM package sandbox kept; host allowed sandbox-exec)"
        elif grep -q 'sandbox_apply' "$BUILD_LOG"; then
            warn "SwiftPM's own sandbox could not be applied in this environment"
            warn "  ('sandbox-exec: sandbox_apply: Operation not permitted' while compiling Package.swift)."
            warn "  This is a property of the surrounding sandbox, not of the code. Retrying once with"
            warn "  --disable-sandbox; NOTE this attempt runs WITHOUT SwiftPM's package sandbox."
            SANDBOX_FLAGS=(--disable-sandbox)
            USED_NO_SANDBOX=1
            SWIFTPM_SANDBOX_NOTE="disabled (auto: host sandbox rejected sandbox-exec; --disable-sandbox used)"
            info "swift build (retry with --disable-sandbox)"
            swift_build -c "$CONFIGURATION" --product "$APP_NAME" \
                || fail_with_log "swift build failed even with --disable-sandbox"
        else
            fail_with_log "swift build failed"
        fi
        ;;
esac

echo "    swiftpm       : sandbox $SWIFTPM_SANDBOX_NOTE"

BIN_DIR="$( cd "$PACKAGE_DIR" && swift build \
    "${SWIFTPM_FLAGS[@]}" \
    "${SANDBOX_FLAGS[@]}" \
    "${EXTRA_FLAGS[@]}" \
    -c "$CONFIGURATION" --show-bin-path )"
BIN_PATH="$BIN_DIR/$APP_NAME"
[[ -x "$BIN_PATH" ]] || fail "expected executable at $BIN_PATH but it is missing or not executable"

# ---------------------------------------------------------------------------
# Assemble the .app bundle
# ---------------------------------------------------------------------------
PLIST_SRC="$PACKAGE_DIR/ReadFlow/Resources/Info.plist"
[[ -f "$PLIST_SRC" ]] || fail "missing bundle Info.plist template at $PLIST_SRC"

# Assemble a bundle *from the build inputs* into $1.
#
# Deliberately an assembly step, not a copy of an already-assembled bundle: a
# `cp -R` of a bundle carries the source's extended attributes along (measured:
# `com.apple.FinderInfo` and `com.apple.fileprovider.fpfs#P` survive `cp -R` to
# /tmp, and the copy then fails `codesign --verify` there too). Assembling from
# scratch into a directory outside the file provider means there is nothing to
# inherit. The caller still asserts the resulting attribute set explicitly —
# "it was copied" is not evidence that it is clean.
assemble_bundle() {   # $1 = bundle path
    local bundle="$1"
    local contents="$bundle/Contents"
    local macos="$contents/MacOS"
    local resources="$contents/Resources"

    # Every step reports its own failure. Why explicit `|| return 1` rather than
    # relying on `set -e` alone: at both call sites this function is the left
    # operand of `|| fail …`, and a command that is part of an AND-OR list does
    # **not** abort on `set -e`. Without these checks a failed `cp` would fall
    # through to the next step and the function could still return 0 — which is
    # exactly the "assembly failed but the build reported success" shape that
    # review flagged as P-2.
    rm -rf "$bundle" || return 1
    mkdir -p "$macos" "$resources" || return 1

    cp "$BIN_PATH" "$macos/$APP_NAME" || return 1
    chmod +x "$macos/$APP_NAME" || return 1

    # Bundle Info.plist: concrete values (no $(...) build variables), no
    # NSMainStoryboardFile (a SwiftUI @main App has no storyboard) and
    # LSUIElement=false so the main window is actually visible.
    cp "$PLIST_SRC" "$contents/Info.plist" || return 1

    # Safari launches a native-messaging host without arguments. Ship the small
    # wrapper beside the binary so the host selects the stdio bridge explicitly.
    local native_host="$PACKAGE_DIR/ReadFlowExtension/readflow-native-host.sh"
    if [[ -f "$native_host" ]]; then
        cp "$native_host" "$resources/readflow-native-host" || return 1
        chmod +x "$resources/readflow-native-host" || return 1
    fi

    printf 'APPL????' > "$contents/PkgInfo" || return 1
}

APP_PATH="$OUT_DIR/$APP_NAME.app"
CONTENTS="$APP_PATH/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RESOURCES_DIR="$CONTENTS/Resources"

info "assembling $APP_PATH"
assemble_bundle "$APP_PATH" || fail "cannot assemble $APP_PATH (staging failed; see the tool output above)"

# The asset catalog holds no compiled icon (actool is an Xcode tool), so the
# bundle ships without an AppIcon on purpose.

# ---------------------------------------------------------------------------
# Verify the bundle
# ---------------------------------------------------------------------------
info "verifying bundle"

plutil -lint "$CONTENTS/Info.plist" >/dev/null || fail "Info.plist is not a valid plist"

plist_get() { /usr/libexec/PlistBuddy -c "Print :$1" "$CONTENTS/Info.plist" 2>/dev/null || true; }

EXEC_NAME="$(plist_get CFBundleExecutable)"
[[ "$EXEC_NAME" == "$APP_NAME" ]] || fail "CFBundleExecutable is '$EXEC_NAME', expected '$APP_NAME'"
[[ -f "$MACOS_DIR/$EXEC_NAME" ]] || fail "CFBundleExecutable '$EXEC_NAME' not found in Contents/MacOS"
[[ "$(plist_get CFBundleIdentifier)" == "$BUNDLE_ID" ]] || fail "CFBundleIdentifier mismatch in $CONTENTS/Info.plist"

file "$MACOS_DIR/$APP_NAME" | grep -q 'Mach-O' || fail "$APP_NAME is not a Mach-O executable"

# Architecture, for the summary. `lipo` ships with the base system; `file` is
# the fallback.
ARCHS="$(command -v lipo >/dev/null 2>&1 && lipo -archs "$MACOS_DIR/$APP_NAME" 2>/dev/null || true)"
[[ -n "$ARCHS" ]] || ARCHS="$(file -b "$MACOS_DIR/$APP_NAME")"

# C16 wording rule: only "ad-hoc" or "unsigned" — never "signed"/"notarized".
SIGN_LINE="linker-signed only (Info.plist not bound)"

if [[ "$DO_SIGN" == "1" ]] && command -v codesign >/dev/null 2>&1; then
    # dist/ is deliberately NOT signed in place any more.
    #
    # Rationale (architecture, not a shortcut): the artifact that gets judged
    # must be the artifact that gets delivered, and the judgement must happen
    # where it is deterministic. Signing `$APP_PATH` here would be a write into
    # the file-provider-managed directory — the provider re-applies
    # `com.apple.FinderInfo` to a bundle root within seconds, so the value of a
    # `--verify` taken afterwards depends on *when* it is taken. That was the
    # source of the "valid artifact, exit 1" reports.
    #
    # So the order is inverted: the export is assembled, signed and verified in
    # the clean path first, and `$APP_PATH` is then replaced by a copy of that
    # verified bundle (see "delivering the verified artifact" below). One signing
    # pass, on a stable path, and the delivered bundle is provably the verified
    # one (`cmp` of the executables).
    info "signature will be applied to the clean export, then copied here (see below)"
else
    # No signature pass at all: then this bundle is the deliverable and nothing
    # later replaces it. It stays linker-signed only, and the summary says so.
    warn "no ad-hoc signature requested/available; $APP_PATH stays linker-signed only (Info.plist not bound)"
fi

# ---------------------------------------------------------------------------
# Signature verification (contract A9 / C16)
# ---------------------------------------------------------------------------
# THE JUDGEMENT LOCATION IS THE EXPORT, NOT dist/.
#
# Measured on this workspace (three independent observers agree): ~/Documents is
# managed by a file provider that re-applies `com.apple.FinderInfo` and
# `com.apple.fileprovider.fpfs#P` to a bundle *root* within seconds, so an
# in-place `codesign --verify --deep --strict dist/ReadFlow.app` flips:
#
#     immediately after xattr -cr + re-sign   → exit 0
#     t+3s, and on every later sample         → exit 1 (detritus count 2)
#
# A sample whose value depends on *when* it is taken must never be the pass
# condition. So the artifact that gets judged is a bundle assembled directly
# from the build inputs into a directory outside the file provider — assembled,
# not copied: `cp -R` carries the source's extended attributes with it (measured:
# both names survive a copy to /tmp, and that copy also fails `--verify` there).
# The export's attribute set is then asserted explicitly, because "it was copied
# / it was exported" is not evidence that it is clean.
#
# dist/ keeps only the assertions that cannot change over time: existence,
# executability, a lint-clean Info.plist, Mach-O/arch, and bundle structure.
EXPORT_DIR="$RESOLVED_EXPORT_DIR"

# The export location was validated at startup (see "Fail fast" above), and the
# reason is measured rather than assumed. Reconciling two observations that look
# contradictory — the difference is *where* the bundle lives, not which attribute
# is at fault:
#
#   location                        attributes                       verify
#   workspace (file-provider)       FinderInfo + fpfs#P                1
#   workspace, after xattr -d FinderInfo
#                                   FinderInfo is BACK within the same
#                                   second (the provider restores it)   1
#   /tmp copy (cp -R carries them)  FinderInfo + fpfs#P                1
#   /tmp copy, xattr -d FinderInfo  removal sticks → fpfs#P only       0
#
# So `com.apple.FinderInfo` is the attribute `codesign` rejects — with it gone, a
# bundle that still carries `com.apple.fileprovider.fpfs#P` verifies fine. But
# that is only observable where the removal *sticks*; inside the workspace the
# provider puts FinderInfo back immediately, which is why the recursive `-cr`
# before signing matters there and why any clean window lasts ~3s at best.
#
# Hence: an export inside the workspace is refused (or, with
# READFLOW_ALLOW_INSIDE_WORKSPACE_EXPORT=1, accepted as explicitly
# time-dependent). Everything below assumes the location is trustworthy.
EXPORT_APP="$EXPORT_DIR/$APP_NAME.app"
EXPORT_VERIFY="skipped"
EXPORT_VERIFY_OUTPUT=""
EXPORT_DETRITUS="n/a"
EXPORT_FILEPROVIDER="n/a"
EXPORT_XATTRS="(none)"
# Whether the ad-hoc signature was actually applied to the *export*. Tracked
# separately from `SIGN_LINE` (which describes dist/) because a linker-signed
# binary also passes `codesign --verify`: without this flag a failed re-sign of
# the export would still let the summary claim "Info.plist bound".
EXPORT_SIGNED="no"
DIST_VERIFY="not evaluated"
DIST_DETRITUS="n/a"
DIST_FILEPROVIDER="n/a"
DIST_VERIFY_OUTPUT=""
DIST_XATTRS="(none)"

# Counts the file-provider attributes that make `codesign` refuse a bundle.
#
# Returns `n/a` — not `0` — when the count could not be taken (`xattr` missing,
# or the listing itself failing). Reason: the guard below treats anything other
# than `0` as a failure, and "we could not measure it" must not be reported as
# "it is clean". The previous form piped a failing `xattr -lr` into `grep -c`,
# which prints `0` for empty input — indistinguishable from a clean bundle.
# Attributes that make `codesign --verify --deep --strict` refuse a bundle.
#
# Measured on this machine: **only `com.apple.FinderInfo`**. A bundle root
# carrying `com.apple.fileprovider.fpfs#P` *and* `com.apple.provenance` still
# verifies (exit 0), so the tolerated ones are counted separately — by
# `fileprovider_attribute_count` — and deliberately NOT gated. This count is a
# **pass condition**, and refusing a bundle whose signature verdict is
# trustworthy would be a false failure; the earlier regex folded `fpfs#P` into
# the gate and would have done exactly that.
#
# A missing path prints `PATH_MISSING`, never `0`: `grep -c` answers 0 for an
# empty listing, which is how a deleted artifact would read as "clean" (a path
# was concurrently removed during this session, so this is not hypothetical).
# `n/a` means "could not measure" — also not clean.
detritus_count() {
    [ -d "$1" ] || { printf 'PATH_MISSING'; return 0; }
    command -v xattr >/dev/null 2>&1 || { printf 'n/a'; return 0; }
    local listing
    listing="$(xattr -lr "$1" 2>/dev/null)" || { printf 'n/a'; return 0; }
    printf '%s' "$(printf '%s\n' "$listing" | grep -cE 'com\.apple\.FinderInfo' || true)"
}

# File-provider attributes `codesign` tolerates. Diagnostic only — never used as
# a pass condition (see above).
fileprovider_attribute_count() {
    [ -d "$1" ] || { printf 'PATH_MISSING'; return 0; }
    command -v xattr >/dev/null 2>&1 || { printf 'n/a'; return 0; }
    local listing
    listing="$(xattr -lr "$1" 2>/dev/null)" || { printf 'n/a'; return 0; }
    printf '%s' "$(printf '%s\n' "$listing" | grep -cE 'fileprovider' || true)"
}
xattr_summary() { xattr -l "$1" 2>/dev/null | sed -n 's/:.*//p' | paste -sd, - || true; }

# Run `codesign --verify --deep --strict` and capture its status *safely*.
#
# The status is taken from a redirected command, never from a pipeline: with
# `codesign ... | head`, `$?` is `head`'s status and a failure reads as success.
# (That exact mistake produced a false "0" during review.) The output is kept so
# a non-zero result can be shown verbatim instead of being summarised away.
verify_bundle() {   # $1 = bundle, $2 = file to capture output into
    set +e
    codesign --verify --deep --strict "$1" >"$2" 2>&1
    local status=$?
    set -e
    return "$status"
}

# ---------------------------------------------------------------- export -----
if [[ "$DO_SIGN" == "1" ]] && command -v codesign >/dev/null 2>&1; then
    info "assembling a clean export for signature verification"
    mkdir -p "$EXPORT_DIR" || fail "cannot create export directory $EXPORT_DIR"
    assemble_bundle "$EXPORT_APP" || fail "cannot assemble the export $EXPORT_APP (staging failed)"

    # Belt and braces: a fresh assembly outside the provider should already be
    # clean, but assert rather than assume (the strip is idempotent).
    command -v xattr >/dev/null 2>&1 && xattr -cr "$EXPORT_APP" 2>/dev/null || true

    if codesign --force --sign - "$EXPORT_APP" >/dev/null 2>&1; then
        EXPORT_SIGNED="yes"
        SIGN_ID="$(codesign --display --verbose=2 "$EXPORT_APP" 2>&1 | sed -n 's/^Identifier=//p' | head -1 || true)"
        info "ad-hoc signed the export (codesign -s -)${SIGN_ID:+ (identifier: $SIGN_ID)}"
    else
        # Not fatal by itself: the verify below is what decides. But the fact is
        # recorded, because a linker-signed bundle still satisfies
        # `codesign --verify`, so verify alone cannot distinguish "ad-hoc signed,
        # Info.plist bound" from "never signed at all".
        EXPORT_SIGNED="no"
        warn "ad-hoc codesign of the export failed; leaving it linker-signed"
    fi

    EXPORT_VERIFY_LOG="$CACHE_ROOT/last-codesign-verify-export.log"
    if verify_bundle "$EXPORT_APP" "$EXPORT_VERIFY_LOG"; then
        EXPORT_VERIFY="exit 0"
    else
        EXPORT_VERIFY="exit 1"
        EXPORT_VERIFY_OUTPUT="$(cat "$EXPORT_VERIFY_LOG")"
    fi
    EXPORT_DETRITUS="$(detritus_count "$EXPORT_APP")"
    EXPORT_FILEPROVIDER="$(fileprovider_attribute_count "$EXPORT_APP")"
    EXPORT_XATTRS="$(xattr_summary "$EXPORT_APP")"

    echo "    export  : $EXPORT_APP"
    echo "    export  : codesign --verify --deep --strict → $EXPORT_VERIFY"
    [[ -n "$EXPORT_VERIFY_OUTPUT" ]] && echo "    export  : verify output: $EXPORT_VERIFY_OUTPUT"
    echo "    export  : xattrs = ${EXPORT_XATTRS:-<none>}   (blocking FinderInfo: $EXPORT_DETRITUS; tolerated fileprovider attrs: $EXPORT_FILEPROVIDER)"

    # The export is the pass condition. Both assertions are time-independent:
    # a bundle outside the file provider does not acquire these attributes later.
    [[ "$EXPORT_VERIFY" == "exit 0" ]] || fail "exported bundle failed signature verification ($EXPORT_VERIFY): ${EXPORT_VERIFY_OUTPUT}"
    # A33: hard failure, not a warning. Two reasons this is not negotiable:
    #   · the bundle that was just verified is the one being delivered, so a
    #     detritus count that disagrees with the verify result means the verdict
    #     describes a state the artifact is no longer in — the signature
    #     conclusion is not trustworthy;
    #   · "warn and continue" is the "prints a problem, exits 0" shape that A32/
    #     A33 exist to forbid.
    # Reachability was measured rather than assumed (R5.3): with the export on a
    # non-file-provider path the provider never re-tags it, and the count stayed 0
    # across repeated builds; when the count is forced non-zero the build fails
    # here (injected-detritus probe: exit 1). Both directions are in the record.
    [[ "$EXPORT_DETRITUS" == "0" ]] || fail "exported bundle carries file-provider attributes (count $EXPORT_DETRITUS): $EXPORT_XATTRS — the signature verdict is NOT trustworthy, because the artifact changed after it was verified; this build is refused rather than reported (A33)"

    # ---------------------------------------------------------- delivery ----
    # The verified export *is* the deliverable: replace $APP_PATH with a copy of
    # it. Direction matters — clean → provider-managed, never the reverse. The
    # copy may acquire `com.apple.FinderInfo` here within seconds (the provider
    # does that to any bundle root); that is harmless for delivery and is why
    # the in-place `--verify` below is reported as an informational sample only.
    #
    # `ditto` is used rather than `cp -R` because the copy must preserve the
    # signature's own metadata; neither tool strips FinderInfo (measured for
    # both), which is exactly why copying is not how verification is obtained.
    info "delivering the verified export to $APP_PATH"
    rm -rf "$APP_PATH" || fail "cannot replace $APP_PATH with the verified export"
    ditto "$EXPORT_APP" "$APP_PATH" || fail "cannot copy the verified export to $APP_PATH"

    # Re-assert the structural invariants on the delivered copy: it is a
    # different directory tree than the one the checks above ran against.
    plutil -lint "$CONTENTS/Info.plist" >/dev/null || fail "delivered Info.plist is not a valid plist"
    [[ -f "$MACOS_DIR/$EXEC_NAME" ]] || fail "delivered bundle is missing Contents/MacOS/$EXEC_NAME"
    file "$MACOS_DIR/$APP_NAME" | grep -q 'Mach-O' || fail "delivered $APP_NAME is not a Mach-O executable"

    # The point of the whole exercise, as a check rather than a claim: the
    # executable that ships must be byte-identical to the one that verified.
    if cmp -s "$EXPORT_APP/Contents/MacOS/$APP_NAME" "$MACOS_DIR/$APP_NAME"; then
        echo "    delivery: dist/ executable is byte-identical to the verified export (cmp)"
    else
        fail "the delivered executable differs from the verified export; the signature verdict does not describe what ships"
    fi

    # And the fact that lets the summary speak: the signature that was judged is
    # the one on the bundle being delivered. Guarded, because `--verify` also
    # passes on a *linker-signed* bundle — claiming "Info.plist bound" there
    # would be exactly the false attribution A32 forbids.
    if [[ "$EXPORT_SIGNED" == "yes" ]]; then
        SIGN_LINE="ad-hoc (codesign -s -), Info.plist bound"
    fi

    # Optional stability re-check: proves the export's verdict does not decay the
    # way dist/'s does. Off by default because it costs wall-clock time; the
    # verifier can enable it with --verify-stability.
    if [[ "$VERIFY_STABILITY" == "1" ]]; then
        STABILITY_SECONDS="${READFLOW_STABILITY_SECONDS:-20}"
        info "stability re-check: re-verifying the export after ${STABILITY_SECONDS}s"
        sleep "$STABILITY_SECONDS"
        STABILITY_LOG="$CACHE_ROOT/last-codesign-verify-export-stability.log"
        if verify_bundle "$EXPORT_APP" "$STABILITY_LOG"; then
            echo "    export  : re-verify after ${STABILITY_SECONDS}s → exit 0 (stable)"
        else
            echo "    export  : re-verify after ${STABILITY_SECONDS}s → exit 1: $(cat "$STABILITY_LOG")"
            fail "exported bundle verified once but not after ${STABILITY_SECONDS}s; the export is not on a stable path"
        fi
        echo "    export  : xattrs after ${STABILITY_SECONDS}s = $(xattr_summary "$EXPORT_APP")"
    fi
fi

# ------------------------------------------------------------- dist/ only ----
# In-place verification of dist/ is reported, never used as the pass condition:
# its value is a function of *when* it is read (see the header of this section).
# It must not be summarised as "signed / Info.plist bound" either — that wording
# is reserved for a verdict that came from the export.
if command -v codesign >/dev/null 2>&1; then
    DIST_VERIFY_LOG="$CACHE_ROOT/last-codesign-verify-dist.log"
    if verify_bundle "$APP_PATH" "$DIST_VERIFY_LOG"; then
        DIST_VERIFY="exit 0 (this sample; NOT durable — see docs §4.1)"
    else
        DIST_VERIFY="exit 1 (this sample; NOT durable — see docs §4.1)"
        DIST_VERIFY_OUTPUT="$(cat "$DIST_VERIFY_LOG")"
    fi
    DIST_DETRITUS="$(detritus_count "$APP_PATH")"
    DIST_FILEPROVIDER="$(fileprovider_attribute_count "$APP_PATH")"
    DIST_XATTRS="$(xattr_summary "$APP_PATH")"
    echo "    dist/   : codesign --verify --deep --strict → $DIST_VERIFY"
    echo "    dist/   : xattrs = ${DIST_XATTRS:-<none>}   (blocking FinderInfo: $DIST_DETRITUS; tolerated fileprovider attrs: $DIST_FILEPROVIDER)"
    echo "    dist/   : the workspace is file-provider managed, so this value flips with time;"
    echo "    dist/   : only the export above is used to judge the signature."
fi

# ---------------------------------------------------------------------------
# Optional smoke test
# ---------------------------------------------------------------------------
if [[ "$DO_SMOKE" == "1" ]]; then
    info "smoke test: launching the app for ${SMOKE_SECONDS}s"
    SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/readflow-smoke.XXXXXX")"
    SMOKE_LOG="$SMOKE_DIR/app.log"
    SMOKE_DB="$SMOKE_DIR/readflow.sqlite"

    READFLOW_DB_PATH="$SMOKE_DB" "$MACOS_DIR/$APP_NAME" >"$SMOKE_LOG" 2>&1 &
    SMOKE_PID=$!
    sleep "$SMOKE_SECONDS"

    if kill -0 "$SMOKE_PID" 2>/dev/null; then
        echo "    app stayed alive for ${SMOKE_SECONDS}s (pid $SMOKE_PID)"
        kill "$SMOKE_PID" 2>/dev/null || true
        wait "$SMOKE_PID" 2>/dev/null || true
    else
        wait "$SMOKE_PID" 2>/dev/null
        SMOKE_RC=$?
        sed 's/^/    | /' "$SMOKE_LOG" | tail -20 >&2
        fail "app exited early with status $SMOKE_RC"
    fi

    # Liveness alone only proves a process did not die. Require the launch
    # marker AppDelegate mirrors to (unbuffered) stderr — proof that
    # NSApplicationDelegate ran and that the bundle identity resolved.
    SMOKE_MARKER="$(grep -m1 '^\[ReadFlow\] ✅ ReadFlow launched successfully' "$SMOKE_LOG" || true)"
    if [[ -n "$SMOKE_MARKER" ]]; then
        echo "    $SMOKE_MARKER"
    else
        sed 's/^/    | /' "$SMOKE_LOG" | tail -20 >&2
        fail "no '✅ ReadFlow launched successfully' marker on stderr; the app process ran but never finished launching"
    fi
fi

# ---------------------------------------------------------------------------
# Optional storage self-check
# ---------------------------------------------------------------------------
# Runs the checks compiled into the app binary (`StorageSelfCheck.swift`) against
# a throwaway store: fresh-store migrations, conversation write/append/reopen
# round trip, migration of a store that predates v3, and the UUID-keyed
# lookup/delete paths. XCTest cannot be used here (Command Line Tools ship no
# XCTest module), so this is the executable form of that test. Non-zero exit
# fails the build.
if [[ "$DO_SELF_CHECK" == "1" ]]; then
    info "storage self-check"
    if SELF_CHECK_OUT="$(READFLOW_DB_PATH= "$MACOS_DIR/$APP_NAME" --self-check-storage 2>&1)"; then
        echo "$SELF_CHECK_OUT" | grep -E 'self-check: (PASS|FAIL|note|info)' | sed 's/^\[ReadFlow\] /    /' || true
    else
        echo "$SELF_CHECK_OUT" | grep -E 'self-check: (PASS|FAIL|note|info)' | sed 's/^\[ReadFlow\] /    /' >&2 || true
        fail "storage self-check failed"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
BUNDLE_SIZE="$(du -sh "$APP_PATH" | cut -f1)"
# Contract C16 / A32: the reported signature status is a function of the *facts
# measured on the export*, never of "the codesign command exited 0".
#
# Three facts are required before the words "Info.plist bound" may appear:
#   · `EXPORT_SIGNED == yes` — the ad-hoc signature was actually applied to the
#     export. Required because a linker-signed Mach-O also satisfies
#     `codesign --verify`, so verify alone cannot tell the two apart;
#   · `EXPORT_VERIFY == exit 0` — the export verifies;
#   · `EXPORT_DETRITUS == 0`   — and it carried no file-provider attributes when
#     it verified, otherwise the verdict is not trustworthy.
# Missing any one of them, the line describes what actually happened and
# deliberately omits the "Info.plist bound" claim — including the case where
# dist/ was signed successfully but the export was not, which is precisely the
# state the earlier unconditional wording would have reported as bound.
if [[ "$EXPORT_SIGNED" == "yes" && "$EXPORT_VERIFY" == "exit 0" && "$EXPORT_DETRITUS" == "0" ]]; then
    SIGNATURE_SUMMARY="ad-hoc (codesign -s -), Info.plist bound — verified on the EXPORT"
else
    if [[ "$EXPORT_SIGNED" == "yes" ]]; then
        SIGNATURE_SUMMARY="ad-hoc signature applied to the export but NOT verified (verify=$EXPORT_VERIFY, detritus=$EXPORT_DETRITUS) — do not report this build as signed"
    else
        # Covers --no-sign, no codesign on PATH, and "the re-sign of the export
        # failed". None of these may borrow dist/'s wording.
        SIGNATURE_SUMMARY="unsigned (linker-signed only, Info.plist not bound) — export verify=$EXPORT_VERIFY, detritus=$EXPORT_DETRITUS; do not report this build as signed"
    fi
fi
cat <<EOF

$(printf '\033[1;32m✓\033[0m') built $APP_PATH
    executable : $MACOS_DIR/$APP_NAME ($BUNDLE_SIZE bundle)
    arch       : $ARCHS
    config     : $CONFIGURATION
    swiftpm    : sandbox $SWIFTPM_SANDBOX_NOTE
    signature  : $SIGNATURE_SUMMARY
    export     : $EXPORT_APP
    verify     : export $EXPORT_VERIFY (blocking FinderInfo: $EXPORT_DETRITUS; tolerated fileprovider attrs: $EXPORT_FILEPROVIDER; xattrs: ${EXPORT_XATTRS:-<none>})
    dist/      : in-place $DIST_VERIFY  — time-dependent, not the pass condition
    icon       : none (Assets.xcassets ships no .png and actool requires Xcode)
    run with   : open "$APP_PATH"
    local store: \$READFLOW_DB_PATH overrides ~/Library/Application Support/ReadFlow/readflow.sqlite
EOF

if [[ "$USED_NO_SANDBOX" == "1" ]]; then
    warn "this build ran WITHOUT SwiftPM's package sandbox (--disable-sandbox)."
    warn "  Reason: the surrounding sandbox forbids the nested sandbox-exec that SwiftPM uses to"
    warn "  compile Package.swift. Reports about this build must state that fact (contract C14)."
    warn "  On an unrestricted machine use READFLOW_SWIFTPM_SANDBOX=enabled to keep the sandbox on."
fi
