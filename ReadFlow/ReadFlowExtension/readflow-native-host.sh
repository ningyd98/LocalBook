#!/bin/sh
# Native-messaging host wrapper installed inside ReadFlow.app.
# Safari launches the host without arguments; the flag selects stdio mode.
set -eu
exec /Applications/ReadFlow.app/Contents/MacOS/ReadFlow --native-messaging-host
