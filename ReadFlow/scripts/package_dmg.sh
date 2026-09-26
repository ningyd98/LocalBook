#!/usr/bin/env bash
#
#  package_dmg.sh — builds ReadFlow, gives it an icon, and wraps it in a .dmg.
#
#  Why this is not just `hdiutil create`: the app icon has to be *inside* the
#  bundle (Contents/Resources + CFBundleIconFile), and adding files to a signed
#  bundle invalidates its signature — so the icon must be injected and the app
#  re-signed before it is packaged, or the shipped app fails `codesign --verify`.
#
#  Steps, in order:
#    1. draw the icon (packaging/make_icon.py -> .icns), unless already present
#    2. build the app into a staging dir (never into dist/, so the verified
#       delivery artifact is not silently replaced)
#    3. inject the icon, set CFBundleIconFile, re-sign ad-hoc
#    4. verify the staged app: signature, icon present, binary runs
#    5. build the .dmg (app + /Applications symlink + volume icon)
#    6. verify the .dmg: hdiutil verify, mount read-only, re-check signature,
#       detach
#
#  Usage:  bash scripts/package_dmg.sh [-o output_dir]
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUT_DIR="$ROOT/dist-dmg"
while [ $# -gt 0 ]; do
    case "$1" in
        -o|--output) OUT_DIR="$2"; shift 2 ;;
        -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

APP_NAME="ReadFlow"
VOL_NAME="ReadFlow"
STAGE="$(mktemp -d -t rf-dmg-stage)"
STAGE_APP="$STAGE/$APP_NAME.app"
ICNS="$ROOT/packaging/build/AppIcon.icns"
MOUNT_POINT=""

cleanup() {
    [ -n "$MOUNT_POINT" ] && hdiutil detach "$MOUNT_POINT" >/dev/null 2>&1
    rm -rf "$STAGE"
}
trap cleanup EXIT

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------- 1. icon
say "1/6 icon"
if [ ! -f "$ICNS" ]; then
    python3 "$ROOT/packaging/make_icon.py" "$ROOT/packaging/build" || fail "icon generation failed"
fi
[ -f "$ICNS" ] || fail "no .icns at $ICNS"
printf '    icon : %s (%s bytes)\n' "$ICNS" "$(stat -f%z "$ICNS")"

# ---------------------------------------------------------------- 2. build
say "2/6 build (staged, dist/ untouched)"
mkdir -p "$STAGE"
bash "$ROOT/scripts/build_app.sh" --self-check -o "$STAGE" >"$STAGE/build.log" 2>&1
build_rc=$?
if [ "$build_rc" -ne 0 ]; then
    tail -25 "$STAGE/build.log"
    fail "build_app.sh exited $build_rc"
fi
[ -d "$STAGE_APP" ] || fail "no app produced at $STAGE_APP"
grep -E 'self-check: PASS|delivery:' "$STAGE/build.log" | sed 's/^/    /'

# ---------------------------------------------------------------- 3. icon in, re-sign
say "3/6 inject icon + re-sign"
mkdir -p "$STAGE_APP/Contents/Resources"
ditto "$ICNS" "$STAGE_APP/Contents/Resources/AppIcon.icns" || fail "could not copy the icon"
PLIST="$STAGE_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconFile" "$PLIST" >/dev/null 2>&1
/usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$PLIST" || fail "could not set CFBundleIconFile"
# The bundle changed, so its signature is stale: signing is not optional here.
codesign --force --sign - --timestamp=none "$STAGE_APP" >/dev/null 2>&1 \
    || fail "re-signing the app failed"

# ---------------------------------------------------------------- 4. verify the app
say "4/6 verify the staged app"
codesign --verify --deep --strict "$STAGE_APP" || fail "staged app fails codesign --verify"
icon_ok=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIconFile" "$PLIST")
printf '    CFBundleIconFile : %s\n' "$icon_ok"
printf '    icon on disk     : %s\n' "$(stat -f%z "$STAGE_APP/Contents/Resources/AppIcon.icns") bytes"
"$STAGE_APP/Contents/MacOS/$APP_NAME" --self-check-storage 2>&1 | grep -o 'PASS ([0-9]* cases)' | sed 's/^/    storage self-check: /'
"$STAGE_APP/Contents/MacOS/$APP_NAME" --self-test 2>&1 | grep 'known scenarios' | \
    awk -F, '{print "    scenarios: " NF}'

