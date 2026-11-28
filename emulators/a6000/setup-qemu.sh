#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Stage the QEMU tree for the sony-a6000 machine (ILCE-6000, CXD90014), the
# port of voxivoid/a6000-qemu (commit 62cf3bc) to the pinned QEMU 11.1.3.
#
# Runs qemu-a6400/setup-qemu.sh unchanged (pinned QEMU, the shared Sony patch
# and qemu-overlay/), then layers the ILCE-6000 parts on the same tree:
#   patches/qemu-v<VERSION>-sony-a6000.patch   build wiring, CPU/L2C/SDIO
#                                              changes and the opt-in properties
#                                              of the shared cxd90014_* models
#   qemu-overlay/                              the ILCE-6000-only device models
# Safe to re-run: the ILCE-6000 hunks are taken out before the shared setup
# runs, and put back after it.
set -euo pipefail
E="$(cd "$(dirname "$0")" && pwd)"
R="$(cd "$E/../.." && pwd)"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["qemu"]["version"])' "$R/tools/toolchain.json")"
Q="${A6400_QEMU_DIR:-$HOME/sony-a6400-qemu-$VERSION}"
PATCH="$E/patches/qemu-v$VERSION-sony-a6000.patch"
[ -f "$PATCH" ] || { echo "No ILCE-6000 patch for QEMU $VERSION: $PATCH" >&2; exit 1; }
FILES="$(sed -n 's|^diff --git a/\([^ ]*\) .*|\1|p' "$PATCH")"

# On a tree staged before, take the ILCE-6000 hunks out again, so that the
# shared setup sees exactly its own patch.
if [ -e "$Q/.git" ]; then
    for f in $FILES; do
        if git -C "$Q" apply --include="$f" --reverse --check "$PATCH" 2>/dev/null; then
            git -C "$Q" apply --include="$f" --reverse "$PATCH"
        fi
    done
fi
bash "$R/qemu-a6400/setup-qemu.sh" > /dev/null

for f in $FILES; do
    git -C "$Q" apply --include="$f" "$PATCH" || {
        echo "ILCE-6000 patch conflicts with $Q/$f" >&2
        exit 1
    }
done
cp -R "$E/qemu-overlay/." "$Q/"
echo "QEMU tree with sony-a6000 ready at: $Q"
echo "Build with:"
echo "  cd \"$Q/build\" && ../configure --target-list=arm-softmmu --enable-sdl --disable-werror && ninja qemu-system-arm qemu-img"
