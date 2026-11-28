#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# From voxivoid/a6000-qemu (commit 62cf3bc) run.sh, which adapts Oein/a5000-emulator (GPL-2.0, base commit 6ad36bd) to the ILCE-6000.
# docs/reference/ paths in comments are files in voxivoid/a6000-qemu.
# Boot the official ILCE-6000 3.21 firmware in the sony-a6000 QEMU machine.
#
#   ./run.sh [extra qemu args...]
#
# Default ("camera" boot, like the real device):
#   power-on -> stage-1 loader (Sony NAND stack mounts the emulated NAND and
#   does the bootloader's NAND duties) -> stage 2 = stock kernel + initrd read
#   from NAND /system -> stock /sbin/init ...
# Needs state/a6000.nand (create with ./provision.sh <your NAND dump>).
# The NAND file is never written: guest writes are discarded when QEMU exits.
# With inputs.local.json (tools/inputs.py init) the updater files, data/ and
# the NAND are SHA-256 checked first and taken from the recorded paths.
#
# Env: A6K_NAND=<file>       NAND base file (default state/a6000.nand); read only,
#                            not hash-checked
#      A6K_PERSIST=<file>    keep guest NAND writes in <file>, copied from the
#                            base when it does not exist yet
#      A6K_SHELL=<port>      root shell on UART2 as a TCP server on
#                            127.0.0.1:<port> (-M console=ttyAM1 via
#                            shell-socket=; only the console= token of the
#                            stock cmdline changes, the UI still runs).
#                            Use tools/a6kshell.py --port <port>.
#                            docs/reference/RE_GUEST_SHELL.md
#      A6K_DEBUG_CONSOLE=1   same console (ttyAM1, UART2) on this terminal
#                            (stdio) instead of a socket
#      A6K_DEBUG_CONSOLE=ttyAM0  old UART0 console (console=ttyAM0): early-boot
#                            logs only, av-cam owns UART0 and the UI does not
#                            start
#      A6K_DIRECT=1          old direct boot: host kernel + stock initrd
#      A6K_SD=<img>          SD card image (FAT32, e.g. state/sd.img)
#      A6K_QEMU=<binary>     qemu-system-arm to run (default: the build made by
#                            ./setup-qemu.sh, $A6400_QEMU_DIR/build)
#      A6K_LOADER=<cpio>     stage-1 loader initramfs (default provision/loader.cpio)
#      A6K_HANDOFF=<file>    bootloader hand-off page (default data/handoff_80021000.bin)
#      A6K_FW=<dir>          unpacked official 3.21 updater (default ./firmware)
#      A6K_ICOUNT=<opts>     -icount options (default shift=0,sleep=on; "off"
#                            = guest time follows the host clock, which makes
#                            boot fail at random on a loaded host)
E="$(cd "$(dirname "$0")" && pwd)"
# unpacked official updater: $A6K_FW, else ./firmware (git-ignored)
M=sony-a6000 RAM=1G DATA="$E/data"
FW="${A6K_FW:-$E/firmware}"
INPUTS="$E/inputs.local.json"
CHECKED=0
if [ -f "$INPUTS" ]; then
  get() { python3 -I "$E/tools/inputs.py" get "$1"; }
  FW="${A6K_FW:-$(get fw_dir)}" DATA="$(get data_dir)"
  CHECKED=1
fi
F="$FW/unpacked/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked_unpacked"
NAND="${A6K_NAND:-$E/state/a6000.nand}"
if [ "$CHECKED" = 1 ] && [ -z "${A6K_NAND:-}" ]; then
  NAND="$(python3 -I "$E/tools/inputs.py" get nand 2>/dev/null || echo "$NAND")"
