#!/bin/sh
# Boot the ILCE-6700 kernel on the sony-a6700 research machine with the camera's own boot contract.
# usage: run_first_boot.sh <qemu-build-dir> <kernel-dir> [seconds] [emmc-image]
#   <kernel-dir> holds vmlinux.bin, initrd.img (nflasha3 of the 2.00 update), cxd90057.dtb and
#   kemco.txt (both from /setting). Load addresses come from the updater's boot/loadaddr.txt:
#   kernel 0x400080000, initrd 0x408000000, command line 0x400001000, device tree 0x400002000.
#   UART0 goes to uart0.log, unimplemented-device accesses to unimp.log (current directory).
set -e
B="$1"; K="$2"; T="${3:-30}"; E="$4"
[ -x "$B/qemu-system-aarch64" ] && [ -f "$K/vmlinux.bin" ] && [ -f "$K/kemco.txt" ] || { echo "usage: $0 qemu-build kernel-dir [seconds]" >&2; exit 2; }
# the camera logs to ttynull; route the console to UART0 for the emulator
sed 's/console=ttynull0/console=ttyAMA0,460800 earlycon=pl011,0xf1000000/' "$K/kemco.txt" > kemco.emu.txt
# optional local eMMC image (4 GiB, see build_emmc_image.py)
if [ -n "$E" ]; then
  set -- -drive "if=sd,format=raw,file=$E"
else
  set --
fi
timeout "$T" "$B/qemu-system-aarch64" -M sony-a6700 -m 4G -display none -no-reboot \
  -serial file:uart0.log -monitor none \
  -kernel "$K/vmlinux.bin" -dtb "$K/cxd90057.dtb" \
  -device loader,file=kemco.emu.txt,addr=0x400001000,force-raw=on \
  -device loader,file="$K/initrd.img",addr=0x408000000,force-raw=on \
  "$@" \
  -d unimp,guest_errors -D unimp.log || true
echo "UART bytes: $(wc -c < uart0.log)"
