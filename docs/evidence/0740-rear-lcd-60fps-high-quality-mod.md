# E0740: Rear LCD 59.94 Hz Hardware Ceiling and `RearLcd60fps` (`Display Quality = High` 60 fps Live-View Mod)

- **Date:** 2026-11-27
- **Scope:** Offline hardware/firmware display refresh-rate audit (`VPH`, `PBL`, `SIO`/`DotLcd`, `viewUnified2.so`, `libObj.so`, `libbackup.so`) and `SMOD v2` implementation of `RearLcd60fps.mod` for `ILCE-6400` (`2.00` captured layout) and `ILCE-6400A` (`1.01`). No physical camera, SD card, or USB device was accessed.

## 1. Input & Tool Identities

| Input / Tool | Size (B) | SHA-256 / Version |
| :--- | ---: | :--- |
| `ILCE-6400` `2.00` captured `viewUnified2.so` (`E0524` `nflasha15`) | `11,530,508` | `fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d` |
| `ILCE-6400` `2.00` captured `libObj.so` (`E0524` `nflasha15`) | `20,862,756` | `e88b7e0fc61b8c98ac06c1979e6ebdd3fa8a2a49384b3fd5ffc8d06da4174ef1` |
| `ILCE-6400` `2.00` captured `libbackup.so` (`E0524` `nflasha15`) | `9,336` | `42729ac13bb10209ae77fb445168cfb21629826b2f8ca45e5f87b7a04b381f5d` |
| `ILCE-6400` `2.00` `av-cam.bin` (`nflasha3_unpacked`, `VA = 0x635C6000 + off`) | `16,674,456` | `9ef8fa1990f457054dcba635c47b8c04d01297a820863a9c5a971e5502989e83` |
| `ILCE-6400` `DX2/CX62600_UC2.bin` (JDI preset) | `1,170,960` | `241c8521035bc9ff1edbe66cf3d42d81879a4bef8ff9e21cbfb84530a1446154` |
| `ILCE-6400` captured `Backup.bin` (`inputs-emmc-v2-20261116-124021-a`, Innolux) | `1,171,036` | `13345a905173ef806944568d80107b72bf0688d08415216e11604ec0d3659344` |
| `ILCE-6400A` `1.01` `viewUnified2.so` (`nflasha15_unpacked`) | `11,534,720` | `a51e120f7ff6334902e2aa49e78bfadbcbcf000a6e31f947b1bdb8380edcf168` |
| `ILCE-6400A` `1.01` `libbackup.so` (`nflasha15_unpacked`) | `9,384` | `b6420f8f7cacade524e1ba7c5b0d8b06691c22ed793dca084fdf1c1d9ae092f3` |
| `arm-linux-gnueabi-gcc` (`arm-gcc-15.2.0-prepush`) | — | `15.2.0-16ubuntu1cross2` |
| Built `mods/rear_lcd_60fps/RearLcd60fps.mod` (`ilce_6400`) | `768` | `7767a887ffed98217d99fe7a6b7701a7e33bca2c299e7b2109c312700d3eb2af` |
| Built `mods/ilce_6400a/RearLcd60fps.mod` (`ilce_6400a`) | `768` | `4412826ec817b7745c31be0fffa0716bbf242443bf60e3980486781727f85719` |

## 2. Static Firmware Findings

1. **Physical scan-out ceiling (`59.94 Hz` NTSC / `50.00 Hz` PAL):**
   - Factory `BK4` presets (`0x005F0007 = 0x06`, `SIO` ch1 `0x00F900B2 = 1`, pixel clock GPIO `P4.29` `0x00F90820..0x00F90822`) show two 3.0-type `921,600`-dot (`640 × 480` RGB) 8-bit serial-RGB/YUV (`DotLcd`) TFT-LCD modules across `ILCE-6400` / `ILCE-6400A`: **JDI** (`DX2` / `DX2C_JDI_HIBIKI2`) and **Innolux** (`DX2C_INX` and captured `ILCE-6400` `Backup.bin`).
   - In `av-cam.bin`, `pbl_ck_async_panel` (`0x042be24`, MMIO `0xF2A01130`) enforces bitmask `0x00220280`, permitting only four clock dividers: `/20` (`div_reg = 0x13`), `/16` (`0x0F`), `/8` (`0x07`, EVF only), and `/6` (`0x05`).
   - Across all 54 VPH panel timing presets (`0x048b674..0x048f5cc`), 52 of 54 are strictly `59.94 Hz` (`858 × 525` @ `27.0 MHz` for `mode 0x06` at `0x048b678`) or `50.00 Hz` (`864 × 625` @ `27.0 MHz` at `0x048b770`). The only `119.84 / 100.00 Hz` presets (`0x048f0dc` / `0x048f1d4`) belong exclusively to `VPH_ENUM_MODEID = 0xD1` (`EVF High`), which keeps the `81.0 MHz` (`/8`) EVF clock and halves active vertical height (`1280 × 480` / `1024 × 384`, `Vtotal = 497` vs `1001`). In `libObj.so` (`0x52d82e`), `Finder Frame Rate` (`0x0107154D`) hardcodes `dev = 2` (`EVF`) when updating property `0x12006054` (`avbb_panelevf_SetEvfFrameRate`, cmd `0x2069`).
