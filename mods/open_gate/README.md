# Open Gate 3:2 Recording (`OpenGate32.mod` & `OpenGateSensor.mod`)

Relocatable **`SMOD v2`** runtime mods with a **`Mod Loader v2` nested submenu (`SMNU`)** enabling configurable **4K & HD 3:2 Open Gate** Movie recording on Sony **`ILCE-6400` (`2.00` captured layout)** and **`ILCE-6400A` (`1.01`)** across **three selectable `Sensor Gate` tiers**:

1. **`5.1K True 4K` *(default — genuine >4K unbinned oversampling, zero `Lost CAMOUT_V` risk)*:**
   - **`XAVC S 4K 24p/25p`:** Keeps Sony's native unbinned `1:1` 11-bit 2H **`IMX284` Mode 16/17 (`0x1F` / `0x89`, `6000 × 3376`)** and keeps `FE[6]`/`FE[9]` **100% stock (`FE max = 3445 <= 3445`, completely immune to the `E0721–E0728` `Lost CAMOUT_V` ceiling)** while unlocking `ExlHelper` `3:2` (`output_aspect = 0`, invoking `0x6376E17C` / `0x63CE2232`). Extracts the centered **`5064 × 3376` (`17.09 MP` = `5.06K × 3.38K`, `1.185×` Super-35 4-perf gate)** 3:2 region and **supersamples `1.563×`** into razor-sharp **`3240 × 2160` (`3:2` 4K)** (identical oversampling density to Sony's `6K → 3.84K` 4K!).
   - **`XAVC S 4K 30p`:** Keeps unbinned **`IMX284` Mode 15 (`0x13`, `4896 × 2760`)** → extracts **`4140 × 2760` (`11.43 MP` = `4.14K × 2.76K`)** and **supersamples `1.278×`** into true **`3240 × 2160` (`3:2` 4K)**.
   - **`XAVC S HD`:** Routes HD (`24p`/`25p`/`30p`) to full-width `IMX284` Mode 10 (`0x44`, `1996 × 1332` `1.00×` FOV) → **`1620 × 1080` (`3:2` HD)**.
2. **`6K Reg01 Full` *(experimental uncropped `6000 × 4000` `24 MP` `6K → 4K` probe with `S-Ele` crash guard)*:**
   - Diffing all 13 `IMX284` `reglist` columns (`0x6454C484` / `0x64550F4C`) revealed that every `16:9` / windowed video column (`col 6, 7, 9, 10, 11`) sets **`reg 0x0001 = 0x41`** (`bit 0 = 1`), whereas every full-gate `3:2` continuous column (`col 3, 4, 12`) sets **`reg 0x0001 = 0x40`** (`bit 0 = 0`) — a bit left untouched at `0x41` in all 16 `E0721–E0728` runs.
   - Flips `col 11` `reg 0x0001` (`0x41 -> 0x40`) + `Y_START = 30, Y_SIZE = 4020` + all `66 × 2` `FE[6]`/`FE[9]` vertical offsets (`+624` → `4016` active) + `size_rows[16,17]`/`rt_t14[16,17]` `V_ACTIVE = 4000` + `6` `rcv_desc` halfwords (`+624`) + lockstep `24p` `(731, 2052)` / `25p` `(701, 2052)` timings, **and automatically NOPs the `S-Ele` deliberate null-deref crash (`0x603F -> 0xBF00` at `PA 0x03907870` / `0x03909BA8`)** so the camera cannot reboot even if physical `IMX284` silicon rejects `(0x0C, 0x40)`.
3. **`Full-FOV 2K` *(100% uncropped `23.5 × 15.6 mm` `1.00×` FOV at `24p`/`25p`/`30p`)*:**
   - Routes both 4K & HD (`Movie Scope`) to Sony's continuous full-gate 3:2 **`IMX284` Mode 10 (`drive_key = 0x44`, `1996 × 1332` @ 12-bit ADC, unity-gain `0.00 dB` `3×3` capacitive averaging / subsampled Bayer readout per [E0680](../../docs/evidence/0680-photonbin-6mp-and-mlv2-nested-submenus.md), stock single-stripe `FE[4]`)**, upscaling to `3240 × 2160` or downsampling to `1620 × 1080` with zero crop even at `30p`.

See **[E0741](../../docs/evidence/0741-open-gate-32-mode10-smod-v2.md)** for full disassembly proofs, Unicorn verification across both targets, and live `sony-a6400` QEMU (`11.1.3`) menu/submenu/memory-readback receipts.

---

## Submenu Options (`MENU` → `Mods` → `Open Gate 3:2 >`)

`OpenGate32.mod` uses `SMOD_V2_FLAG_SUBMENU` (`SMNU`, all 6 rows on a single `1/1` page). Editing any option while `Mod Status = ON` reconfigures RAM live via `og_cfg(2)`:

