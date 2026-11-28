# E0741: 3-Tier 4K & HD 3:2 Open Gate (`5.1K True 4K`, `6K Reg01 Full`, `Full-FOV 2K`) and `OpenGate32` / `OpenGateSensor` (`SMOD v2` + `SMNU`)

- **Date:** 2026-11-27
- **Scope:** Static disassembly (`av-cam.bin`, `libmpr.so`), Cortex-A5 Thumb-2 Unicorn verification, and full-system `sony-a6400` QEMU (`11.1.3`) guest verification of `OpenGate32.mod` (`9,788` B) and `OpenGateSensor.mod` (`6,672` B) (`SMOD v2` + `SMNU` nested submenu) for `ILCE-6400` (`2.00` captured layout) and `ILCE-6400A` (`1.01`). No physical camera, SD card, or USB device was accessed.

## 1. Input, Tool, and Artifact Identities

| Input / Tool / Output | Size (B) | SHA-256 / Version |
| :--- | ---: | :--- |
| `ILCE-6400` `2.00` captured `av-cam.bin` (`PA 0x035C6000`, `VA 0x635C6000`) | `16,803,048` | `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209` |
| `ILCE-6400` `2.00` captured `libmpr.so` (`E0524` `nflasha15`) | `12,662,568` | `bb31351cb0fd6f5bf6b5695b3c4602ef8cf5c914d5518e93f668634218a1ca76` |
| `ILCE-6400` `2.00` captured QEMU `emmc.img` (`inputs-pr-preflight-20261116-1555`) | `2,147,483,648` | `18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b` |
| `ILCE-6400A` `1.01` `av-cam.bin` (`nflasha3_unpacked`) | `16,826,304` | `3029a174b393fee9b2624d27737c20bc60e71cbbb33736b2a9363b57184ee03d` |
| `ILCE-6400A` `1.01` `libmpr.so` (`nflasha15_unpacked`) | `12,666,664` | `dfca46967875926b4397af929981dd66daeb242c97b8a7e92f44a456f8432807` |
| `arm-linux-gnueabi-gcc` (`arm-gcc-15.2.0-prepush`) | — | `15.2.0-16ubuntu1cross2` |
| `unicorn` / `capstone` (`venv`) | — | `2.1.4` / `5.0.7` |
| `qemu-system-arm` (`sony-a6400` machine) | — | `11.1.3` |
| Built `mods/open_gate/OpenGate32.mod` (`ilce_6400`) | `9,788` | `cf782455097396083362a4b1a6dc876bd44af4633b167ea2376ed924e6384c1d` |
| Built `mods/open_gate/OpenGateSensor.mod` (`ilce_6400`) | `6,672` | `f6cfed5215cfa69b160b9a2ca9fafecb2f97edd4f60ff9210a7ced492170839f` |
| Built `mods/ilce_6400a/OpenGate32.mod` (`ilce_6400a`) | `9,788` | `bfc03026fec4f1cac951017d5f909721bdf81c8f0b31f788b244fc18ee7414dd` |
| Built `mods/ilce_6400a/OpenGateSensor.mod` (`ilce_6400a`) | `6,672` | `4237297ff3aa32463ef2c0d042ae66551755a92e45290625b29db4bb93807dd9` |

## 2. Static & Unicorn Firmware Findings

