# E0756 - OpenGateA1 `libmpr.so` hook sites match the updater build, not the camera; guarded test kit

- **Date:** 2026-11-28
- **Scope:** 100 % offline. Byte comparison of the OpenGateA1 hook and screen sites against the
  captured camera libraries and the 2.00 updater libraries, plus a new guarded test kit run against
  a simulated camera. No camera, USB, debugger, WSL or `/dev/mem` operation was performed.
- **Predecessors:** `E0752` (live `A12`–`A19`, OpenGateA1 SMOD, A5 4896 × 3376 path), `E0753`/`E0754`
  (offline open-gate analysis; `E0754` names the updater `libmpr.so` as its input).

## Inputs (read-only)

| File | Bytes | SHA-256 |
| --- | --- | --- |
| `artifacts/dumps/a6400-live-lib/libmpr.so` (captured from the camera) | 12,665,872 | `bb31351cbd546d88e8ff9bef006db75bd1dabfe3d2f67f359f04155cff9ab8af` |
| `firmware/extracted_a6400/.../nflasha15_unpacked/lib/libmpr.so` (2.00 updater) | 12,740,352 | `40a86215a4a03d30be0f12a492739898c509a5eb221586e774478c545b09bb12` |
| `artifacts/hardware/live-libs/viewUnified2.so` (captured from the camera) | 11,530,508 | `fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d` |
| `firmware/extracted_a6400/.../nflasha15_unpacked/lib/viewUnified2.so` (2.00 updater) | 11,530,552 | `1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2` |
| `mods/opengate_a1/build_v2_wsl.sh`, `mods/opengate_a1/opengate_a1.c` at `1d26e3c` | | |
| `tools/native_camera/mlv2/reference_layout_ilce6400.txt` | | |

## Observations

1. `build_v2_wsl.sh` declares two hooks: `0x079b472c:f8b50446d0f8d85000af0dbb` (`tkhd`) and
   `0x079b4a74:f0b593b00c4600af06460b68` (`stsd`). With the reference layout's `libmpr.so` base
   `0x07144000`, these are file offsets `0x87072C` and `0x870A74`.
2. The **updater** `libmpr.so` holds exactly those bytes at `0x87072C` / `0x870A74`.
3. The **captured** `libmpr.so` holds `0080cdf80480edf6…` / `878142e145f20303…` there. Each 12-byte
   stock pattern occurs once in it, at `0x8612AC` and `0x8615F4`: `0xF480` lower.
4. The reference layout maps `libmpr.so` as `0xBEE000` r-x plus an rw segment at file offset
   `0xBED000` of size `0x27000`, i.e. a `0xC14000`-byte image, consistent with the captured file
   (`0xC14410` bytes) and not with the updater file (`0xC26800` bytes).
5. All 16 `viewUnified2.so` words the mod swaps (`VU_CELL_OFFS[i] + 0x0C` = `0x404E0000`,
   `+ 0x1C` = `0x40768000`) hold their stock values in the **captured** library and do not in the
   updater library (`+0x0C` reads `0x403A0000` there).

## Inference (not observed on a camera)

On a camera whose `libmpr.so` is the captured build, the two MP4-header hooks would meet bytes
other than the declared stock bytes, so Mod Loader v2's stock-byte guard would refuse them (or the
hooks would land in the wrong function if a guard were bypassed). The screen and av-cam.bin parts are
unaffected by this. The mod needs its hook addresses re-derived from the captured `libmpr.so`.

## Guarded test kit

`tools/kits/opengate_a6400.py` with launchers and README in `mods/opengate_a1/kit/`:

- `check` (read-only): SHA-256 of `/usr/lib/viewUnified2.so` and `/usr/lib/libmpr.so` against the
  captured pins (`mods/opengate_a1/kit/identity.json`), the 16 screen words and both hook sites read
  from `im.elf` memory (debugger `OP_MEM`), Mod Loader v2 presence, the mod's log state, and
  `/proc/uptime` monotonicity. Verdict `READY (stock preflight)` / `NOT READY`.
- `apply`: refuses without a READY check from the same power-on less than 30 minutes old, re-runs
  the check, asks for a typed `YES`, and stages one file (`/tmp_bt/mods/OpenGateA1.mod`, read back).
- `verify` / `putback` / `export`: read the mod's log line (`ON|OFF av=w…,a…,x… vu=… ok=…`) and the
  live screen words; `putback` removes the staged file only after a clean OFF and only if the kit
  staged it; `export` never reads `/setting` or Backup files.
- No `/dev/mem` access: `tools/devmem_policy.py` classifies DRAM as FORBIDDEN, so the av-cam.bin
  table identity is inferred from the library pins and the mod's own guard and log.

Site tables are parsed from the mod sources (337 av-cam sites, 8 cells × 2 words, 2 hooks;
expected `ok = 353`).

## Verification

- `python -m unittest tests.test_opengate_a6400_kit`: 24 tests OK, including
  `test_captured_camera_libmpr_does_not_hold_the_hook_bytes` (skipped on clones without the
  local capture).
- `python -m tools.kits.opengate_a6400 selftest`: `check` READY, `apply` STAGED, `verify` APPLIED,
  `putback` STOCK with the staged file removed, and `check` NOT READY on a simulated camera whose
  hook bytes sit `0xF480` lower.

## Limits and next boundary

- Nothing here ran on a camera. The kit's first physical `check` needs an explicit go-ahead.
- The on-camera `sha256sum` applet is not confirmed on BusyBox 1.13.4; the kit falls back to a
  bounded read-back of the library (about 12 MB over the debugger).
- Next: re-derive the two hook addresses from the captured `libmpr.so`, rebuild OpenGateA1, then run
  `check` on the camera.
