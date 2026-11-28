# E0755 — Live `ILCE-6400` TCP Read-Only Check: HEVC / 10-Bit Absence, Movie vs. Still `CamPig` / `cRAW2` DDR Buffers, and `0xF3031164` Allocation

> **Renumbered 2026-11-28:** drafted locally as `E0732`; that ID was taken on `origin/main` by an unrelated record, so this record is `E0755`. Cross-references were remapped (`E0729`→`E0753`, `E0730`→`E0752`, `E0731`→`E0754`, `E0732`→`E0755`); content is otherwise unchanged.

- **Date:** 2026-11-27
- **Scope:** Physical `ILCE-6400` read-only inspection over RAM-only 5 GHz Wi-Fi TCP debugger (`192.168.100.17:4640`, `/tmp_bt/tcp_debugd`, `pid=1532`) using `/tmp_bt/ro_page` (`artifacts/tests/ro-page-check/ro_page`, `O_RDONLY | O_SYNC` single-page 4 KiB `mmap` with `SIGBUS` guard, removed after each run) plus offline `hdmi.ko` / `libInfraHdmi.so` / `av-cam.bin` static analysis. No writes to `/setting/`, flash, or SD; `/dev/stream` not touched.

## 1. Live `/usr/sabin/` and `hdmi.ko` / `libInfraHdmi.so` Verification

1. **Live `/usr/sabin/` (`192.168.100.17:4640`):**
   - Only `ZIMA` DSP blobs are present (`sa_avce_quality_zma.bin` `231,044` B, `sa_avce_performance_zma.bin`, `sa_avce_performance_plus_zma.bin`, `sa_avcd_compatible_zma.bin`, `sa_avcd_performance_zma.bin`, `sa_zima_startup_zma0/1.bin`, `sa_lfnr_zma0.bin`, `sa_zma_lpcmdec.bin`).
   - No `CURIE` (HEVC), `Beaune` (10-bit XAVC), or `FPGA_PRORES` DSP binaries exist on the camera.
2. **Offline `hdmi.ko` (`238,744` B) and `libInfraHdmi.so` (`400,832` B):**
   - Zero `DeepColor`, `10bit`, or `12bit` strings/symbols. `YCbCr 4:4:4`, `YCbCr 4:2:2`, `BT2020ycc`, and `BT2020rgb` appear only in the EDID CEA sink parser (`hdmi_core_print_status_information`), and `depth` refers to 3D HDMI (`L + depth`).

## 2. Live Movie Mode `ro_page` Readback (TCP)

- **State:** `SET_RAW_EXTERNAL_OUTPUT` (`PA 0x04407C40..0x044080F4`): `+0x4AC = 1`, `+0x4B4 = 0` (inactive). `RCRAW_EN` (`PA 0x043D1810`) = `0`. `EC_GAMMA_BUF` (`PA 0x04427840`) holds an active 10-bit curve (`[22, 44, 66, 87, 108, ...]`).
- **`AIF0/1 W2` & `IDT`:** `IDT_RCRAW` (`0xF3000200..0xF300023F`) = `0`. `AIF0 W2` (`0xF3002480`): `en=1`, `run=1`, `cuAdrs=0xFE329110` (SRAM), `size=0x0148` (`328` B), `loop=0x018B`.
- **`CamPig 1`, `PQD`, `RC0`, `cRAW2`:**
  - `CamPig1_200` (`0xF3102200`): `en=1`, `run=0`, `cuAdrs=0x16118E80` (DDR), `size=0x0A80`.
  - `PQD_280` (`0xF3020280`): `run=1`, `cuAdrs=0xFE415680`, `size=0x00027000`; `PQD_380` (`0xF3020380`): `run=1`, `cuAdrs=0xFF020000`, `size=0x00070800`; `PQD_480` (`0xF3020480`): `run=1`, `cuAdrs=0x16118480` (DDR), `size=0x08C0`.
  - `RC0_280` (`0xF3881280`): `run=1`, `cuAdrs=0x1A765180` (DDR), `size=0x07C0`, `loop=0x0947`.
  - `cRAW2` (`0xF3030000`): `w0=1`, `w1=1` (enabled, idle).
- **Camera responsiveness:** `/tmp_bt/ro_page` removed; `PING` OK; `/proc/uptime` `708.47` -> `1399.28`.

## 3. Live Post-RAW Still Shot `ro_page` Readback (TCP)

After a single physical Still+RAW shutter release:

- **`0xF3031160..0xF30317EC` (`cRAW2` / `IDT` 512-word RAM):**
  - `+0x1160 = 0xFE32D500` (SRAM), `+0x1164 = 0x17CBC000` (DDR), `+0x1178 = 0x1991F800` (DDR)
  - `+0x1180..+0x1190`: `0x16AF3200`, `0x16B89200`, `0x16BAEA00`, `0x16BB9E00`, `0x16BBDA00` (active DMM DDR pointers)
  - `+0x17C4 = 0x16796000` (DDR), `+0x17C8 = 0x016000F0` (`352x240`), `+0x17DC = 0x64411BE0`, `+0x17E0 = 0x643CF4F4` (`av-cam.bin` `.bss` pointers)
- **`CamPig 1` full-sensor height:**
  - `CamPig1_200` (`0xF3102200`): `size=0x0740`, `loop=0x0FB7` (`4024` lines)
  - `CamPig1_400` (`0xF3102400`): `size=0x0DC8`, `loop=0x0F9F` (`4000` lines)
  - `CamPig0_480` (`0xF3002480`, W2): `cuAdrs=0xFE2EB680` (SRAM), `size=0x0190`, `loop=0x018F` (`400` lines, FOPD-only)
- **Camera responsiveness:** `/tmp_bt/ro_page` removed; `PING` OK; `/proc/uptime` `1828.97 4922.67`.

## 4. Offline `av-cam.bin` (`0x635C6000`) DDR Allocation Trace

- `0x6376BC7A..0x6376BCF8` calls the DMM allocator (`0x63940BE4`) for a `0xF000`-byte (60 KiB) DDR block, stores the physical address into `0xF3031164` via `0x6376BC6C`, and copies `0x400`/`0x480` tables into `+0x400`/`+0x800` with `0x63C01D7C`.
- `0x6376A500` is the pure setter for `0xF3031180`, while the bulk `0xF3031000..0xF30317FC` block is populated with DMM DDR pointers (`0x16AF3200..`).
