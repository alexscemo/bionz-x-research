#!/bin/sh
# Make privacy-scrubbed COPIES of a captured ILCE-6700 partition set for local emulator use.
# usage: scrub_dump.sh <dir with nflashaN.bin> <output dir>     (run as root in WSL/Linux)
# Captured images are only mounted read-only. Each FAT partition is rebuilt as a NEW image
# that carries the generic files and none of the free-space remnants; device-unique or
# unreadable data is replaced by zeros of the same size. This is data hygiene for the
# owner's own files; it is NOT a licence to redistribute the firmware.
set -e
IN="$1"; OUT="$2"
[ -d "$IN" ] && [ -n "$OUT" ] || { echo "usage: $0 captures-dir output-dir" >&2; exit 2; }
[ ! -e "$OUT" ] || { echo "refusing to overwrite $OUT" >&2; exit 2; }
mkdir -p "$OUT"
SRC=$(mktemp -d); DST=$(mktemp -d)
cleanup() { umount "$SRC" 2>/dev/null || true; umount "$DST" 2>/dev/null || true; rmdir "$SRC" "$DST" 2>/dev/null || true; }
trap cleanup EXIT

fat_bits() { # prints 12, 16 or 32 from the BPB type label
  if dd if="$1" bs=1 skip=82 count=5 2>/dev/null | grep -q FAT32; then echo 32
  elif dd if="$1" bs=1 skip=54 count=5 2>/dev/null | grep -q FAT12; then echo 12
  else echo 16; fi
}
# rebuild <partition> <label> <exclude-glob...>   (files matching an exclude are skipped)
rebuild() {
  p="$1"; label="$2"; shift 2
  img="$IN/$p.bin"; [ -f "$img" ] || { echo "skip $p (missing)"; return; }
  size=$(stat -c %s "$img"); bits=$(fat_bits "$img")
  mount -o loop,ro "$img" "$SRC"
  truncate -s "$size" "$OUT/$p.bin"
  mkfs.fat -F "$bits" -n "$label" "$OUT/$p.bin" >/dev/null
  mount -o loop "$OUT/$p.bin" "$DST"
  ( cd "$SRC" && find . -mindepth 1 \( -type d -o -type f \) | sort ) | while read -r f; do
    skip=0
    for ex in "$@"; do case "$f" in $ex) skip=1;; esac; done
    [ "$skip" = 1 ] && continue
    if [ -d "$SRC/$f" ]; then mkdir -p "$DST/$f"; else mkdir -p "$DST/$(dirname "$f")"; cp "$SRC/$f" "$DST/$f"; fi
  done
  umount "$DST"; umount "$SRC"
  echo "rebuilt $p"
}
zero_image() { [ -f "$IN/$1.bin" ] && { truncate -s "$(stat -c %s "$IN/$1.bin")" "$OUT/$1.bin" && echo "zeroed $1"; }; }

rebuild nflasha1  UPDATER
rebuild nflasha2  SETTING './Backup.*' './facerec0.bin' './.hasp' './.etools' './.etools/*'
rebuild nflasha6  JIRITSU './*'
rebuild nflasha10 WORK    './run' './run/*' './resolv.conf' './kikilog.dat' './log' './log/*' './mtab' './jiritsu' './jiritsu/*'
rebuild nflasha11 LOG     './AccessLog.*'
rebuild nflasha12 CERT    './ccpf' './ccpf/*'
rebuild nflasha13 UPDWORK './history' './history/*'
rebuild nflasha18 LENS
rebuild nflasha22 AIDICT
zero_image nflasha4   # MPR flash backup: unreadable and probably device-unique
# files replaced by zero-filled stand-ins of the same size where the layout matters
if [ -f "$IN/nflasha11.bin" ]; then
  mount -o loop "$OUT/nflasha11.bin" "$DST"; head -c 2097220 /dev/zero > "$DST/AccessLog.dat"; head -c 2097220 /dev/zero > "$DST/AccessLog.bak"; umount "$DST"
fi
if [ -f "$IN/nflasha2.bin" ]; then
  mount -o loop "$OUT/nflasha2.bin" "$DST"; head -c 88 /dev/zero > "$DST/.hasp"; head -c 57888 /dev/zero > "$DST/facerec0.bin"; umount "$DST"
fi
echo done
