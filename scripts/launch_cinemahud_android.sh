#!/usr/bin/env bash
# Start the PTP live-view bridge (if needed) and launch CinemaHUD on an Android phone over adb.
# macOS/Linux counterpart of launch_cinemahud_android.ps1.
#
# Usage: scripts/launch_cinemahud_android.sh [--device SERIAL] [--install]
#   adb is taken from .android-tools/<darwin|linux>/platform-tools/adb, else from PATH.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

DEVICE_SERIAL=""
INSTALL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --device) DEVICE_SERIAL="${2:?--device needs a value}"; shift 2 ;;
        --install) INSTALL=1; shift ;;
        -h | --help) sed -n '2,6p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

case "$(host_os)" in macos) TOOLS_OS=darwin ;; *) TOOLS_OS=linux ;; esac
ADB="$REPO_ROOT/.android-tools/$TOOLS_OS/platform-tools/adb"
if [ ! -x "$ADB" ]; then
    ADB=$(command -v adb) || die "ADB is missing. See apps/android/cinemahud/README.md."
fi
command -v curl >/dev/null || die "curl is required to probe the PTP server"

if [ -z "$DEVICE_SERIAL" ]; then
    DEVICES=$("$ADB" devices | awk 'NR > 1 && $2 == "device" { print $1 }')
    if [ -z "$DEVICES" ] || [ "$(printf '%s\n' "$DEVICES" | wc -l | tr -d ' ')" -ne 1 ]; then
        die "Connect one authorized Android phone, or pass --device."
    fi
    DEVICE_SERIAL="$DEVICES"
fi

STATUS_URL="http://127.0.0.1:8080/api/status"
if ! curl -fsS --max-time 2 "$STATUS_URL" >/dev/null 2>&1; then
    PY=$(find_python venv) || die "Python 3 was not found; create venv/ or install python3."
    LOG="${TMPDIR:-/tmp}/cinemahud-ptp-server.log"
    (cd "$REPO_ROOT" && nohup "$PY" -u -m tools.research.ptp_liveview_client --source ptp --serve --no-auto-deploy \
        >"$LOG" 2>&1 </dev/null &)
    READY=0
    for _ in $(seq 1 15); do
        sleep 0.7
        if curl -fsS --max-time 1 "$STATUS_URL" >/dev/null 2>&1; then READY=1; break; fi
    done
    [ "$READY" = 1 ] || die "PTP server did not start. Check that the camera is connected in PC Remote (log: $LOG)."
    echo "PTP server started in the background (log: $LOG)."
fi

if [ "$INSTALL" = 1 ]; then
    "$ADB" -s "$DEVICE_SERIAL" install -r "$REPO_ROOT/dist/cinemahud-android/CinemaHUD-Android-arm64.apk" ||
        die "Android installation failed."
fi
"$ADB" -s "$DEVICE_SERIAL" reverse tcp:8080 tcp:8080 || die "USB bridge could not be created."
"$ADB" -s "$DEVICE_SERIAL" shell am force-stop com.cinemahud.cinemahud_android
"$ADB" -s "$DEVICE_SERIAL" shell am start -n com.cinemahud.cinemahud_android/.MainActivity --es bridge http://127.0.0.1:8080 ||
    die "CinemaHUD could not be started."
