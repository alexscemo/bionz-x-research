#!/bin/bash
# Build OpenGate32.mod (SMOD v2 + SMNU nested submenu) and OpenGateSensor.mod.
# Usage: build_wsl.sh OUTPUT_DIR [--target ilce_6400|ilce_6400a]
set -euo pipefail
cd "$(dirname "$0")/../.."

if ! command -v arm-linux-gnueabi-gcc >/dev/null 2>&1; then
  for cand in "$HOME/a6400-rea-tools/arm-gcc-15.2.0-prepush/bin" "$HOME/.local/bin"; do
    if [[ -x "$cand/arm-linux-gnueabi-gcc" ]]; then
      export PATH="$cand:$PATH"
      break
    fi
  done
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

target_defs=()
if [[ "$TARGET" == "ilce_6400a" ]]; then
  target_defs=(-DTARGET_ILCE6400A=1)
elif [[ "$TARGET" != "ilce_6400" ]]; then
  echo "Unsupported target: $TARGET (expected ilce_6400 or ilce_6400a)" >&2
  exit 2
fi

mkdir -p "$OUT"
MD=mods/open_gate
common=(-mthumb -mcpu=cortex-a5 -mno-unaligned-access -mfpu=vfpv3-d16 -mfloat-abi=softfp -Os
        -ffreestanding -fno-builtin -nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer
        -fno-tree-vectorize -fno-unwind-tables -fno-asynchronous-unwind-tables
        -Wall -Wextra -Werror -fpie -fvisibility=hidden)
if [[ -d /usr/arm-linux-gnueabi/bin ]]; then
  common+=(-B/usr/arm-linux-gnueabi/bin)
fi

# 1. Full OpenGate32.mod (with SMNU nested submenu)
arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" \
  -c "$MD/open_gate.c" -o "$OUT/open_gate_32.o"
if arm-linux-gnueabi-readelf -rW "$OUT/open_gate_32.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
  echo "open_gate_32.o has absolute or GOT relocations; not position-independent" >&2
  exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
  "$OUT/open_gate_32.o" -o "$OUT/open_gate_32.elf"
if arm-linux-gnueabi-objdump -d "$OUT/open_gate_32.elf" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*)>"; then
  echo "unexpected runtime helper in open_gate_32.elf" >&2
  exit 1
fi
arm-linux-gnueabi-objdump -d "$OUT/open_gate_32.elf" > "$OUT/open_gate_32.dis"
arm-linux-gnueabi-nm -n "$OUT/open_gate_32.elf" > "$OUT/open_gate_32.sym"

python3 -m tools.research.pack_mod_v2 \
  --elf "$OUT/open_gate_32.elf" \
  --name "Open Gate 3:2" \
  --on og_on --off og_off \
  --bss 64 \
  --submenu-bss-off 4 \
  --submenu-cfg og_cfg \
  --submenu-item "Sensor Gate:0:5.1K True 4K|6K Reg01 Full|Full-FOV 2K" \
  --submenu-item "Canvas Format:0:Native 3:2 4K|Native 3:2 HD|Native 3:2 Both|Anamorphic" \
  --submenu-item "M10 Timing:0:24p (23.98fps)|30p (29.97fps)|Stock Auto" \
  --submenu-item "Movie Scope:0:4K + HD|4K Only|HD Only" \
  --out "$OUT/OpenGate32.mod" | tee "$OUT/pack_open_gate_32.json"

# 2. Sensor-only OpenGateSensor.mod (Sensor Gate + ExlHelper + Timing, stock encoder canvas)
arm-linux-gnueabi-gcc "${common[@]}" "${target_defs[@]}" -DOG_SENSOR_ONLY=1 \
  -c "$MD/open_gate.c" -o "$OUT/open_gate_sensor.o"
if arm-linux-gnueabi-readelf -rW "$OUT/open_gate_sensor.o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
  echo "open_gate_sensor.o has absolute or GOT relocations; not position-independent" >&2
  exit 1
fi
arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
  "$OUT/open_gate_sensor.o" -o "$OUT/open_gate_sensor.elf"
if arm-linux-gnueabi-objdump -d "$OUT/open_gate_sensor.elf" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*)>"; then
  echo "unexpected runtime helper in open_gate_sensor.elf" >&2
  exit 1
fi
arm-linux-gnueabi-objdump -d "$OUT/open_gate_sensor.elf" > "$OUT/open_gate_sensor.dis"
arm-linux-gnueabi-nm -n "$OUT/open_gate_sensor.elf" > "$OUT/open_gate_sensor.sym"

python3 -m tools.research.pack_mod_v2 \
  --elf "$OUT/open_gate_sensor.elf" \
  --name "Open Gate Sensor" \
  --on og_on --off og_off \
  --bss 64 \
  --submenu-bss-off 4 \
  --submenu-cfg og_cfg \
  --submenu-item "Sensor Gate:0:5.1K True 4K|6K Reg01 Full|Full-FOV 2K" \
  --submenu-item "M10 Timing:0:24p (23.98fps)|30p (29.97fps)|Stock Auto" \
  --submenu-item "Movie Scope:0:4K + HD|4K Only|HD Only" \
  --out "$OUT/OpenGateSensor.mod" | tee "$OUT/pack_open_gate_sensor.json"
