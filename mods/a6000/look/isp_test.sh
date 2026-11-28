#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 voxivoid
# From voxivoid/a6000-qemu (commit 62cf3bc) mods/look/isp_test.sh.
# isp_test.sh — interactive write-test harness for all verified + candidate ISP parameters.
# EXPERIMENTAL: this harness has no recorded run on hardware; the candidate tests are guesses.
#
# Tests one parameter at a time: saves the original, writes a test value, waits for the user
# to confirm the effect on live view, then reverts.  Read-only until the user says "go".
# RAM-only; battery pull = stock in every case.
#
# Usage:
#   CAM=<camera-ip> ./isp_test.sh list                   show all tests
#   CAM=<camera-ip> ./isp_test.sh run <test>              run one test
#   CAM=<camera-ip> ./isp_test.sh run all                 run every test in sequence
#   CAM=<camera-ip> ./isp_test.sh snap <name>             full CC snapshot (like isp_snap.sh)
#   CAM=<camera-ip> ./isp_test.sh read <pa_hex> <nwords>  raw MMIO read
#
# Env:  CAM=<camera ip>  (OpenMemories-Tweak: Wifi + Telnet + ADB)
# Deps: adb, python3, gammamod (built, same dir)
#
# Every write is to volatile ISP SRAM via /dev/mem mmap.  Nothing touches flash.
# Power cycle = stock.  The script saves the original words before writing and reverts
# immediately on user request.
#
# Setter-freeze protocol: for parameters the camera continuously rewrites, the script
# patches the setter function's first halfword to `bx lr` (return immediately) before
# writing, and restores it on revert.  The setter addresses are for FW 3.21 only;
# the script verifies the original bytes before patching and refuses on mismatch.
set -euo pipefail

CAM=${CAM:?set CAM=<camera ip>}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
SH="python3 $ROOT/emulators/a6000/tools/a6kshell.py --host $CAM --port 23"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
GM=/android/gammamod

# ---- helpers ----------------------------------------------------------------

cam() { $SH run "$1" 2>&1; }
connect() { adb connect "$CAM:5555" >/dev/null 2>&1 || true; }
push_gm() {
  connect
  adb push "$HERE/gammamod" /gammamod >/dev/null 2>&1
  cam "chmod 755 $GM" >/dev/null
}

# write N u32 words (decimal, space-separated) into a little-endian binary file
mk_bin() {
  local out="$1"; shift
  python3 -I -c "
import struct, sys
words = [int(w) for w in sys.argv[1:]]
sys.stdout.buffer.write(struct.pack('<%dI' % len(words), *words))
" "$@" > "$out"
}

# read N u32 words from ISP MMIO, print as decimal (one per line)
isp_read() {
  local pa=$1 nw=$2
  cam "$GM -F hwrd $pa $nw" | awk -F': ' '{print $2}'
}

# read N u32 words, save to local file (raw LE u32)
isp_save() {
  local pa=$1 nw=$2 out=$3
  cam "$GM -F hwdump $pa $nw /android/_save.bin" >/dev/null
  adb pull /android/_save.bin "$out" >/dev/null 2>&1
}

# write a local binary file to ISP MMIO
isp_write() {
  local pa=$1 file=$2
  local fname; fname=$(basename "$file")
  adb push "$file" "/android/$fname" >/dev/null 2>&1
  cam "$GM -F hwwr $pa /android/$fname"
}

# read 4 bytes at a DDR PA (av-cam code), return as hex
ddr_read_hex() {
  cam "$GM read $1 4 /android/_r4.bin" >/dev/null
  adb pull /android/_r4.bin "$T/_r4.bin" >/dev/null 2>&1
  python3 -I -c "import sys; sys.stdout.write(open(sys.argv[1],'rb').read().hex())" "$T/_r4.bin"
}

