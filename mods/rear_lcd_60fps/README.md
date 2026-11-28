# RearLcd60fps (`Rear LCD 60fps`)

Relocatable **`SMOD v2`** RAM-only mod for **Sony `ILCE-6400` (`2.00` captured layout)** and **`ILCE-6400A` (`1.01`)** that unlocks full **`59.94 fps` (`60 fps` NTSC / `50 fps` PAL)** Live-View (`EE`) on the rear LCD monitor while keeping **`Display Quality = High`** active for the electronic viewfinder (`EVF`).

---

## Why This Mod Exists

1. **Rear LCD hardware ceiling:** The 3.0-type `921,600`-dot (`640 × 480` RGB) JDI and Innolux rear TFT-LCD panels on `ILCE-6400` / `ILCE-6400A` scan out over `VPH` mode `0x06` (`27.0 MHz` `858 × 525` NTSC / `864 × 625` PAL) at a fixed hardware maximum of **`59.94 Hz` (`50.00 Hz` PAL)**.
2. **Stock firmware bottleneck in `Display Quality = High`:** When `Menu → Setup → Display Quality` (`BK4 0x01070C8A`) is set to `High` (`0`) so the `1024 × 768` OLED EVF receives its higher-resolution `IMX284` readout (`BK4 0x0107147B = 0x00`, `1996 × 800` @ `29.97 fps`), `viewUnified2.so` also switches the **Rear LCD** (`dev == 1`) to `BK4 0x01071489 = 0x02` (**`29.97 fps` / `30 fps`**), even though the Rear LCD's own display-resolution setting in `High` (`0x0107147D = 0x01`) is **identical** to `Standard` (`0x0107147E = 0x01`, `IMX284` column 3 `1996 × 570`).
3. **What `RearLcd60fps` does:**
   - Hooks `Backup_read(int id, void *buf)` (`libbackup.so + 0x1204`) and overrides **only** `BK4 0x01071489` (`NoAdapter + High Quality + Rear LCD EE FrameRate`) in RAM from `0x02` (`30 fps`) to `0x01` (`60 fps`, matching `0x0107148A`).
   - Automatically covers **all 5** `viewUnified2.so` call paths: EVF ↔ Rear LCD eye-sensor switches (`CmnPanelCommonPattern`), `Display Quality` menu changes (`CustomLCDQuality`), Playback → Shooting transitions (`CmnViewModelWrpCameraEeStatus::setCameraDispResolutionEEFrameRateNoAdapter`), and lens/adapter updates.
   - Leaves **EVF `High` quality** (`0x0107147B = 0x00`, `0x01071487 = 0x02`), **LA-EA mount-adapter** frame rates (`0x01071481..0x01071484`), and persistent `/setting/Backup.bin` **100% untouched**.
   - On live toggle **`ON` / `OFF`** in `MENU → Mods`, checks the mount-adapter state and active panel output (`0x1200600B`) and—whenever no LA-EA adapter (`2`/`3`) is attached and the Rear LCD (`dev == 1`) is active—immediately calls `CmnViewModelWrpCameraEeStatus::setCameraDispResolutionEEFrameRateNoAdapter(1)` so Live-View switches between `59.94 fps` (`ON`) and stock `29.97 fps` (`OFF`) on the spot.

---

## Build & Verify Offline (WSL + Unicorn)

```bash
# Build ILCE-6400 (2.00 captured layout)
wsl.exe --exec bash mods/rear_lcd_60fps/build_v2_wsl.sh artifacts/tests/rlcd60-ilce6400 --target ilce_6400

# Build ILCE-6400A (1.01)
wsl.exe --exec bash mods/rear_lcd_60fps/build_v2_wsl.sh artifacts/tests/rlcd60-ilce6400a --target ilce_6400a
```

```powershell
# Run Cortex-A5 Thumb-2 Unicorn test suite (reference + relocated library bases)
.\venv\Scripts\python.exe mods\rear_lcd_60fps\test_mod_v2_unicorn.py artifacts\tests\rlcd60-ilce6400\RearLcd60fps.mod --target ilce_6400
.\venv\Scripts\python.exe mods\rear_lcd_60fps\test_mod_v2_unicorn.py artifacts\tests\rlcd60-ilce6400a\RearLcd60fps.mod --target ilce_6400a
```

---

## Install & Use

- **`ILCE-6400` (`2.00` captured layout):** Stage [`RearLcd60fps.mod`](RearLcd60fps.mod) into `mods/` on your SD card or `/tmp_bt/mods/` under Mod Loader v2, or enable **Rear LCD 60fps (High Quality)** in the BIONZ X Mod Manager GUI.
- **`ILCE-6400A` (`1.01`):** Use [`mods/ilce_6400a/RearLcd60fps.mod`](../ilce_6400a/RearLcd60fps.mod).
- Open `MENU → Mods` (tab 7) and toggle **`Rear LCD 60fps`** **`ON`** or **`OFF`**. Diagnostic state transitions log to `/tmp_bt/mods/rear_lcd_60fps.log`.
