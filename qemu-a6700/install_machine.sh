#!/bin/sh
# Copy the sony-a6700 machine into a QEMU 10.0.0 source tree and register it (idempotent).
# usage: install_machine.sh /path/to/qemu-10.0.0
set -e
SRC="$1"; HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$SRC/hw/arm/virt.c" ] || { echo "not a QEMU tree: $SRC" >&2; exit 2; }
cp "$HERE/hw/arm/sony-a6700.c" "$SRC/hw/arm/sony-a6700.c"
python3 - "$SRC/hw/arm/Kconfig" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path).read()
text = re.sub(r"\nconfig SONY_A6700\n(?:    .*\n)+", "\n", text)
text = text.rstrip("\n") + """

config SONY_A6700
    bool
    default y
    depends on TCG && AARCH64
    select ARM_GIC
    select PL011
    select PL330
    select SDHCI
    select SD
"""
open(path, "w").write(text)
PY
grep -q "sony-a6700.c" "$SRC/hw/arm/meson.build" || \
  sed -i "/arm_ss.add(when: 'CONFIG_ARM_VIRT'/a arm_ss.add(when: ['CONFIG_SONY_A6700', 'TARGET_AARCH64'], if_true: files('sony-a6700.c'))" "$SRC/hw/arm/meson.build"
# Settable EXT_CSD revision in QEMU's eMMC model (see patch_sd_emmc.py).
python3 "$HERE/patch_sd_emmc.py" "$SRC"
python3 "$HERE/patch_pl330_log.py" "$SRC"
python3 "$HERE/patch_sdhci_adma_log.py" "$SRC"
echo installed