# patch a setter to bx lr (freeze), or restore its original bytes
setter_freeze() {
  local pa=$1 orig_first4_hex=$2
  local live; live=$(ddr_read_hex "$pa")
  local orig8="${orig_first4_hex}"
  if [ "${live:0:8}" = "${orig8:0:8}" ]; then
    # original → patch first halfword to 7047 (bx lr), keep the rest
    mk_bin "$T/_patch4.bin" $(python3 -I -c "
import struct
orig = bytes.fromhex('$orig_first4_hex')
patched = b'\\x70\\x47' + orig[2:4]
print(struct.unpack('<I', patched)[0])
")
    adb push "$T/_patch4.bin" /android/_patch4.bin >/dev/null 2>&1
    cam "$GM -F write $pa /android/_patch4.bin" >/dev/null
    echo "frozen"
  elif [ "${live:0:4}" = "7047" ]; then
    echo "already frozen"
  else
    echo "UNKNOWN (live=$live, expected=$orig8) — refusing"
    return 1
  fi
}

setter_thaw() {
  local pa=$1 orig_first4_hex=$2
  mk_bin "$T/_orig4.bin" $(python3 -I -c "
import struct; print(struct.unpack('<I', bytes.fromhex('$orig_first4_hex'))[0])
")
  adb push "$T/_orig4.bin" /android/_orig4.bin >/dev/null 2>&1
  cam "$GM -F write $pa /android/_orig4.bin" >/dev/null
  echo "thawed"
}

# prompt user, return 0 for yes, 1 for no
ask() {
  local ans
  read -rp "$1 [y/n] " ans
  [[ "$ans" =~ ^[yY] ]]
}

# ---- test definitions -------------------------------------------------------
# Each test: name, description, ISP PA, word count, setter PA (0 = no freeze),
# setter original first-4-byte hex, test values generator, verify hint

declare -A TEST_DESC TEST_PA TEST_NW TEST_SETTER_PA TEST_SETTER_SIG TEST_VALUES TEST_HINT

register_test() {
  local name=$1
  TEST_DESC[$name]="$2"
  TEST_PA[$name]="$3"
  TEST_NW[$name]="$4"
  TEST_SETTER_PA[$name]="$5"
  TEST_SETTER_SIG[$name]="$6"
  TEST_VALUES[$name]="$7"
  TEST_HINT[$name]="$8"
}

# --- 3D LUT bank #3 (the style bank) ---
# 60 words at 0xF3217300.  Identity = 256 per word.
# Test: write 0 (kill chroma contribution from LUT) and 512 (double)
register_test "3dlut-zero" \
  "3D LUT bank #3: all zeros (kill LUT chroma)" \
  "0xF3217300" 60 \
  "0x83b67988" "70b50c46" \
  "$(printf '0 %.0s' $(seq 1 60))" \
  "If the 3D LUT affects colour: visible colour shift. If neutral anyway: no change (expected on stock Standard)."

register_test "3dlut-boost" \
  "3D LUT bank #3: all 512 (2× chroma boost via LUT)" \
  "0xF3217300" 60 \
  "0x83b67988" "70b50c46" \
  "$(printf '512 %.0s' $(seq 1 60))" \
  "If the 3D LUT is active: extreme colour boost. If neutral/bypassed: no change."

register_test "3dlut-invert" \
  "3D LUT bank #3: negative values (invert chroma deltas)" \
  "0xF3217300" 60 \
  "0x83b67988" "70b50c46" \
  "$(python3 -I -c "print(' '.join(['65280'] * 60))")" \
  "u16 -256 (0xFF00) = negative identity. Should invert chroma mapping if LUT is active."

# --- 3D LUT enable bracket ---
register_test "3dlut-enable" \
  "3D LUT write-enable bracket (0x15D40): toggle on, write bank, toggle off" \
  "0xF3217300" 60 \
  "0x83b67988" "70b50c46" \
  "BRACKET" \
  "Same as 3dlut-boost but uses the write-enable bracket protocol."

# --- Saturation gains (verified, but re-test for completeness) ---
register_test "sat-zero" \
  "Saturation: all 4 chroma gains to 0 (expect B&W)" \
  "0xF32156AC" 4 \
  "0x83b69312" "70b50d46" \
  "0 0 0 0" \
  "Live view should go fully monochrome (confirmed previously)."

register_test "sat-max" \
  "Saturation: all 4 chroma gains to 255 (extreme colour)" \
  "0xF32156AC" 4 \
  "0x83b69312" "70b50d46" \
  "255 255 255 255" \
  "Live view should be extremely oversaturated."

# --- Sharpness ---
register_test "sharp-zero" \
  "Sharpness: edge enhancement to 0 (soft)" \
  "0xF3215C14" 2 \
  "0x83b68c88" "70b50d46" \
  "0 0" \
  "Live view should look visibly softer / less detailed."

register_test "sharp-max" \
  "Sharpness: edge enhancement to 15 (max)" \
  "0xF3215C14" 2 \
  "0x83b68c88" "70b50d46" \
  "15 15" \
  "Live view should look over-sharpened / crunchy edges."

# --- WB gains (R and B channel) ---
register_test "wb-red" \
  "WB: R gain to 1900 (strong red cast)" \
  "0xF3211900" 1 \
  "0x83b665b6" "70b50c46" \
  "1900" \
  "Live view should have a strong red/warm cast."

register_test "wb-blue" \
  "WB: B gain to 1900 (strong blue cast)" \
  "0xF3211908" 1 \
  "0x83b665b6" "70b50c46" \
  "1900" \
  "Live view should have a strong blue/cool cast."

# --- Colour matrix: R<->B swap (dramatic, verified) ---
register_test "matrix-swap" \
  "Colour matrix: swap R and B rows (red sky, blue skin)" \
  "0xF321550C" 9 \
  "0x83b675e0" "70b50c46" \
  "MATRIX_SWAP" \
  "Red and blue swap in the entire image. Unmistakable."

# --- Mono flag candidate (0xF3200D10) ---
register_test "mono-flag" \
  "Mono candidate: set 0xF3200D10 to 1 (possible chroma kill)" \
  "0xF3200D10" 1 \
  "0" "" \
  "1" \
  "If this is the chroma-kill flag: image goes B&W. If not: no visible change."

# ---- test runner ------------------------------------------------------------

run_test() {
  local name=$1
  echo ""
  echo "========================================================================"
  echo "TEST: $name"
  echo "  ${TEST_DESC[$name]}"
  echo "  Hint: ${TEST_HINT[$name]}"
  echo "========================================================================"

  local pa=${TEST_PA[$name]}
  local nw=${TEST_NW[$name]}
  local setter_pa=${TEST_SETTER_PA[$name]}
  local setter_sig=${TEST_SETTER_SIG[$name]}
  local values=${TEST_VALUES[$name]}

  # 1. Read and save original
  echo ""
  echo "[1/5] Reading original $nw words at $pa ..."
  isp_save "$pa" "$nw" "$T/orig_${name}.bin"
  echo "  Saved original. Current values:"
  isp_read "$pa" "$nw" | head -6
  [ "$nw" -gt 6 ] && echo "  ... ($nw words total)"

  # For WB tests, read both R and B so we can restore both
  if [[ "$name" == wb-* ]]; then
    echo "  (also saving the other WB gain for safety)"
    if [ "$pa" = "0xF3211900" ]; then
      isp_save "0xF3211908" 1 "$T/orig_${name}_other.bin"
    else
      isp_save "0xF3211900" 1 "$T/orig_${name}_other.bin"
    fi
  fi

  if ! ask "[2/5] Proceed with write?"; then
    echo "  Skipped."
    return 0
  fi

  # 2. Freeze setter if needed
  if [ -n "$setter_pa" ] && [ "$setter_pa" != "0" ]; then
    echo "[3/5] Freezing setter at $setter_pa ..."
    local fst; fst=$(setter_freeze "$setter_pa" "$setter_sig") || { echo "  ABORT: $fst"; return 1; }
    echo "  Setter: $fst"
  else
    echo "[3/5] No setter to freeze for this test."
  fi

  # 3. Write test values
  echo "[4/5] Writing test values ..."

  if [ "$values" = "BRACKET" ]; then
    # 3D LUT with write-enable bracket protocol
    mk_bin "$T/test_${name}.bin" $(printf '512 %.0s' $(seq 1 60))
    mk_bin "$T/enable.bin" 1
    mk_bin "$T/disable.bin" 0
    echo "  Setting 3D LUT write-enable (0xF3215D40 = 1) ..."
    isp_write "0xF3215D40" "$T/enable.bin"
    echo "  Writing 60 words of 512 to bank #3 ..."
    isp_write "$pa" "$T/test_${name}.bin"
    echo "  Clearing write-enable (0xF3215D40 = 0) ..."
    isp_write "0xF3215D40" "$T/disable.bin"
  elif [ "$values" = "MATRIX_SWAP" ]; then
    # Read live matrix, swap rows 0 and 2
    echo "  Reading live matrix ..."
    local mvals; mvals=$(isp_read "0xF321550C" 9)
    local m; IFS=$'\n' read -r -d '' -a m <<< "$mvals" || true
    # Row 0 = m[0..2], Row 1 = m[3..5], Row 2 = m[6..8]
    # Swap: new row 0 = old row 2, new row 2 = old row 0
    echo "  Original: ${m[0]} ${m[1]} ${m[2]} / ${m[3]} ${m[4]} ${m[5]} / ${m[6]} ${m[7]} ${m[8]}"
    mk_bin "$T/test_${name}.bin" "${m[6]}" "${m[7]}" "${m[8]}" "${m[3]}" "${m[4]}" "${m[5]}" "${m[0]}" "${m[1]}" "${m[2]}"
    echo "  Swapped:  ${m[6]} ${m[7]} ${m[8]} / ${m[3]} ${m[4]} ${m[5]} / ${m[0]} ${m[1]} ${m[2]}"
    isp_write "$pa" "$T/test_${name}.bin"
  else
    mk_bin "$T/test_${name}.bin" $values
    isp_write "$pa" "$T/test_${name}.bin"
  fi

  echo ""
  echo "  >>> LOOK AT THE CAMERA LIVE VIEW NOW <<<"
  echo "  >>> Describe what you see, then press Enter to revert <<<"
  echo ""
  read -rp "  Your observation: " observation
  echo "  Recorded: $observation"

  # 4. Revert
  echo "[5/5] Reverting ..."
  isp_write "$pa" "$T/orig_${name}.bin"

  # Thaw setter
  if [ -n "$setter_pa" ] && [ "$setter_pa" != "0" ]; then
    setter_thaw "$setter_pa" "$setter_sig"
  fi

  echo "  Reverted. Force a style-change on camera to let it reprogram fully."
  echo ""
  echo "  RESULT: $name"
  echo "  Observation: $observation"
  echo "  ---"
  echo "$name|$observation" >> "$T/results.txt"
}

# ---- commands ---------------------------------------------------------------

cmd_list() {
  echo "Available ISP write tests:"
  echo ""
  printf "  %-20s %s\n" "NAME" "DESCRIPTION"
  printf "  %-20s %s\n" "----" "-----------"
  for name in 3dlut-zero 3dlut-boost 3dlut-invert 3dlut-enable \
              sat-zero sat-max sharp-zero sharp-max \
              wb-red wb-blue matrix-swap mono-flag; do
    printf "  %-20s %s\n" "$name" "${TEST_DESC[$name]}"
  done
}

cmd_run() {
  local target=${1:-}
  [ -z "$target" ] && { echo "usage: isp_test.sh run <test|all>"; exit 1; }

  push_gm
  echo "Connected to $CAM, gammamod pushed."
  echo ""

  if [ "$target" = "all" ]; then
    for name in 3dlut-zero 3dlut-boost 3dlut-invert 3dlut-enable \
                sat-zero sat-max sharp-zero sharp-max \
                wb-red wb-blue matrix-swap mono-flag; do
      run_test "$name"
      if ! ask "Continue to next test?"; then
        echo "Stopped."
        break
      fi
    done
    echo ""
    echo "======== ALL RESULTS ========"
    [ -f "$T/results.txt" ] && cat "$T/results.txt"
  else
    if [ -z "${TEST_DESC[$target]+x}" ]; then
      echo "Unknown test: $target"; echo "Run 'isp_test.sh list' for available tests."; exit 1
    fi
    run_test "$target"
  fi
}

cmd_snap() {
  local name=${1:?snapshot name}
  push_gm
  echo "Taking CC snapshot '$name' ..."
  local cmd="rm -f /android/snap.txt;"
  for p in 0x0 0x1000 0x3000 0x5000 0x11000 0x12000 0x13000 0x14000 0x15000 0x16000 0x17000; do
    cmd="$cmd $GM -F hwrd $(printf '0x%X' $((0xF3200000 + p))) 1024 >> /android/snap.txt;"
  done
  cam "$cmd busybox wc -l /android/snap.txt" | tail -1
  mkdir -p "$HERE/snaps"
  adb pull /android/snap.txt "$HERE/snaps/$name.txt" >/dev/null 2>&1
  echo "Saved mods/a6000/look/snaps/$name.txt"
}

cmd_read() {
  local pa=${1:?PA hex} nw=${2:?nwords}
  push_gm
  isp_read "$pa" "$nw"
}

# ---- main -------------------------------------------------------------------

case "${1:-}" in
  list)           cmd_list ;;
  run)            cmd_run "${2:-}" ;;
  snap)           cmd_snap "${2:-}" ;;
  read)           cmd_read "${2:-}" "${3:-}" ;;
  *)
    echo "isp_test.sh — interactive ISP parameter write-test harness"
    echo ""
    echo "Usage:"
    echo "  CAM=$CAM $0 list                   show all tests"
    echo "  CAM=$CAM $0 run <test|all>          run one or all tests"
    echo "  CAM=$CAM $0 snap <name>             full CC snapshot"
    echo "  CAM=$CAM $0 read <pa_hex> <nwords>  raw MMIO read"
    echo ""
    echo "RAM-only. Power cycle = stock. See header for details."
    exit 1
    ;;
esac
