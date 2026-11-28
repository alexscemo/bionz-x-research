#!/bin/bash
# Build SilentFlashUnlock as an SMOD v2 (relocatable) mod for Mod Loader v2.
# Usage: build_v2_wsl.sh NEW_OUTPUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$1
mkdir -p "$OUT"
DIR=mods/silent_flash_unlock
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -fpie -fvisibility=hidden)

arm-linux-gnueabi-gcc "${common[@]}" -c "$DIR/silent_flash_unlock.c" -o "$OUT/sflash.o"
arm-linux-gnueabi-gcc "${common[@]}" -c "$DIR/silent_flash_unlock_trampoline.S" -o "$OUT/sflash_tramp.o"

for o in "$OUT/sflash.o" "$OUT/sflash_tramp.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "$o has absolute or GOT relocations; it would not be position-independent" >&2
    exit 1
  fi
done

arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
  "$OUT/sflash.o" "$OUT/sflash_tramp.o" -o "$OUT/sflash.elf"

if arm-linux-gnueabi-objdump -d "$OUT/sflash.elf" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*)>"; then
  echo "unexpected runtime helper in sflash.elf" >&2
  exit 1
fi

arm-linux-gnueabi-objdump -d "$OUT/sflash.elf" > "$OUT/sflash.dis"
arm-linux-gnueabi-nm -n "$OUT/sflash.elf" > "$OUT/sflash.sym"

python3 -m tools.research.pack_mod_v2 \
  --elf "$OUT/sflash.elf" \
  --name "Silent Flash" \
  --on sflash_on \
  --off sflash_off \
  --bss 6980 \
  --auto-on \
  --hook "0x069888a0:2de9f04104464cf2b60000af:sflash_hook" \
  --hook "0x0411532c:2de9f0410d46194982b08046:sflash_inh3_hook" \
  --hook "0x041153a4:f0b50d46174983b0064600af:sflash_inh2_hook" \
  --hook "0x06729d28:2de9f843044600af16468846:sflash_pset_hook" \
  --hook "0x063f8d1c:f0b500af15461c467e691eb9:sflash_mget_hook" \
  --reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400.txt \
  --out "$OUT/SilentFlashUnlock.mod" | tee "$OUT/pack.json"