# ---------------------------------------------------------------- 5. dmg
say "5/6 build the .dmg"
mkdir -p "$OUT_DIR"
DMG="$OUT_DIR/$APP_NAME-$VOL_NAME.dmg"
rm -f "$DMG"

# Layout: the app on the left, an Applications symlink on the right — the
# convention a macOS user already knows how to use. (The built app already sits
# at $STAGE/ReadFlow.app; copying it onto itself is not a layout step.)
ln -sfn /Applications "$STAGE/Applications"
ditto "$ICNS" "$STAGE/.VolumeIcon.icns"
SetFile -a C "$STAGE" 2>/dev/null || true

rm -f "$STAGE/build.log"          # build log is not part of the shipped image
if ! hdiutil create -srcfolder "$STAGE" -volname "$VOL_NAME" \
        -fs HFS+ -format UDZO -imagekey zlib-level=9 "$DMG" >"$STAGE/../hdiutil.log" 2>&1; then
    tail -20 "$STAGE/../hdiutil.log"
    fail "hdiutil create failed"
fi
printf '    dmg : %s (%s)\n' "$DMG" "$(du -h "$DMG" | cut -f1)"

# ---------------------------------------------------------------- 6. verify the dmg
say "6/6 verify the .dmg"
hdiutil verify "$DMG" >/dev/null || fail "hdiutil verify failed"
MOUNT_POINT="$(hdiutil attach -quiet -nobrowse -readonly -mountpoint "$STAGE/mnt" "$DMG" >/dev/null && echo "$STAGE/mnt")"
[ -n "$MOUNT_POINT" ] || fail "could not mount the dmg"
[ -d "$MOUNT_POINT/$APP_NAME.app" ] || fail "the dmg does not contain $APP_NAME.app"
[ -L "$MOUNT_POINT/Applications" ] || fail "the dmg has no /Applications symlink"
codesign --verify --deep --strict "$MOUNT_POINT/$APP_NAME.app" || fail "the app inside the dmg fails verification"

# Behaviour battery, run on the binary **inside the shipped image** — verifying a
# staging copy while shipping a different file is the classic way to certify
# something nobody receives.
SHIPPED="$MOUNT_POINT/$APP_NAME.app/Contents/MacOS/$APP_NAME"
"$SHIPPED" --self-check-storage 2>&1 | grep -o 'PASS ([0-9]* cases)' \
    | sed 's/^/    storage self-check: /'
battery_fail=0
battery() {   # battery <label> <args...>
    local label="$1"; shift
    "$SHIPPED" --self-test "$@" >/dev/null 2>&1
    local rc=$?
    printf '    %-26s exit=%s\n' "$label" "$rc"
    [ "$rc" -eq 0 ] || battery_fail=1
}
battery db-init                  db-init
battery roundtrip-nil            roundtrip-nil
battery roundtrip-sentinel-guard roundtrip-sentinel-guard
battery attribution-nil          attribution-nil
battery render-row               render-row
battery render-search-row        render-search-row
battery cjk-recall               cjk-recall
battery s1-unconfigured          s1-unconfigured
battery s2-unreachable           s2-unreachable
battery "search-query 深度学习"   search-query 深度学习
battery localbook-connection     localbook-connection
"$SHIPPED" --self-test definitely-not-a-scenario >/dev/null 2>&1
guard=$?
printf '    %-26s exit=%s (expected 2)\n' "unknown scenario guard" "$guard"
[ "$guard" -eq 2 ] || battery_fail=1
[ "$battery_fail" -eq 0 ] || fail "the shipped binary failed its behaviour battery"

hdiutil detach "$MOUNT_POINT" >/dev/null && MOUNT_POINT=""

printf '\n\033[1;32m✓\033[0m %s\n' "$DMG"
printf '    dmg sha256 : %s\n' "$(shasum -a 256 "$DMG" | cut -d' ' -f1)"
