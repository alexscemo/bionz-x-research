#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# From voxivoid/a6000-qemu (commit 62cf3bc) provision.sh, which adapts Oein/a5000-emulator (GPL-2.0, base commit 6ad36bd) to the ILCE-6000.
# docs/reference/ paths in comments are files in voxivoid/a6000-qemu.
# One-time factory-style NAND programming: blank NAND -> Sony MUMIN format
# (nand.ko boot_mode=1) -> your camera's NAND dump written through /dev/nflasha.
# Result: state/a6000.nand (or the path given as the 2nd argument)
#
# Two input sources make the emulated NAND:
#   $FW  = Sony's official updater (fwtool-unpacked). COMMON to every A6000 --
#          system, /usr, rootfs, kernel, Android partitions. Not device-specific.
#   $IMG = a full NAND dump of YOUR OWN camera (first argument).
#          Supplies only what the updater does NOT contain, i.e. the DEVICE-SPECIFIC
#          regions: the bootloader area, the updater core (nflasha1, the recovery
#          lifeline), and the settings partition (nflasha2: Backup.bin + factory
#          calibration). These are why boot/recovery/Backup behave exactly like the
#          real camera in the emulator.
#
# Safety / privacy:
#   - The camera dump is READ-ONLY. Never run dd (or anything) with of=/dev/nflasha*
#     on the real camera -- writing the NAND can brick it.
#   - $IMG contains your camera's serial, Wi-Fi settings and calibration. It is
#     never part of this repository and MUST NOT be committed or shared.
#   - See README.md for how to produce $FW (step 1-2) and $IMG (step 3).
set -e
E="$(cd "$(dirname "$0")" && pwd)"
# unpacked official updater: $A6K_FW, else ./firmware (git-ignored)
FW="${A6K_FW:-$E/firmware}"
QEMU="${A6K_QEMU:-${A6400_QEMU_DIR:-$HOME/sony-a6400-qemu-11.1.3}/build/qemu-system-arm}"
F="$FW/unpacked/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked_unpacked"
# NAND dump of your own camera (kept outside Git)
IMG="${1:?usage: provision.sh <NAND dump of your own A6000> [output]}"; OUT="${2:-$E/state/a6000.nand}"
mkdir -p "$(dirname "$OUT")"; rm -f "$OUT.tmp"
# provision/a6kcopy: static ARM helper inside both initramfs images, built on demand
# (gcc-arm-none-eabi; -Ttext-segment: some toolchains link at VA 0, which the
# kernel refuses)
if [ ! -x "$E/provision/a6kcopy" ] || [ "$E/provision/a6kcopy.c" -nt "$E/provision/a6kcopy" ]; then
  arm-none-eabi-gcc -march=armv7-a -marm -O2 -nostdlib -static -ffreestanding \
    -Wl,-Ttext-segment=0x10000 -o "$E/provision/a6kcopy" "$E/provision/a6kcopy.c" || exit 1
fi
python3 -I "$E/provision/mkinitramfs.py" "$E/provision/prov.cpio" init.sh
LOG="$E/state/provision.log"
"$QEMU" -M "sony-a6000,debug-console=on,nand=$OUT.tmp,provision=$IMG" \
  -m 1G -kernel "$F/vmlinux.bin" -initrd "$E/provision/prov.cpio" \
  -display none -serial null -serial null -serial file:"$LOG" -monitor none &  # debug-console = ttyAM1 = UART2 (3rd -serial)
QP=$!
for i in $(seq 1 240); do
  sleep 5
  grep -q "a6k-provision: OK" "$LOG" && break
  grep -qE "a6k-provision: .*(missing|FAIL)" "$LOG" && { kill $QP; echo "provision failed, see $LOG"; exit 1; }
done
kill $QP; wait $QP 2>/dev/null || true
grep -q "a6k-provision: OK" "$LOG" || { echo "provision timed out, see $LOG"; exit 1; }
mv "$OUT.tmp" "$OUT"; echo "wrote $OUT"
