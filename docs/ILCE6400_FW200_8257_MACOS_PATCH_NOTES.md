# Sony ILCE-6400 FW 2.00 (`8.2.5.7`) on macOS — Setup Post-Mortem & Patch Notes

## 1. Root Cause Summary: Two Different "Firmware 2.00" Builds

Although Sony displays both builds as **Firmware 2.00** on the camera, `ILCE-6400` shipped with two distinct internal Sony CE Linux library builds (see [2026-11-19-firmware-qualification.md](evidence/mods/2026-11-19-firmware-qualification.md)):

| Property | Captured FW 2.00 (`8.2.6.0`) | Retail FW 2.00 (`8.2.5.7`) | Impact |
| :--- | :--- | :--- | :--- |
| `libtestcmd.so` EOF | `+0x275c` | `+0x2790` (`10,128` B, May 27 2019) | Overlapped `mlv2-layout-build-4` shim cave at `+0x2760` (`MLV2_RESULT=-12`) |
| `viewUnified2.so` tab rect table | `+0x802a04` / `+0x802a84` | `+0x802a64` / `+0x802ae4` (`+0x60` shift) | Required FW 2.00 guard fallback (`RECT_TBL*_SLOT6_FW200`) |
| `libObj.so` touch hooks | `+0x5f1d48` / `+0x5f1e54` | `+0x5f1dc0` / `+0x5f1ecc` (`+0x78` shift) | `MenuTouchFull` prologue check failed closed (`ERR`) |
| `libObj.so` `.bss` (`pViewManager` / `g_pEvtMgr`) | `+0x14257ec` / GOT `+0x1371984` | `+0x1424eac` (`-0x940`) / `+0x1424d70` | Required exact `8.2.5.7` `.bss` offsets + single-deref for `g_pEvtMgr` |
| `libObj.so` alcor icon hook | `+0x79d4b6` | `+0x79d526` (`+0x70` shift) | `ModsTabLook` hook & `core.c` icon detection needed `8.2.5.7` offset |
| `CautionConfig.so` `CmnViewSettingNode` vtable | `+0xab93e8` | `+0xab9400` (`+0x18` shift) | `MenuTouchFull` vtable identity check would fail without update |

In addition, the live `im.elf` process (PID `152`) loaded libraries at **non-zero ASLR bases**:

- `libObj.so`: `0x05a53000` (bias `-0x2c5000` vs. reference `0x05d18000`)
- `libtestcmd.so`: `0x07e18000` (bias `+0x27b8000` vs. reference `0x05660000`)
- `viewUnified2.so`: `0x15585000` (bias `-0x61000` / `0xfff9f000` vs. reference `0x155e6000`)
- `CautionConfig.so`: `0x160fe000` (bias `-0x61000` vs. reference `0x1615f000`)

Every mod therefore had to be built as a **relocatable `SMOD v2`** module with an `RLC1` relocation table, rather than a fixed-address `SMOD v1` binary.

---

## 2. Why Default Setup Failed (Step by Step)

### 2.1 `mlv2_guard install failed: STATE hooks=stock v1_build=-1 shim=other MLV2_RESULT=-12`

- **Prepare Tools** downloaded the pinned GitHub release `mlv2-layout-build-4` into `.cache/mlv2-layout-build-4/build.json`, which places the shim at `0x05662760` (`libtestcmd.so + 0x2760`).
- On retail `8.2.5.7`, `libtestcmd.so` contains real library bytes up to `+0x2790`. When `mlv2_guard` froze `im.elf` and inspected `0x05662760 + testcmd_bias`, the first 48 bytes were non-zero, so it reported `shim=other`, refused to write (`MLV2_RESULT=-12 changed=0`), and resumed all 177 threads cleanly.
- Although the repository sources ([shim.ld](../tools/native_camera/mlv2/shim.ld), [mlv2_guard.c](../tools/native_camera/mlv2/mlv2_guard.c)) already moved `SHIM_ADDR` to `0x05662800` and added `RECT_TBL*_SLOT6_FW200`, `resolve_mlv2_build()` in [tools_bundle.py](../src/a6400/mod_manager/tools_bundle.py) fell back to `.cache/mlv2-layout-build-4` because no compiled `>= 0x05662790` build existed locally on macOS.