1. **Three-tier 3:2 Sensor Gate architecture (`Sensor Gate` `cfg[0] = 0, 1, 2`):**
   - **Tier 0 (`5.1K True 4K`, default — `100%` within the `E0721–E0728` `FE <= 3445` hardware-safe envelope):**
     - Keeps `XAVC S 4K` on Sony's unbinned `1:1` 11-bit 2H `IMX284` Mode 16/17 (`0x1F` / `0x89`, `6000 × 3376` @ `24p`/`25p`) and Mode 15 (`0x13`, `4896 × 2760` @ `30p`) with `FE[6]`/`FE[9]` (`3392`, max `3445`) and `FE[1]`/`FE[3]` **100% stock (`dy = 0`, zero `Lost CAMOUT_V` possible)**.
     - Executing `ExlHelper_CalcCrop` (`0x63CE2232` / `0x63CE4F84`) and `0x6376E20C` (which invokes Sony's `16:9 -> 3:2` horizontal Q16 crop helper `0x6376E17C`) in Cortex-A5 Unicorn with `output_aspect = 0` (`3:2`) proves Sony's ISP extracts **`5064 × 3376` (`17.09 MP` = `5.06K × 3.38K`, `1.185×` Super-35 gate)** at `24p`/`25p` (`1.563×` supersampled into **`3240 × 2160`**) and **`4140 × 2760` (`11.43 MP` = `4.14K × 2.76K`)** at `30p` (`1.278×` supersampled into **`3240 × 2160`**), while routing `XAVC S HD` (`0x07`/`0x05`) to full-width Mode 10 (`0x44`, `1996 × 1332` → `1620 × 1080`).
   - **Tier 1 (`6K Reg01 Full` — experimental uncropped `6000 × 4000` `24 MP` `6K -> 4K` probe with `S-Ele` crash guard):**
     - Diffing all 125 rows × 13 columns of `IMX284` `reglist` (`0x6454C484` on `ILCE-6400`, `0x64550F4C` on `ILCE-6400A`) shows `col 3` (`1996×570` 3:2) and `col 11` (`6000×3376` 16:9) share the identical `0x99/0x33` analog ramp, and across **all 13 columns** `reg 0x0001 = 0x41` (`bit 0 = 1`, `col 6, 7, 9, 10, 11`) enables the 16:9 vertical window crop (`<= 3396`), whereas `reg 0x0001 = 0x40` (`bit 0 = 0`, `col 3, 4, 12`) selects uncropped full-gate 3:2 continuous readout — a bit left at `0x41` in all 16 `E0721–E0728` tests.
     - Patches `col 11` `reg 0x0001` (`PA 0x0454C4A6` / `0x04550F6E`: `0x4141 -> 0x4041`, verified by executing `REGLIST_APPLY_FN` `0x6379D618` / `0x6379E770` in Unicorn), `Y_START = 30`, `Y_SIZE = 4020`, all `66 × 2` `FE[6]`/`FE[9]` vertical offsets (`+624` → `4016` active, `+0x20E: 5818 -> 6888`), `size_rows[16,17]+0x3C` & `.bss` `rt_t14[16,17]+0x50` (`3376 -> 4000`), `6` `rcv_desc` halfwords (`+624`), lockstep `24p` `(731, 2052)` / `25p` `(701, 2052)` timings, and NOPs the `S-Ele` deliberate null-deref crash (`0x603F -> 0xBF00` at `PA 0x03907870` / `0x03909BA8`).
   - **Tier 2 (`Full-FOV 2K` — `100%` uncropped `23.5 × 15.6 mm` `1.00×` FOV at `24p`/`25p`/`30p`):**
     - Routes 4K & HD (`Movie Scope`) to **Mode 10 (`drive_key = 0x44`, column 12)**: `reg 0x0000 = 0x1C`, `reg 0x0001 = 0x40` (uncropped full `23.5 × 15.6 mm` 3:2 active array, unity-gain `0.00 dB` `3×3` H/V capacitive averaging / subsampled Bayer readout with `ECDrvParam[+0x20] = 0x1000` per [E0680](0680-photonbin-6mp-and-mlv2-nested-submenus.md)—not charge-domain FD summing → **`1996 × 1332` @ 12-bit ADC**), classified by `0x63CE2E00` / `0x63764714` as `sub = 0` (stock single-stripe `FE[4]`).
2. **Movie drive-key selectors, `ExlHelper` 3:2 gate, and `buf_sel`:**
   - Movie drive-key selectors (`0x2044` when Mode 10 routing is active):
     - 24p NTSC (`0x63909DAC` / `0x6390C0E4`): 4K `0x03909E4C` (`0x201F`), HD `0x03909E74` (`0x2007`); `ILCE-6400A`: `0x0390C184`, `0x0390C1AC`.
     - 25p PAL (`0x63909E80` / `0x6390C1B8`): 4K `0x03909F52` (`0x2089`), HD `0x03909FA6` (`0x2005`); `ILCE-6400A`: `0x0390C28A`, `0x0390C2DE`.
     - 30p NTSC (`0x63D4DA60` / `0x63D50826`): 4K `0x03D4DAB4` (`0x2013`), HD `0x03D4DAC8` (`0x2005`); `ILCE-6400A`: `0x03D5087A`, `0x03D5088E`.
   - `ExlHelper` aspect crop (`0x0376E23E` `0x6F05 -> 0x2500` and `0x03CE22CC` `0x6F19 -> 0x2100`; `ILCE-6400A`: `0x037702E2`, `0x03CE501E`): outputs `5064×3376` on `6000×3376`, `4140×2760` on `4896×2760`, `6000×4000` on `6000×4000`, and `1980×1332` (`out_h = 1332`) on `1996×1332`.
   - `buf_sel` (`0x03CAEAE4` / `0x03CB445A` `0xD112 -> 0xBF00`): routes 4K to built-in 3:2 DMM entry `6` (`3240×2160`, aligned `3264×2160`, pool `665`) and HD to entry `3` (`1620×1080`, aligned `1664×1080`). Notice `0x63D6721E..0x63D6722C` validates output height strictly against `1080` (`0x438`) or `2160` (`0x870`), which `3240×2160` and `1620×1080` satisfy natively.
3. **Mode 10 lockstep frame timing (`36.000 MHz` clock):**
   - `PA_T10_NTSC = 0x03E258FC` (`ILCE-6400A`: `0x03E282AC`), `PA_T10_PAL = 0x03E25AC4` (`0x03E28474`), and live `.bss` `PA_RT_T14_M10 = 0x0420173C` (`0x042062FC`): `24p` `(660, 2275)` = `23.976024` fps, `25p` PAL `(600, 2400)` = `25.000000` fps, `30p` `(660, 1820)` = `29.970030` fps.
4. **Critical `libmpr.so` `.text` vs `.rodata` fix (`MprLibCore::EncOpen`):**
   - `OpenGate32.mod` resolves `libmpr.so`'s live ASLR base from `/proc/self/maps` and patches both 2-mod-4 aligned Thumb-2 immediates in `MprLibCore::EncOpen` (`+0x87025A`/`+0x870696` `1920 <-> 1620` and `+0x870266`/`+0x8706A2` `3840 <-> 3240`) via `mprotect(..., PROT_READ|PROT_WRITE|PROT_EXEC)` + `__ARM_NR_cacheflush` (`0x0f0002`), alongside the XAVC S / MP4 `.rodata` width tables.

## 3. Commands Executed & Verification Results

```powershell
# 1. Cross-compile and package all four SMOD v2 + SMNU binaries in WSL
wsl.exe --exec bash mods/open_gate/build_wsl.sh artifacts/tests/og32-v2-a6400 --target ilce_6400
wsl.exe --exec bash mods/open_gate/build_wsl.sh artifacts/tests/og32-v2-a6400a --target ilce_6400a

# 2. Synthetic + real-firmware (av-cam.bin + libmpr.so) Cortex-A5 Unicorn suite
.\venv\Scripts\python.exe mods/open_gate/test_mod_v2_unicorn.py artifacts/tests/og32-v2-a6400 artifacts/tests/og32-v2-a6400a

# 3. Mod Loader v2 16-scenario Unicorn suite (including Scenario 16 SMNU)
.\venv\Scripts\python.exe tools\native_camera\mlv2\test_mlv2_unicorn.py artifacts\tests\mlv2-opengate-loader
```

- **Unicorn results (`tests/test_open_gate_32_mod.py`, `6/6 PASS`):**
  - Verified `SMOD v2` headers (`flags = 0x20` `SMOD_V2_FLAG_SUBMENU`, `4` submenu items in `OpenGate32.mod`, `3` in `OpenGateSensor.mod`), multi-base relocatability, full `SMNU` state transitions across all 3 `Sensor Gate` tiers, PAL/NTSC/cold-BSS `rt_t14[10,16,17]` handling, fail-closed preflight atomicity across all `276` sites, and 100% byte-identical `OFF` restoration.
  - Executed original firmware functions in `av-cam.bin` and `libmpr.so` for **both** `ILCE-6400` (`2.00` captured) and `ILCE-6400A` (`1.01`) across Tier 0 (`5.1K True 4K`: `5064×3376` & `4140×2760` crop, `FE[6]+0x00A == 3392`), Tier 1 (`6K Reg01 Full`: `REGLIST_APPLY_FN` staging `reg 0x0001 == 0x40`, `Y_START == 30`, `Y_SIZE == 4020`, `FE[6]+0x00A == 4016`, `S-Ele == 0xBF00`, `6000×4000` uncropped), Tier 2 (`Full-FOV 2K`: `0x44` Mode 10 `1996×1332`), and `OFF` (`100%` byte-for-byte match across the entire `16.8 MB` `av-cam.bin` and `12.6 MB` `libmpr.so` images).
- **Full-system `sony-a6400` QEMU (`11.1.3`) 3-tier guest verification (`artifacts/qemu-a6400/open-gate-32-v2-e2e-20261127/`):**
  - Booted captured `ILCE-6400` `2.00` (`emmc.img` `18e37ec3b3e56d26...` on disposable qcow2 overlay) with `Mod Loader v2` and `OpenGate32.mod` (`9,788` B), cycling `OFF -> ON (5.1K True 4K) -> 6K Reg01 Full -> Full-FOV 2K -> OFF` with live guest `/dev/mem` readback (`libmpr.so` live ASLR base `0x06e5b000`, `276` total verified sites):
    - `[STOCK_BEFORE_ON]`: `dk24=201f dkhd=2007 exl1=6f05 bsel=d112 reg01=4141 fe6a=0d40 sele=603f dmmw=00000f00`
    - `[TIER0_51K_TRUE_4K]` (`OG32_ON rsn=1 cfg=0000 st=ok w=032 u=0e2`): `dk24=201f dkhd=2044 exl1=2500 bsel=bf00 reg01=4141 fe6a=0d40 sele=603f dmmw=00000ca8`
    - `[TIER1_6K_REG01_FULL]` (`OG32_ON rsn=2 cfg=1000 st=ok w=0ac u=068`): `dk24=201f dkhd=2044 exl1=2500 bsel=bf00 reg01=4041 fe6a=0fb0 sele=bf00 dmmw=00000ca8`
    - `[TIER2_FULL_FOV_2K]` (`OG32_ON rsn=2 cfg=2000 st=ok w=0af u=065`): `dk24=2044 dkhd=2044 exl1=2500 bsel=bf00 reg01=4141 fe6a=0d40 sele=603f dmmw=00000ca8`
    - `[RESTORED_AFTER_OFF]` (`OG32_OFF rsn=0 cfg=2000 st=ok w=035 u=0df`): `dk24=201f dkhd=2007 exl1=6f05 bsel=d112 reg01=4141 fe6a=0d40 sele=603f dmmw=00000f00`
  - Post-run `emmc.img` SHA-256 re-verified identical (`18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b`).

## 4. Assumptions, Repeatability, and Next Boundary

- **Repeatability:** Deterministic across repeated builds (`mods/open_gate/build_wsl.sh`), Unicorn runs (`tests/test_open_gate_32_mod.py`), and disposable-overlay QEMU `sony-a6400` runs.
- **Assumptions & boundary:** Tier 0 (`5.1K True 4K`, `5064×3376` / `4140×2760` → `3240×2160`) and Tier 2 (`Full-FOV 2K`, `1996×1332` → `3240×2160`) keep `FE` vertical maxima at stock (`<= 3445` and `FE[4]`), within the `E0721–E0728` hardware-proven `CAMOUT_V` envelope, whereas Tier 1 (`6K Reg01 Full`) tests whether clearing `IMX284` `col 11` `reg 0x0001` bit 0 (`0x41 -> 0x40`) lifts the `3396`-line sensor window clamp on physical silicon (protected by the `0x603F -> 0xBF00` `S-Ele` crash guard). Physical camera MP4 clip validation awaits supervised user-authorized hardware testing via `python -m tools.research.verify_open_gate`.
