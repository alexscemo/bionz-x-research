#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 voxivoid
# From voxivoid/a6000-qemu (commit 62cf3bc) mods/look/isp_snap.sh.
# isp_snap.sh NAME - read-only snapshot of the CamCore CC register pages into mods/a6000/look/snaps/NAME.txt  (CAM=<ip> env)
# Then: python3 mods/a6000/look/tools/isp_diff.py mods/a6000/look/snaps/A.txt mods/a6000/look/snaps/B.txt   -> registers that changed between two camera states.
# Pages (offsets from 0xF3200000): 0x0 0x1000 0x3000 0x5000 0x11000..0x17000. Reads only; nothing is written.
set -euo pipefail
CAM=${CAM:?set CAM=<camera ip>}; NAME=${1:?snapshot name}
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../../.." && pwd)
SH="python3 $ROOT/emulators/a6000/tools/a6kshell.py --host $CAM --port 23"
adb connect "$CAM:5555" >/dev/null 2>&1 || true
adb push "$HERE/gammamod" /gammamod >/dev/null 2>&1; $SH run "chmod 755 /android/gammamod" >/dev/null
cmd="rm -f /android/snap.txt;"
for p in 0x0 0x1000 0x3000 0x5000 0x11000 0x12000 0x13000 0x14000 0x15000 0x16000 0x17000; do
  cmd="$cmd /android/gammamod -F hwrd $(printf '0x%X' $((0xF3200000 + p))) 1024 >> /android/snap.txt;"
done
$SH run "$cmd busybox wc -l /android/snap.txt" | tail -1
mkdir -p "$HERE/snaps"; adb pull /snap.txt "$HERE/snaps/$NAME.txt" >/dev/null 2>&1
echo "saved mods/a6000/look/snaps/$NAME.txt"
