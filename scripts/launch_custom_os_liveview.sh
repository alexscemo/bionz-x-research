#!/usr/bin/env bash
# Custom OS live-view launcher for macOS/Linux (counterpart of launch_custom_os_liveview.bat).
# The launcher lives in the local, untracked custom_os/ tree.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

PY=$(find_python venv) || die "Python 3 was not found; create venv/ or install python3."
FLOW="$REPO_ROOT/custom_os/launcher/launch_liveview_flow.py"
[ -f "$FLOW" ] || die "missing $FLOW (custom_os/ is a local, untracked tree)"
cd "$REPO_ROOT"
exec "$PY" -u "$FLOW" "$@"