| Option | Choices | Behavior |
| :--- | :--- | :--- |
| **Sensor Gate** | `5.1K True 4K` *(default)* \| `6K Reg01 Full` \| `Full-FOV 2K` | Selects **`5064×3376` (`5.1K`) / `4140×2760` (`4.1K`)** unbinned oversampled 4K (`FE <= 3445`), experimental uncropped **`6000×4000` (`6K`)** (`reg 0x0001 = 0x40` + `S-Ele` crash guard), or uncropped **`1996×1332` (`1.00×` FOV)** Mode 10. |
| **Canvas Format** | `Native 3:2 4K` *(default)* \| `Native 3:2 HD` \| `Native 3:2 Both` \| `Anamorphic` | `Native 3:2 4K` encodes square-pixel `3240×2160` (`DMM` `idx 6` `3264` stride + `VPI` + `ZIMA DVENC` + `libmpr.so` `MprLibCore::EncOpen` `.text` & `.rodata`); `Native 3:2 HD` encodes `1620×1080`; `Native 3:2 Both` sets both; `Anamorphic` keeps `3840×2160` / `1920×1080` (`1.33×` horizontal squeeze). |
| **M10 Timing** | `24p (23.98fps)` *(default)* \| `30p (29.97fps)` \| `Stock Auto` | Sets Mode 10 (`0x44`) frame timing (`660×2275` = `23.976` fps NTSC / `600×2400` = `25.000` fps PAL, `660×1820` = `29.970` fps, or stock). |
| **Movie Scope** | `4K + HD` *(default)* \| `4K Only` \| `HD Only` | Controls whether 4K XAVC S, HD XAVC S, or both Movie paths apply sensor routing. |

`OpenGateSensor.mod` (`Open Gate Sensor`) exposes the same 3-tier **`Sensor Gate`**, **`M10 Timing`**, and **`Movie Scope`** submenu options while keeping `DMM`, `VPI`, `ZIMA DVENC`, and `libmpr.so` 100% stock (`3840×2160` / `1920×1080` `1.33×` anamorphic squeeze).

---

## Shipped Binaries

| Target | File | Size (B) | SHA-256 |
| :--- | :--- | ---: | :--- |
| `ILCE-6400` (`2.00` captured) | `mods/open_gate/OpenGate32.mod` | `9,788` | `cf782455097396083362a4b1a6dc876bd44af4633b167ea2376ed924e6384c1d` |
| `ILCE-6400` (`2.00` captured) | `mods/open_gate/OpenGateSensor.mod` | `6,672` | `f6cfed5215cfa69b160b9a2ca9fafecb2f97edd4f60ff9210a7ced492170839f` |
| `ILCE-6400A` (`1.01`) | `mods/ilce_6400a/OpenGate32.mod` | `9,788` | `bfc03026fec4f1cac951017d5f909721bdf81c8f0b31f788b244fc18ee7414dd` |
| `ILCE-6400A` (`1.01`) | `mods/ilce_6400a/OpenGateSensor.mod` | `6,672` | `4237297ff3aa32463ef2c0d042ae66551755a92e45290625b29db4bb93807dd9` |

> **Relocation note:** `OpenGate32.mod` and `OpenGateSensor.mod` intentionally omit `SMOD_V2_FLAG_RELOC` (`RLC1`), because `LIRO` physical addresses (`PA 0x0420xxxx..0x0457xxxx`) numerically overlap user-space library ranges in `im.elf` `/proc/self/maps`. `OpenGate32.mod` resolves `libmpr.so`'s live user-space ASLR base directly from `/proc/self/maps` at runtime.

---

## Build & Verify

```powershell
# Rebuild both targets in WSL (requires zero non-PIE relocations and zero libc helpers)
wsl.exe --exec bash mods/open_gate/build_wsl.sh artifacts/tests/og32-v2-a6400 --target ilce_6400
wsl.exe --exec bash mods/open_gate/build_wsl.sh artifacts/tests/og32-v2-a6400a --target ilce_6400a

# Run synthetic guard/matrix + real-firmware (av-cam.bin + libmpr.so) Unicorn test suite
.\venv\Scripts\python.exe mods/open_gate/test_mod_v2_unicorn.py artifacts/tests/og32-v2-a6400 artifacts/tests/og32-v2-a6400a

# Verify an on-camera recorded MP4 clip after supervised physical testing
.\venv\Scripts\python.exe -m tools.research.verify_open_gate --before before_card.json --after C0001.MP4 --size 3240 2160
```

---

## Safety & Verification Boundary

- **RAM-only, fail-closed, two-phase commit:** Both mods verify the `ORIL` `av-cam.bin` build header at `PA 0x035C6000` (`+0xB8/+0xBC`) and check every target word/halfword (`276` sites in `OpenGate32.mod`) against `orig` or `patch` before writing a single byte. Turning `Mod Status` `OFF` (or restarting the camera) restores 100% of `av-cam.bin` and `libmpr.so` to stock.
- **Current verification scope:** Verified statically, in Cortex-A5 Thumb-2 Unicorn against real `ILCE-6400` (`2.00` captured) and `ILCE-6400A` (`1.01`) `av-cam.bin` + `libmpr.so`, and end-to-end in full-system `sony-a6400` QEMU (`11.1.3`) across all 3 `Sensor Gate` tiers. Physical on-camera MP4 clip recording (recommended order: `5.1K True 4K` -> `Full-FOV 2K` -> `6K Reg01 Full` with its built-in `S-Ele` crash guard) awaits supervised user-authorized hardware testing.
