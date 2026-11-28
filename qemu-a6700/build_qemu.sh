#!/bin/sh
# Build qemu-system-aarch64 10.0.0 with the sony-a6700 machine (Debian packages: build-essential
# ninja-build pkg-config libglib2.0-dev libpixman-1-dev python3-venv flex bison zlib1g-dev libslirp-dev).
# usage: build_qemu.sh /path/to/qemu-10.0.0    (tarball SHA-256 22c075601fdcf8c7b2671a839ebdcef1d4f2973eb6735254fd2e1bd0f30b3896)
set -e
SRC="$1"; HERE="$(cd "$(dirname "$0")" && pwd)"
sh "$HERE/install_machine.sh" "$SRC"
mkdir -p "$SRC/build" && cd "$SRC/build"
[ -f build.ninja ] || ../configure --target-list=aarch64-softmmu --disable-docs --disable-user --disable-tools --disable-guest-agent --disable-gtk --disable-sdl --disable-vnc --disable-opengl --disable-curses --disable-werror
ninja -j"$(nproc)" qemu-system-aarch64
