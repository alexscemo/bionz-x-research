#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 voxivoid
# From voxivoid/a6000-qemu (commit 62cf3bc) tools/port/fetch_avcam.sh.
# Download a Sony updater, cut out its FirmwareData .dat, unpack it with fwtool and keep only av-cam.bin.
# usage: mods/a6000/look/tools/port/fetch_avcam.sh <updater URL> [out root, default mods/a6000/look/firmware_port]
#   -> <out root>/<Update_X>/av-cam.bin  (+ .sha256). The updater is read as data only, never run.
set -euo pipefail
url=$1; root=${2:-$(dirname "$0")/../../firmware_port}
here=$(cd "$(dirname "$0")/../../../../.." && pwd)   # repository root
fwtool=${FWTOOL:-$here/../fwtool.py}
name=$(basename "$url" .exe)
out=$root/$name
[ -f "$out/av-cam.bin" ] && { echo "$name: have it"; exit 0; }
mkdir -p "$out"; work=$(mktemp -d "$out/work.XXXX"); trap 'rm -rf "$work"' EXIT
curl -sfL -A "Mozilla/5.0" -o "$work/u.exe" "${url%%\?*}?fm=jp"
python3 -I - "$work/u.exe" "$work/fw.dat" <<'EOF'
import struct, sys
d = open(sys.argv[1], 'rb').read()
off = d.index(b'\x89UFU\r\n\x1a\n')
i = d.find('FirmwareData_'.encode('utf-16-le'))
size = struct.unpack('<I', d[i - 4:i])[0] if i > 4 else len(d) - off
open(sys.argv[2], 'wb').write(d[off:off + size])
EOF
"$fwtool/.venv/bin/python" "$fwtool/fwtool.py" unpack -f "$work/fw.dat" -o "$work/u" > "$work/fwtool.log" 2>&1 || { tail -5 "$work/fwtool.log"; exit 1; }
f=$(find "$work/u" -name av-cam.bin | head -1)
[ -n "$f" ] || { echo "$name: no av-cam.bin"; exit 1; }
mv "$f" "$out/av-cam.bin"; sha256sum "$out/av-cam.bin" > "$out/av-cam.bin.sha256"
echo "$name: $(stat -c %s "$out/av-cam.bin") B"