### 2.2 `Camera Offline / GenericUsbException:` on macOS

- On macOS, `libusb` requires `sudo` to detach/claim the Sony Mass Storage USB interface. PMCA's `libusb.py` catches `usb.core.USBError` (such as `Access denied` or a stalled bulk endpoint after a process restart) and re-raises it as an empty `GenericUsbException()`.
- Launching the Mod Manager web app with `sudo` and power-cycling/replugging the camera restored Mass Storage and native USB debugger access.

### 2.3 `Menu Touch Full` Displayed `ERR` in `MENU -> Mods`

- Two issues prevented it from enabling:
  1. The shipped [MenuTouchFull.mod](../mods/menu_touch_full/MenuTouchFull.mod) on disk is an **SMOD v1** fixed-address binary (`has_reloc=False`), which cannot relocate to the camera's runtime `libObj.so` / `viewUnified2.so` / `CautionConfig.so` addresses.
  2. Even rebuilt as `SMOD v2`, the default `8.2.6.0` offsets pointed 120 bytes (`0x78`) too early in `libObj.so`, so `core.bin` checked the 12-byte hook prologues, saw a mismatch, and safely failed closed with `ERR`.
- By contrast, `Arrivederci` worked immediately because it modifies physical LIRO ISP tables without hooking `libObj.so` code.

### 2.4 `ModsTabLook` (`MODS` Tab Icon) Unavailable & Shifted

- `ModsTabLook.mod` was not prebuilt on disk.
- On `8.2.5.7`, its alcor lookup hook in `libObj.so` shifted by **`+0x70`** (`0x79d4b6` to `0x79d526`), not `+0x78`.
- `core.c`'s `mtl_present()` only checked the `8.2.6.0` hook address (`0x064b54b6`) to decide whether to use the custom `MODS` tab icon resource IDs or fall back to the 6th tab's star icon.

---

## 3. What We Patched & Built

### 3.1 macOS Cross-Compilation Setup (Replacing WSL `arm-linux-gnueabi-gcc`)

Installed via Homebrew (`brew install zig`, which also provides `llvm@22` and `lld@22`) plus `unicorn==2.1.4` and `capstone==5.0.9` for offline ARM verification:

- **Freestanding Thumb-2 payloads (`shim.elf`, `core.elf`, `.mod` ELFs):** compiled with `llvm@22` `clang` + `ld.lld`:
  - Flags: `--target=thumbv7-linux-gnueabi -mthumb -mcpu=cortex-a5 -mfpu=vfpv4-d16 -mfloat-abi=softfp -mno-movt -Os -ffreestanding -fno-builtin -nostdlib -fno-unwind-tables -fno-asynchronous-unwind-tables -fno-stack-protector -fomit-frame-pointer`
  - **Key macOS/Clang flags that mattered:**
    - `-mfpu=vfpv4-d16`: prevents NEON code generation (the CXD90045 Cortex-A5 has VFPv4-D16 without NEON).
    - `-mno-movt`: keeps 32-bit firmware addresses in aligned literal pools instead of `movw`/`movt` pairs so [package.py](../tools/native_camera/mlv2/package.py) and [pack_mod_v2.py](../tools/research/pack_mod_v2.py) can relocate every 32-bit word cleanly.
    - Linking `.o` files directly via `ld.lld -T <script>` avoids Clang's default `-pie` flag on non-PIC `shim.elf`.