fi
QEMU="${A6K_QEMU:-${A6400_QEMU_DIR:-$HOME/sony-a6400-qemu-11.1.3}/build/qemu-system-arm}"
LOADER="${A6K_LOADER:-$E/provision/loader.cpio}"
# stock console=ttynull by default (A6K_STOCK_CONSOLE=1 is the old spelling).
# UART0 is set to null explicitly for the UART2 shell so that QEMU's default
# serial backend never attaches to av-cam's monitor port.
SER=()
# Guest time from the instruction count (1 insn = 1 ns), not the host clock.
# Without it a slow or busy host stretches every guest instruction in virtual
# time, firmware timeouts fire and the boot stops on a black panel at random
# (1 of 6 runs booted with 6 emulators in parallel; 16 of 16 with -icount).
IC=()
[ "${A6K_ICOUNT:-}" = off ] || IC=(-icount "${A6K_ICOUNT:-shift=0,sleep=on}")
DBG="${A6K_DEBUG_CONSOLE:-0}"; [ "${A6K_STOCK_CONSOLE:-0}" = 1 ] && DBG=0
if [ -n "${A6K_SHELL:-}" ]; then
  [ "$DBG" = 0 ] || { echo "run.sh: A6K_SHELL and A6K_DEBUG_CONSOLE are exclusive" >&2; exit 1; }
  M="$M,shell-socket=127.0.0.1:$A6K_SHELL"
  SER=(-serial null)
elif [ "$DBG" = 1 ]; then
  M="$M,console=ttyAM1"
  SER=(-serial null -serial null -serial stdio)
elif [ "$DBG" = ttyAM0 ]; then
  M="$M,console=ttyAM0"
fi
if [ "${A6K_DIRECT:-0}" = 1 ]; then
  exec "$QEMU" -M "$M" -m $RAM ${IC[@]+"${IC[@]}"} \
    -kernel "$F/vmlinux.bin" -initrd "$F/initrd.img" ${SER[@]+"${SER[@]}"} "$@"
fi
[ -f "$NAND" ] || { echo "run.sh: $NAND missing — run ./provision.sh <your NAND dump> first" >&2; exit 1; }
if [ "$CHECKED" = 1 ] && [ -z "${A6K_NAND:-}" ] && [ -z "${A6K_FW:-}" ]; then
  python3 -I "$E/tools/inputs.py" check --nand || exit 1
else
  echo "run.sh: inputs not hash-checked (no inputs.local.json, or A6K_NAND/A6K_FW set)" >&2
fi
SNAP=on
if [ -n "${A6K_PERSIST:-}" ]; then
  if [ ! -e "$A6K_PERSIST" ]; then
    cp --reflink=auto -- "$NAND" "$A6K_PERSIST.tmp"
    chmod u+w "$A6K_PERSIST.tmp"
    mv -- "$A6K_PERSIST.tmp" "$A6K_PERSIST"
  fi
  [ ! "$A6K_PERSIST" -ef "$NAND" ] || { echo "run.sh: A6K_PERSIST must not be the base NAND" >&2; exit 1; }
  NAND="$A6K_PERSIST" SNAP=off
fi
# provision/a6kcopy: static ARM helper inside both initramfs images, built on demand
# (gcc-arm-none-eabi; -Ttext-segment: some toolchains link at VA 0, which the
# kernel refuses)
if [ ! -x "$E/provision/a6kcopy" ] || [ "$E/provision/a6kcopy.c" -nt "$E/provision/a6kcopy" ]; then
  arm-none-eabi-gcc -march=armv7-a -marm -O2 -nostdlib -static -ffreestanding \
    -Wl,-Ttext-segment=0x10000 -o "$E/provision/a6kcopy" "$E/provision/a6kcopy.c" || exit 1
fi
# (re)build the loader from this model's firmware when missing or older than its sources
if [ -z "${A6K_LOADER:-}" ] && { [ ! -f "$LOADER" ] || [ "$E/provision/loader.sh" -nt "$LOADER" ] ||
     [ "$E/provision/mkinitramfs.py" -nt "$LOADER" ] || [ "$E/provision/a6kcopy" -nt "$LOADER" ]; }; then
  A6K_FW="$FW" python3 -I "$E/provision/mkinitramfs.py" "$LOADER" loader.sh >/dev/null || exit 1
fi
exec "$QEMU" \
  -M "$M,submicro=on,submicro-frames=${A6K_SMFRAMES:-$DATA/submicro_frames.bin},nand=$NAND,nand-snapshot=$SNAP,loader=$LOADER,preload=${A6K_HANDOFF:-$DATA/handoff_80021000.bin}@0x80021000" \
  -m $RAM ${IC[@]+"${IC[@]}"} -kernel "$F/vmlinux.bin" \
  ${A6K_SD:+-drive if=sd,file=$A6K_SD,format=raw} ${SER[@]+"${SER[@]}"} "$@"
