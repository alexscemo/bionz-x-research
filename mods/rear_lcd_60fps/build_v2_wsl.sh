#!/bin/bash
# Build RearLcd60fps as a relocatable SMOD v2 mod for Mod Loader v2.
# Usage: build_v2_wsl.sh OUTPUT_DIR [--target ilce_6400|ilce_6400a]
set -euo pipefail
cd "$(dirname "$0")/../.."
if ! command -v arm-linux-gnueabi-gcc >/dev/null 2>&1 && [[ -d "$HOME/a6400-rea-tools/arm-gcc-15.2.0-prepush/bin" ]]; then
  export PATH="$HOME/a6400-rea-tools/arm-gcc-15.2.0-prepush/bin:$PATH"
fi
OUT=$1
shift
TARGET="${MOD_TARGET:-ilce_6400}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT"
DIR=mods/rear_lcd_60fps
common=(-mthumb -mcpu=cortex-a5 -mno-unaligned-access -mfpu=vfpv3-d16 -mfloat-abi=softfp -Os
        -ffreestanding -fno-builtin -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer
        -fno-tree-vectorize -fno-unwind-tables -fno-asynchronous-unwind-tables
        -Wall -Wextra -Werror -fpie -fvisibility=hidden)
if [[ -d /usr/arm-linux-gnueabi/bin ]]; then
  common+=(-B/usr/arm-linux-gnueabi/bin)
fi

target_defs=()
if [[ "$TARGET" == "ilce_6400a" ]]; then
  target_defs=(-DTARGET_ILCE6400A=1)
  HOOK_TARGET=0x0405c204
  LAYOUT=tools/native_camera/mlv2/reference_layout_ilce6400a.txt
elif [[ "$TARGET" == "ilce_6400" ]]; then
  HOOK_TARGET=0x0401f204
  LAYOUT=tools/native_camera/mlv2/reference_layout_ilce6400.txt
else
  echo "Unsupported target: $TARGET (supported: ilce_6400, ilce_6400a)" >&2
  exit 2
fi

arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" -c "$DIR/rear_lcd_60fps.c" -o "$OUT/rear_lcd_60fps.o"
arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" -c "$DIR/rear_lcd_60fps_trampoline.S" -o "$OUT/rear_lcd_60fps_tramp.o"

for o in "$OUT/rear_lcd_60fps.o" "$OUT/rear_lcd_60fps_tramp.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "$o has absolute or GOT relocations; it would not be position-independent" >&2
    exit 1
  fi
done

arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
  "$OUT/rear_lcd_60fps.o" "$OUT/rear_lcd_60fps_tramp.o" -o "$OUT/rear_lcd_60fps.elf"

if arm-linux-gnueabi-objdump -d "$OUT/rear_lcd_60fps.elf" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*)>"; then
  echo "unexpected runtime helper in rear_lcd_60fps.elf" >&2
  exit 1
fi

arm-linux-gnueabi-objdump -d "$OUT/rear_lcd_60fps.elf" > "$OUT/rear_lcd_60fps.dis"
arm-linux-gnueabi-nm -n "$OUT/rear_lcd_60fps.elf" > "$OUT/rear_lcd_60fps.sym"

python3 -m tools.research.pack_mod_v2 \
  --elf "$OUT/rear_lcd_60fps.elf" \
  --name "Rear LCD 60fps" \
  --on rlcd_on \
  --off rlcd_off \
  --bss 32 \
  --hook "${HOOK_TARGET}:80b584b002af07f108034ff0ff32:rlcd_hook_backup_read" \
  --reloc-layout "$LAYOUT" \
  --out "$OUT/RearLcd60fps.mod" | tee "$OUT/pack.json"