- **Static camera guard (`mlv2_guard_arm`):** compiled with `zig cc -target arm-linux-musleabi -mcpu=cortex_a5 -mfloat-abi=softfp -static -O2 -Wall -Wextra -Werror` and stripped with `llvm-strip -s` down to **65 KiB** (well within the camera's 4 MiB `/tmp_bt` tmpfs, with zero glibc minimum-kernel checks).

### 3.2 Mod Loader v2 (`0x05662800` + Dual `ModsTabLook` Detection)

- **Code change:** [core.c](../tools/native_camera/mlv2/core.c) and [mods_tab_look_layout.h](../tools/native_camera/mods_tab_look_layout.h) — added `MTL_HOOK_ADDR_FW200` (`0x064b5526`) so `mtl_present()` detects an active `ModsTabLook` hook on both `8.2.6.0` and `8.2.5.7`.
- **Output directory:** `artifacts/tests/mlv2-fw200-build-1/` (automatically preferred by `resolve_mlv2_build()` over `.cache/mlv2-layout-build-4` because `0x05662800 >= FW200_SAFE_MIN_SHIM_ADDR` `0x05662790`):
  - `mlv2.req`: `1,448` B, `shim_addr = 0x05662800`, 23 ASLR relocations, SHA-256 `285a2df4dd4cba0108c643deb58cb7073077d877300f63462aa2867dc71427d5`
  - `core.bin`: `14,980` B (`CTX_VERSION=4`), SHA-256 `fabbf4204469…`
  - `mlv2_guard_arm`: `65` KiB stripped static ARMv7 musl binary, SHA-256 `7fbeb06a1169fc8c1c145c00354a88e51dc647c08628ecacecc342232798a386`
- **Verification:** Passed all 18 scenarios in [test_mlv2_unicorn.py](../tools/native_camera/mlv2/test_mlv2_unicorn.py) and installed cleanly on the physical `ILCE-6400`.

### 3.3 `MenuTouchFull` (`MTF2`) Ported to `8.2.5.7`

Using read-only camera probes (`artifacts/tests/mtf-8257-probe.json` and `artifacts/tests/mtf-8257-verify2.json`), every hook and symbol address was verified directly against the camera's `.dynsym`, PLT/GOT, vtables, and disassembled prologues:

| Symbol | Library | Verified `8.2.5.7` File Offset | Reference VA (`ref_base + off`) | Delta vs. `8.2.6.0` |
| :--- | :--- | :--- | :--- | :--- |
| `HOOK_REL` (`dispatchMouseRelease`) | `libObj.so` | `0x005f1dc0` | `0x06309dc0` | `+0x78` |
| `HOOK_PRS` (`dispatchMousePress`) | `libObj.so` | `0x005f1ecc` | `0x06309ecc` | `+0x78` |
| `MT_SYS_GET_INST` (`WidgetSystem::sys_getInstance`) | `libObj.so` | `0x005f0da9` | `0x06308da9` | `+0x78` |
| `MT_GET_POS` (`getMousePosInFramebuffer`) | `libObj.so` | `0x005f1091` | `0x06309091` | `+0x78` |
| `MT_WGT_LOOKUP` (`WidgetLayouter::getWidget`) | `libObj.so` | `0x005ca857` | `0x062e2857` | `+0x78` |
| `MT_WGT_IS_HIT` (`Widget::isHit`) | `libObj.so` | `0x005ed0f9` | `0x063050f9` | `+0x78` |
| `MT_WGT_INH_MODE` (`getInheritedWidgetMode`) | `libObj.so` | `0x0060688d` | `0x0631e88d` | `+0x78` |
| `MT_VIEW_MGR_SLOT` (`pViewManager`) | `libObj.so` | `0x01424eac` | `0x0713ceac` | `-0x940` |
| `MT_EVMGR_SLOT` (`g_pEvtMgr`, single-deref) | `libObj.so` | `0x01424d70` | `0x0713cd70` | direct `.bss` |
| `MT_OP_NEW` (`_Znwj` PLT to GOT `0x136a7a0`) | `libObj.so` | `0x000fd974` | `0x05e15974` | `0` |
| `MT_EV_CTOR` (`Event::Event(ulong, uchar, uchar)`) | `libObj.so` | `0x00840fed` | `0x06558fed` | direct Thumb |
| `MT_EVMGR_PUSH` (`EventManager::push(Event*, bool)`) | `libObj.so` | `0x00842599` | `0x0655a599` | direct Thumb |
| `MT_VT_SETTING_NODE` (`_ZTV18CmnViewSettingNode + 8`) | `CautionConfig.so` | `0x00ab9400` | `0x16c18400` | `+0x18` |
| `MT_NODE_SEL_INDEX` / `IDX_CHILD` / `SET_SEL_IDX` | `CautionConfig.so` | `0x7c6c17` / `0x7c6bc7` / `0x7c70db` | unchanged | `0` |
| All 2 vtables + 11 functions | `viewUnified2.so` | unchanged | unchanged | `0` |

- **Code changes:** Added `#elif defined(TARGET_ILCE6400_8257)` in [menu_touch_full.c](../mods/menu_touch_full/menu_touch_full.c) (plus single-deref `rd32(MT_EVMGR_SLOT, 0u)` in `push_async_event()`) and [menu_touch_full_trampoline.S](../mods/menu_touch_full/menu_touch_full_trampoline.S).
- **Output:** `artifacts/tests/mtf-v2-build-2/MenuTouchFull.mod` (`3,452` B, `SMOD v2` with 36 `RLC1` relocated words, SHA-256 `05014fb0337aa3fbaaa3e7cbac0e67142c0ac546b996391d68a2f5175990875e`), automatically selected ahead of the v1 file by [catalog.py](../src/a6400/mod_manager/catalog.py)'s `artifact_globs`.

### 3.4 `ModsTabLook` Ported to `8.2.5.7`

- Read-only camera probe (`artifacts/tests/mtl-8257-probe.json`) located the exact prologue `f8b500af0d460446fff7e2ff` at `libObj.so + 0x79d526` (`+0x70` from `8.2.6.0`).
- **Code changes:** Added `#elif defined(TARGET_ILCE6400_8257)` return targets (`0x064b54f7` and `0x064b5533`) in [mods_tab_look_trampoline.S](../tools/native_camera/mods_tab_look_trampoline.S).
- **Clang/LLD mapping-symbol fix:** Added an explicit `$d:` label right before `mtl_icon_rle:` in [mods_tab_look_trampoline.S](../tools/native_camera/mods_tab_look_trampoline.S). Without it, `ld.lld` merged the 8-byte literal pool (`0x150..0x157`) with `mtl_icon_rle` (`0x158+`), causing `pack_mod_v2.py --data-symbol mtl_icon_rle` to skip both literal pool words (`"reloc_words": []`). With `$d:`, both `libObj.so` trampoline addresses are properly emitted in `RLC1`.
- **Output:** `artifacts/tests/mtl-v2-build-2/ModsTabLook.mod` (`1,992` B, `--auto-on` + `RLC1`, SHA-256 `0637c11ec6cc58003a2f05cf8181fdb5c728d09569c187b514ba84aefa5b768f`).

---

## 4. Operational Notes & Files Changed

- **Tracked source files modified (all gated behind `#ifdef TARGET_ILCE6400_8257` or backward-compatible fallback, leaving `8.2.6.0` and `ILCE-6400A` intact):**
  - [mods/menu_touch_full/menu_touch_full.c](../mods/menu_touch_full/menu_touch_full.c)
  - [mods/menu_touch_full/menu_touch_full_trampoline.S](../mods/menu_touch_full/menu_touch_full_trampoline.S)
  - [tools/native_camera/mods_tab_look_layout.h](../tools/native_camera/mods_tab_look_layout.h)
  - [tools/native_camera/mods_tab_look_trampoline.S](../tools/native_camera/mods_tab_look_trampoline.S)
  - [tools/native_camera/mlv2/core.c](../tools/native_camera/mlv2/core.c)
- **Local gitignored artifacts picked up automatically by Mod Manager:**
  - `artifacts/tests/mlv2-fw200-build-1/` (Mod Loader v2 `0x05662800`)
  - `artifacts/tests/mtf-v2-build-2/MenuTouchFull.mod` (`8.2.5.7` relocatable `SMOD v2`)
  - `artifacts/tests/mtl-v2-build-2/ModsTabLook.mod` (`8.2.5.7` relocatable `SMOD v2`)
- **Safety reminder:** Always toggle **Arrivederci OFF** in `MENU -> Mods` before powering off the camera, since Sony persists the active color matrix to `Backup.bin` at shutdown.
