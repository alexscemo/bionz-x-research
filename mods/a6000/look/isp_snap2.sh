#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 voxivoid
# From voxivoid/a6000-qemu (commit 62cf3bc) mods/look/isp_snap2.sh.
# isp_snap2.sh NAME - read-only snapshot of the OTHER ISP/CamCore blocks (bases found in av-cam constructors) into mods/a6000/look/snaps/NAME.blocks.txt
# Output lines keep absolute addresses ("0xf3020008: 4369"), same format as isp_snap.sh; mods/a6000/look/tools/isp_diff.py handles it with --abs.
set -euo pipefail
CAM=${CAM:?set CAM=<camera ip>}; NAME=${1:?name}
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../../.." && pwd)
SH="python3 $ROOT/emulators/a6000/tools/a6kshell.py --host $CAM --port 23"
adb connect "$CAM:5555" >/dev/null 2>&1 || true
pages=""
for b in F3000000 F3020000 F3030000 F3040000 F3100000 F3120000; do for o in 0000 1000 2000 3000; do pages="$pages 0x$(printf '%X' $((0x$b + 0x$o)))"; done; done
pages="$pages 0xF3002000 0xF300C000 0xF3102000 0xF3121000 0xF30A0000 0xF30A1000 0xF3440000 0xF3441000 0xF3445000 0xF3446000 0xF34A0000"
run() { $SH run "rm -f /android/snap2.txt; for p in $1; do /android/gammamod -F hwrd \$p 1024 >> /android/snap2.txt; done; busybox wc -l /android/snap2.txt" | tail -1; }
mkdir -p "$HERE/snaps"; : > "$HERE/snaps/$NAME.blocks.txt"
set -- $pages; half=$(( $# / 2 ))
a="${@:1:$half}"; b="${@:$((half+1))}"
run "$a"; adb pull /snap2.txt "$HERE/snaps/$NAME.p1" >/dev/null 2>&1
run "$b"; adb pull /snap2.txt "$HERE/snaps/$NAME.p2" >/dev/null 2>&1
cat "$HERE/snaps/$NAME.p1" "$HERE/snaps/$NAME.p2" > "$HERE/snaps/$NAME.blocks.txt"; rm -f "$HERE/snaps/$NAME.p1" "$HERE/snaps/$NAME.p2"
echo "saved mods/a6000/look/snaps/$NAME.blocks.txt"
