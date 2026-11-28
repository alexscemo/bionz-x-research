# ILCE-6400A (FW 1.01) Runtime Mods (`SMOD v2`)

This directory contains the target-specific **`SMOD v2`** runtime mod packages built and verified for **Sony `ILCE-6400A` firmware `1.01`** (`CXD90045`, Sony CE Linux `8.2.7.6`).

Unlike `ILCE-6400` `2.00` (where the official updater package and captured camera partitions differ in `viewUnified2.so` / `libObj.so`), every target user-space library and `av-cam.bin` in captured `ILCE-6400A` `1.01` (`emmc.img` SHA-256 `ed231fc020c4e43a1bc7ef7d62738049a988622e7cdf7271bb3543b3e4e7e22f`) is **bit-for-bit identical** to the official `Update_ILCE6400AV101.exe` package (`firmware/extracted_a6400a_v101/`). All `.mod` packages here are linked with `--reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400a.txt` (72 libraries) so `Mod Loader v2` (`core.bin`) relocates virtual library references automatically across ASLR / load-order shifts.

See **[E0679](../../docs/evidence/0679-ilce-6400a-mod-loader-v2-and-mods-port.md)** for full instruction-level address mappings, Unicorn test results, and live QEMU `ILCE-6400A` `1.01` `rear_panel.py` transcripts.

---

## Shipped `ILCE-6400A` 1.01 `.mod` Packages