2. **Stock `Display Quality = High` throttles Rear LCD Live-View to `29.97 fps`:**
   - Across all 4 `BK4` presets, when `Display Quality = High` (`0x01070C8A = 0`, active in captured `Backup.bin`), EVF (`dev = 2`) uses `dispRes 0x0107147B = 0x00` (`IMX284` col 4 `1996 × 800`) at `0x01071487 = 0x02` (`29.97 fps`).
   - Meanwhile, Rear LCD (`dev = 1`) keeps the exact same display-resolution byte as `Standard` (`0x0107147D = 0x01 == 0x0107147E`, `IMX284` col 3 `1996 × 570`), yet `0x01071489 = 0x02` drops Rear LCD Live-View (`"model/CAMERA"` cmd `0x172` → `CamUser::SequenceSetStillEEFrameRateMode` `0x44e`) from `59.94 fps` (`0x0107148A = 0x01`) to `29.97 fps`.
   - Exhaustive literal search across `libObj.so` (0 hits) and `viewUnified2.so` (5 sites: `0x2ab5b8` / `0x2adfe4`, `0x2bfe30` / `0x2c3cb0`, `0x2c00f0` / `0x2c3f70`, `0x402a88` / `0x406ac8`, `0x492cdc` / `0x496ebc`) proves every `NoAdapter + High + Rear LCD` lookup calls `BackupManager::Bkup_Read(0x01071489, buf)` → `Backup_read` (`libbackup.so + 0x1204`, identical 14-byte position-independent prologue `80b584b002af07f108034ff0ff32` on both bodies).

## 3. Mod Design & Commands Executed

`RearLcd60fps` (`mods/rear_lcd_60fps/rear_lcd_60fps.c` + `rear_lcd_60fps_trampoline.S`):
1. Hooks `Backup_read` (`libbackup.so + 0x1204`: `0x0401f204` on `ILCE-6400`, `0x0405c204` on `ILCE-6400A`) and overrides `*buf = 0x01` (`60 fps`) in RAM **only** when `rc == 0 && buf != NULL && id == 0x01071489 && enabled != 0`. Never calls `Backup_write` or mutates `Backup.bin`.
2. In `rlcd_on()` / `rlcd_off()`, queries `getMountAdapterType()` (`viewUnified2.so + 0x2aafe0` / `+ 0x2ada0c`) and active panel output (`0x1200600B` via `viewUnified2.so + 0x403e20` / `+ 0x407e60`). When no LA-EA adapter (`2`/`3`) is attached and `dev == 1` (Rear LCD), invokes `CmnViewModelWrpCameraEeStatus::setCameraDispResolutionEEFrameRateNoAdapter(1)` (`+ 0x4029a0` / `+ 0x4069e0`) so toggling `ON`/`OFF` in `MENU → Mods` immediately switches Rear LCD Live-View between `59.94 fps` and stock `29.97 fps` without disturbing active EVF or LA-EA sessions.

```bash
wsl.exe --exec bash mods/rear_lcd_60fps/build_v2_wsl.sh artifacts/tests/rlcd60-ilce6400 --target ilce_6400
wsl.exe --exec bash mods/rear_lcd_60fps/build_v2_wsl.sh artifacts/tests/rlcd60-ilce6400a --target ilce_6400a
```

```powershell
.\venv\Scripts\python.exe mods\rear_lcd_60fps\test_mod_v2_unicorn.py mods\rear_lcd_60fps\RearLcd60fps.mod --target ilce_6400
.\venv\Scripts\python.exe mods\rear_lcd_60fps\test_mod_v2_unicorn.py mods\ilce_6400a\RearLcd60fps.mod --target ilce_6400a
.\venv\Scripts\python.exe -m unittest tests.test_mod_manager -q
```

## 4. Observations, Assumptions & Next Boundary

- **Observed (offline & Unicorn):** Both builds compile with `-Wall -Wextra -Werror -fpie -nostdlib` into `768`-byte `SMOD v2` (`RLC1`) binaries (`568` B `.text`, `32` B `.bss`, 1 hook, 4 relocated literal words across `libbackup.so` and `viewUnified2.so`). Cortex-A5 Thumb-2 Unicorn suites pass at reference and relocated (`+0x300000` / `-0x400000`) library bases across all 7 scenarios (stock `0x02`, live `ON` `(1, 0x01)` apply, EVF `0x01071487`/`0x0107147B` & LA-EA `0x01071483` preservation, `rc = -1` / `NULL` guards, live `OFF` `(1, 0x02)` restore, EVF-active skip, and LA-EA `2`/`3` skip).
- **Assumptions & limitations:** Offline disassembly and Unicorn verification do not constitute physical-camera or full-ISP timing telemetry; physical verification on `ILCE-6400` (`8.2.6.0`) / `ILCE-6400A` (`1.01`) requires staging `RearLcd60fps.mod` under an owner-authorized RAM-only `Mod Loader v2` session and confirming `59.94 fps` Live-View in `Display Quality = High` alongside `/tmp_bt/mods/rear_lcd_60fps.log`.