| File | Menu Name | Size (B) | SHA-256 | Summary & Verification |
| :--- | :--- | ---: | :--- | :--- |
| `Arrivederci.mod` | `Arrivederci` | `129,444` | `31beab47dd10e4ec43c0419e558ec64734430dd5e40d71bc31bd6e29585f5694` | ARRI LogC3 Classic tone curve (`PA 0x03E560C4`), fitted Q10 color matrix (`PA 0x0444FDC8`), `CC_TUNING` (`PA 0x0444ED96`), 3D LUT (`PA 0x044B191B`..`+0x143C`), and SPICA/DCV `/usr/sabin/*` 4K tap-table bind-mounts (`sabin=m`/`u`). Excluded from default `MLV2.TAR` SD auto-boot packaging; **turn OFF before powering off camera** (`COLOR_PA` persists in `Backup.bin`). |
| `MenuTouchFull.mod` | `Menu Touch Full` | `3,032` | `b4e30572bf3282f860b9c21564b054e31302a98a3f4c811d54b96cd3beb9f35c` | Full-menu touch (`MTF2`): tab bar taps, page-dot taps, horizontal swipe paging, row select/open, popup scroll/apply/outside-tap close across `viewUnified2.so`, `libsyscommon.so`, and `libevsel.so`. |
| `ModsTabLook.mod` | `Mods Tab Look` | `2,000` | `e8d526bcb54e2561cfe5d489365808f69a4fbdb0078186859e04ff432afaf9eb` | Auto-ON (`SMOD_FLAG_AUTO_ON`) custom `MODS` tab icon + teal tab gradient (`viewUnified2 + 0x5ADBB4`). |
| `OpenGate32.mod` | `Open Gate 3:2` | `9,788` | `bfc03026fec4f1cac951017d5f909721bdf81c8f0b31f788b244fc18ee7414dd` | 3-tier 3:2 Open Gate Movie mod (`SMOD_V2_FLAG_SUBMENU` `SMNU`: `5.1K True 4K` `5064x3376`/`4140x2760` default, `6K Reg01 Full` `6000x4000` + `reg 0x0001=0x40` + `S-Ele` crash guard, or `Full-FOV 2K` `Mode 10` `1996x1332`; `3240x2160`, `1620x1080`, `Both`, or `Anamorphic`) synchronized across `ExlHelper`, `DMM`, `VPI`, `ZIMA DVENC`, and runtime-located `libmpr.so` (`MprLibCore::EncOpen` `.text` + `.rodata`; **[E0741](../../docs/evidence/0741-open-gate-32-mode10-smod-v2.md)**). Mutually exclusive with `OpenGateSensor` and `OpenGate_5400_Test`. |
| `OpenGateSensor.mod` | `Open Gate Sensor` | `6,672` | `4237297ff3aa32463ef2c0d042ae66551755a92e45290625b29db4bb93807dd9` | Companion 3-tier `SMNU` sensor-only 3:2 Open Gate stage (`5.1K True 4K` `5064x3376`/`4140x2760`, `6K Reg01 Full` `6000x4000`, or `Full-FOV 2K` `1996x1332`) keeping stock `3840x2160` / `1920x1080` `DMM`/`VPI`/`DVENC`/`libmpr.so` (`1.33x` anamorphic squeeze; **[E0741](../../docs/evidence/0741-open-gate-32-mode10-smod-v2.md)**). |
| `OpenGate_5400_Test.mod` | `OpenGate_5400_Test` | `1,388` | `11524dbf4abd8b9aa1ad375450dc76e6ef9ac7860335f84adef5aa44f46d515f` | Diagnostic `41/41` LIRO RAM swap (`5400x3600` sensor + `3240x2160` DMM/VPI/VENC) with `libmpr.so` skipped (`m=skip`). |
| `PhotonBin6MP.mod` | `PhotonBin 6MP` | `6,188` | `ef5af925becb321b29da55440d1c8e6ace650b099690cda1b3193747a219017f` | Low-light pre-demosaic `4x4->2x2` Bayer / `2x2` Mono `.ARW` photon convergence (`3008x2008` / `3000x2000` active, 16-bit Uncomp / 14-bit Uncomp / 14-bit `cRAW`) + `IMX284` `Mconv` HCG & `14-bit 1H2H` overrides via 2-page nested submenu (`SMOD_V2_FLAG_SUBMENU`, **[E0680](../../docs/evidence/0680-photonbin-6mp-and-mlv2-nested-submenus.md)**). |
| `RCRAW_Enable_Test.mod` | `RCRAW_Enable_Test` | `1,184` | `43953573166bd8be5410122626615b1d3ffb9c58a23cae5ec83320ee103ce487` | Diagnostic toggle for `RCRAW_EN_PA` (`0x043F2B04`) with readback of `RDRAW_EN_PA` (`0x043F2EC0`), `D1534_PA` (`0x043F2828`), and `W1`/`W2` descriptors (`0x04422FE8`/`0x0442304C`). |
| `RCRAW_W2_Probe.mod` | `RCRAW_W2_Probe` | `780` | `213983f17e7e08d5ad0e7e7c97fc452f10333c260b0ff8db4314459df0083a0d` | Strictly read-only LIRO RAM probe logging `W1`, `W2`, `W2B`, and `RAW_EXT_PA` (`0x044295BC`) to `/tmp_bt/mods/rcraw_w2_probe.log`. |
| `RearLcd60fps.mod` | `Rear LCD 60fps` | `768` | `4412826ec817b7745c31be0fffa0716bbf242443bf60e3980486781727f85719` | Unlocks `59.94 fps` (`60 fps`) Live-View on the rear LCD while keeping `Display Quality = High` on EVF (`libbackup.so + 0x1204` RAM override of `BK4 0x01071489` + live `NoAdapter(1)` apply on `ON`/`OFF`; **[E0740](../../docs/evidence/0740-rear-lcd-60fps-high-quality-mod.md)**). |
| `UsbStreamingTest.mod` | `USB Streaming Test` | `2,784` | `431482047d39bb9cf23a7c042b20d93cf4189e8046344c34a48634c39ecad289` | QEMU-only `--action` item driving original `ILCE-6400A` `UsbControl` (`libUsbControl.so` `94,220` B) through `STATE 1 -> 5 -> 7` and auto-closing the menu via `EventManager::push`. Refuses execution outside QEMU (`console=ttyAM0,`). |

---

## Rebuild From Source (WSL)

```bash
# 1. Build Mod Loader v2 (shim @ 0x05d76800, core.bin with nested submenu support, mlv2.req, mlv2_guard_arm)
bash tools/native_camera/mlv2/build_wsl.sh artifacts/tests/mlv2-ilce6400a-build-3 --target ilce_6400a

# 2. Build all 11 ILCE-6400A SMOD v2 mods
bash mods/mods_tab_look/build_mtl_v2_wsl.sh artifacts/mods/ilce_6400a/mods_tab_look --target ilce_6400a
bash mods/menu_touch_full/build_mtf_v2_wsl.sh artifacts/mods/ilce_6400a/menu_touch_full --target ilce_6400a
bash mods/rear_lcd_60fps/build_v2_wsl.sh artifacts/mods/ilce_6400a/rear_lcd_60fps --target ilce_6400a
bash mods/arri_isp/arrivederci/build_v3_wsl.sh artifacts/mods/ilce_6400a/arrivederci --target ilce_6400a
bash mods/open_gate/build_wsl.sh artifacts/mods/ilce_6400a/open_gate --target ilce_6400a
bash mods/opengate_5400_test/build_v2_wsl.sh artifacts/mods/ilce_6400a/opengate_5400_test --target ilce_6400a
bash mods/photon_bin_6mp/build_wsl.sh artifacts/mods/ilce_6400a/photon_bin_6mp --target ilce_6400a
bash mods/rcraw_enable_test/build_v2_wsl.sh artifacts/mods/ilce_6400a/rcraw_enable_test --target ilce_6400a
bash mods/rcraw_w2_probe/build_v2_wsl.sh artifacts/mods/ilce_6400a/rcraw_w2_probe --target ilce_6400a
bash mods/usb_streaming_test/build_wsl.sh artifacts/mods/ilce_6400a/usb_streaming_test --target ilce_6400a

# 3. Package SD card bundle (MLV2.TAR + mlv2_sd_boot.sh; excludes UsbStreamingTest.mod and Arrivederci.mod by default)
mkdir -p artifacts/tests/mlv2-ilce6400a-build-2/card artifacts/tests/mlv2-ilce6400a-build-2/companion
rm -f artifacts/tests/mlv2-ilce6400a-build-2/card/MLV2.TAR artifacts/tests/mlv2-ilce6400a-build-2/companion/mlv2_sd_boot.sh
python3 -m tools.research.package_mlv2_sd --build artifacts/tests/mlv2-ilce6400a-build-2 --target ilce_6400a
```

---

## Run in QEMU (`ILCE-6400A` 1.01)

```bash
TARGET=ilce_6400a \
INPUTS="$HOME/a6400-inputs/a6400a-v101-captured" \
LOADER=artifacts/tests/mlv2-ilce6400a-build-2 \
ULOGIO="" EVSEL_LATE_US=500 \
SUBMICRO_FRAMES="$HOME/a6400-inputs/submicro-a6400a/wbi-powered-384.bin" \
SUBMICRO_FRAMES_SHA=5effe8d44b670f71fd7b537e44276718b57f072e48a10be45e621cb73c207695 \
bash emulators/qemu-a6400/tools/start_rear_panel.sh
```

Or select **`ILCE-6400A (FW 1.01)`** in the **[BIONZ X Mod Manager GUI](../../docs/MOD_MANAGER_GUI.md)** target selector to inspect/stage `ILCE-6400A` mods to SD card or deploy over an active native debugger session.

---

## Standalone SD-Card Auto-Boot on Physical `ILCE-6400A` (`MLV2.TAR`)

Because `ILCE-6400A` `1.01` blocks unauthenticated USB Senser `FileControl`, standalone SD auto-boot uses a **one-time companion setup** over `054C:0336` Jiritsu service mode (`tools/research/a6400a_sd_boot_install.py`):

1. **Build the Option B package (`MLV2.TAR` + `/setting` companions):**
   ```powershell
   wsl.exe -d REA-Ubuntu-24.04 --exec bash tools/native_camera/mlv2/build_wsl.sh artifacts/tests/mlv2-ilce6400a-sd-1 --target ilce_6400a
   ```
2. **Prepare your SD card (`FAT32` or `exFAT`, formatted in-camera first; preserves `DCIM/`):**
   ```powershell
   .\venv\Scripts\python.exe -m tools.research.a6400a_sd_boot_install --mode prep-card --build artifacts\tests\mlv2-ilce6400a-sd-1 --sd-root E:\
   ```
3. **One-time camera companion install (Administrator PowerShell, camera ON in `USB Connection -> Mass Storage`):**
   ```powershell
   .\venv\Scripts\python.exe -m tools.research.a6400a_sd_boot_install --mode full-install --build artifacts\tests\mlv2-ilce6400a-sd-1 --owner-authorized-sd-boot --confirm-firmware-101 --permit-control-pipe
   ```
4. **Boot & use:** Unplug USB, turn the camera OFF, remove the battery for ~5 seconds, reinsert the battery and your prepared SD card, and turn the camera ON. Within ~5 seconds `MENU -> MODS` (Tab 7) appears automatically.
5. **Kill-switches & full uninstall:**
   - **Instant stock boot:** Eject the SD card (or create an empty `MLV2.OFF` file at the SD card root) and power-cycle the camera.
   - **Full factory uninstall:** Run step 3 with `--mode full-uninstall` to remove `/setting/mlv2_*` and restore stock `/etc/ld.so.preload` absence.
