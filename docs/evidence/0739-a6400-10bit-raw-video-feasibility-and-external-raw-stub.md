# E0739 — ILCE-6400 10-bit RAW video feasibility, `SET_RAW_EXTERNAL_OUTPUT` (`0x40BD`) 3G-SDI stub proof, `DDR-B` `SHS` ring buffer, and `QFHD` HDMI `UYVY` transport

Date: 2026-11-27. Offline static disassembly and bounded ARM/Thumb Unicorn execution only. No physical camera, USB, or SD card access.

## 1. Inputs and Tool Identities

| Input / Tool | Size / Version | Identity / SHA-256 |
| --- | ---: | --- |
| `ILCE-6400` `av-cam.bin` (`nflasha3_unpacked`, `ORIL v4` @ `0x635C6000`) | `16,674,456` B | `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e` |
| `ILCE-6400` `libObj.so` (`nflasha15_unpacked/lib`) | `20,860,436` B | `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1` |
| `ILCE-6400` `CAMERA_SCN_EXTERNAL_RAW_RECORD.so` (`nflasha15_unpacked/scenario`) | `7,793` B | `842fa541b4c2c60089a5b98f365554dd3120e7111da86dbecfe8887befd7104e` |
| `ILCE-6400` `hdmi.ko` (`nflasha15_unpacked/kmod`) | `238,744` B | `cc70cc5956b6bf48601cdd874fe31a03e1b7c7ddeb925bb22b8aebb4f86d9faf` |
| `ILCE-6400` `dmm.ko` (`nflasha15_unpacked/kmod`) | `84,896` B | `c5cb62579f4719bed9248b8e3dabe4d95c6fa57481e07908f10b68098e54a74f` |
| `ILCE-6400` `DmmConfig.bin` (`0800_appli/setting`, `for ilc8g-astra 2019/02/18`) | `77,108` B | `3e68b4f482ba24fceea1b572c8e2c5ee132f3fdd93ade64e634df47448bd3abd` |
| Python / Capstone / Unicorn | `venv` | ARM/Thumb static disassembly + bounded `UC_ARCH_ARM \| UC_MODE_THUMB` execution |

## 2. Verified Offline Observations

### 2.1 Complete 19-Mode `IMX284` Bit-Depth, Channel, and Timing Inventory (`av-cam.bin`)

Decoding all 19 entries of the `IMX284` configuration table (`0x63E12610`, stride `0x20`), geometry table (`0x63E129E8`, stride `0x4C`), and timing table (`0x63E13284`, stride `0x18`) establishes that every movie and live-view readout mode on `ILCE-6400` already outputs **11-bit or 12-bit Bayer RAW** into `CXD90045` (never 8-bit):

| Mode(s) | Active Bayer Array | ADC Bits (`bw[4]>>8`) | Channels (`bw[3]>>16`) | `(h1, v1)` Timing | Frame Rate (`@36 MHz` / `@72 MHz`) | Stock Role |
| --- | --- | ---: | ---: | --- | --- | --- |
| `0, 1` | `6000 x 4000` (3:2) | `14` | `1` | `1260 x 4148` | `6.89` / `13.78` fps | Single / uncompressed still |
| `18` | `6000 x 4000` (3:2) | `14` | `1` | `1260 x 4835` | `5.91` / `11.82` fps | Silent / long-exposure still |
| `2, 3` | `6000 x 4000` (3:2) | `12` | `1` | `831 x 4148` | `10.44` / `20.89` fps | Continuous Hi+ RAW burst |
| `4, 5` | `1996 x 570` | `11` | `1` | `619 x 1940` / `619 x 970` | `29.98` / `59.96` fps (`119.91`) | LiveView (Column 3 in [E0715](0715-qemu-f01-playback-capture-uhs-mmio-and-kexec-prereqs.md)) |
| `6, 7, 8` | `1996 x 438` | `11` | `1` | `546 x 550` | `119.88` / `239.76` fps | High-rate AF / EVF |
| `9` | `1996 x 800` | `11` | `1` | `619 x 1940` | `29.98` / `59.96` fps | LiveView |
| `10` | `1996 x 1332` (3:2) | `12` | `1` | `660 x 1820` | `29.97` / `59.94` fps | Full-width 3:2 subsampled LiveView |
| `11, 12` | `1996 x 1122` (16:9) | `11` | `1` | `840 x 1430` / `858 x 1750` | `29.97` / `23.98` fps (`59.94` / `47.95`) | Full HD 16:9 movie readout |
| `13` | `1752 x 986` (16:9) | `11` | `1` | `546 x 1100` | `59.94` / `119.88` fps | 1080p120 S&Q readout |
| `14` | `1024 x 684` (3:2) | `11` | `1` | `1232 x 975` | `29.97` / `59.94` fps | Focus magnifier / crop LiveView |
| `15` | `4896 x 2760` (16:9) | `11` | `2` | `840 x 1430` | `29.97` / `59.94` fps | 4K30 movie (~1.23x Super35 crop) |
| `16, 17` | `6000 x 3376` (16:9) | `11` | `2` | `862 x 1740` / `827 x 1740` | `24.00` / `25.02` fps | Oversampled 6K -> 4K24/25p movie |

### 2.2 Qualification of `CAMERA_SCN_EXTERNAL_RAW_RECORD.so` and `SET_RAW_EXTERNAL_OUTPUT` (`0x40BD`) — Unwired 3G-SDI FS-RAW Stub

This section resolves the static and Unicorn qualification of `SET_RAW_EXTERNAL_OUTPUT` on `ILCE-6400` (open frontier `F01` from [E0621](0621-a6400-10bit-and-raw-video-paths.md), [E0622](0622-a6400-aif-dma-channel-map-and-raw-external-gate.md), and [E9105](9105-a6100-rcraw-aif-w2-and-fopd-gates.md)):

1. **Scenario library (`CAMERA_SCN_EXTERNAL_RAW_RECORD.so`, `camera_external_raw_record.cpp`):**
   - `.data.rel.ro` (`0x008A84`) defines the 5-entry `cam_scn_external_raw_record_mtx_tbl`: `cam_scn_raw_output_on` (`0x0935`), `cam_scn_raw_output_off` (`0x0911`), `cam_scn_raw_device_acquire` (`0x08ED`), `cam_scn_raw_device_release` (`0x08C9`), and `cam_scn_raw_rsv` (`0x07BD`), invoked from `scenario_run` (`0x0959`).
   - All four active wrappers tail-call `cam_scn_raw_output_common(Camera::RAW_EXTERNAL_OUTPUT, CAM_SCN_RAW_FUNC_ID, u8, u32)` (`0x07EC`), which allocates a 20-byte (`0x14`) UIPC message on destination queue **`0x00030001`** (`av-cam.bin`), sets source queue **`0x00DC0000`** (`msg[-4]`), writes `msg[0] = 5`, `*(u16*)(msg + 2) = 0x40BD`, `msg[8] = {0: ON, 1: OFF, 2: ACQUIRE, 3: RELEASE}`, `msg[9] = u8_arg`, and `*(u32*)(msg + 10) = u32_arg`, calls `osal_snd_msg(0x00030001, msg)`, and blocks up to `5,000 ms` (`0x1388`) on `osal_rcv_msg_tmo(0x00DC0000)` expecting `reply[0] == 1` and `*(u16*)(reply + 2) == 0x40BD`.
   - Note correction to [E9105](9105-a6100-rcraw-aif-w2-and-fopd-gates.md) §2.3's tentative IPC label: `0x2044` and `0x2042` at `av-cam.bin` `0x63BDFDA8` / `0x63BDFDD6` (`bl 0x63D403E0(4, 0x2044/0x2042, ...)`) are `ulogio` trace event IDs in an adjacent command handler (`0x63BDFD40`), not external-RAW backend IPC messages.
2. **Application layer (`libObj.so`):**
   - Strings and RTTI at `0x010986AE..0x0117B851` pair `VALUEID_RAW_OUTPUT_OFF`, `VALUEID_RAW_OUTPUT_ON`, `VALUEID_MOVIE_RAW_2K`, `VALUEID_MOVIE_RAW_4K`, and `PARAMID_RAW_OUTPUT_MODE` with **`OBJDISPLAY::SeqControllerSdiLineout`** and **`OBJDISPLAY::SequenceSetSdiLineoutInternalOn`** (`[ObjDisplay]OsalSndMsg => SDI`), proving this control path belongs to Sony's FS-series (`PXW-FS5` / `PXW-FS7`) **3G-SDI FS-RAW** architecture rather than HDMI RAW output.
3. **Firmware command dispatch, license gate, and missing backend (`av-cam.bin`):**
   - Executing `0x63BDB174(0x40BD)` in Unicorn returns index **`0xA6`** (`166`) in command table `0x645773C8` (`entry @ 0x64577E28`: `cmd = 0x40BD`, `fn1 = 0x63BE0649`, `flags = 0`, `fn2 = 0x63BD9249`).
   - Gate `0x63BE06E0` resolves GOT base `0x645A21E4`, checks `*(u8*)(*[0x645A6BE0] + 0xE0)` (`0x643334E8 + 0xE0`), and compares `**[0x645A2630]` (`*0x64408588`) against constant key **`0x00BF0274`**, clearing `[r0] = 0` on mismatch.
   - Handler `0x63BE0648` (`SET_RAW_EXTERNAL_OUTPUT`) writes `msg[8]` -> `[0x64407C40 + 0x878]`, `msg[9]` -> `[+0x87C]`, and `*(u32*)(msg + 10)` -> `[+0x880]`, then calls `0x63BDFCE0` to register completion poll callback `0x63BDC8D8` (`SET_RAW_EXTERNAL_OUTPUT_COMP`), which returns `0` (pending) until `[r4, #0x6D4] == [0x64407C40 + 0x878]`, at which point it writes `0xF000` to `reply[+8]` and returns `0xC`.
   - Exhaustive Thumb-2 instruction and PC-relative literal scans across `0x635C6000..0x63DE0000` prove:
     - No function outside `0x63BE1E26` (init), `0x63BE0648` (`fn1` store), `0x63BD9248` (`fn2` echo check), and `0x63BDC8D8` (`COMP` check) reads `[0x64407C40 + 0x878..0x880]` (`0x6396098C`'s `+0x878` read targets `GOT[0x3EB0] = 0x6435CC38`, not `0x64407C40`).
     - Across the entire 8.5 MiB code segment, only **one** instruction writes to struct offset `#0x6D4`: `0x63D73B68` (`movs r3, #0; ... strb.w r3, [r0, #0x6d4]`), which zero-initializes the struct. No task or hardware ISR on `ILCE-6400` ever sets `[r4, #0x6D4] = 1` or drives an SDI/RAW serializer.

### 2.3 `DmmConfig.bin` 2 GiB DRAM Map (`DDR-B` `1,024 MiB`) and `SuperSlowManagerMainLsi` (`0x0332`)

1. **Physical DRAM & DMM pools (`DmmConfig.bin` `+0x0050..+0x0240`):**
   - `ILCE-6400` configures two 1 GiB DRAM ranks (**2 GiB total**), including a dedicated **`1,024.00 MiB` (`0x40000000` B) `DDR-B` DMM extent at `PA 0x80000000..0xC0000000` (`attr=0x15`, `tag=0x0012`, `flg=0x01000002`)** and a **`384.00 MiB` (`0x18000000` B) `DDR-A` capture extent at `PA 0x24000000..0x3C000000` (`attr=0x15`, `tag=0x0010`, `flg=0x01000001`)**.
2. **Built-in Main-LSI RAW ring buffer (`SuperSlowManagerMainLsi.cpp`, `0x6391E6FC..0x6391EB38`):**
   - `av-cam.bin` includes `Camera::SuperSlowManagerMainLsi` + `Stage_RawPlay.cpp`, which computes ring-buffer slot addresses via `mla r2, r1, r2, r3` (`base_addr + index * frame_bytes` at `0x6391EA26`) with wrap-around at `[r4, #0xD0]` (`0x6391EA80`).
   - `SHS AllocNew` (`0x6391E738`) requests DMM unit **`0x0332`** (`bl 0x63940BE4`). In stock `DmmConfig.bin` (`+0x11940`), unit `0x0332` is an unprovisioned stub (`00000275 00000000 00000332 ffffffff`, `size = 0`); `dmm.ko` (`dmm_ctrl_LoadExtraMapHdlr` @ `.text+0x55B8`) supports loading `/setting/DmmConfigExtra.bin`.

### 2.4 On-SoC `HDMI TX` (`0xF2A40000`) Bit-Depth and Range-Clip Contract (`hdmi.ko`)

- Disassembling `hdmi_core_task_reso` (`hdmi.ko` `.text+0xB65C..0xB9D0`) with resolved `.rel.text` symbols shows:
  - Color space (`[r4, #0x90]`): `2` selects RGB (`addr_1019_1_0 = 0`, `addr_0200_4_0 = 1`, `addr_0804_6_0 = 0x47`); non-`2` selects **`YCbCr 4:2:2`** (`addr_1019_1_0 = 1`, `addr_0200_4_0 = 0x16`, `addr_0804_6_0 = 0x0D`).
  - Color depth: `.text+0xB788` unconditionally writes **`addr_0803_1_0 = 0`** (8-bit/component TMDS, no 10-bit/12-bit Deep Color mode in `hdmi.ko`), with QFHD cell tables `HDMI_RESO_QFHD_24_Cells` (`.data+0x8828`), `HDMI_RESO_QFHD_25_Cells` (`+0x897C`), and `HDMI_RESO_QFHD_30_Cells` (`+0x8AD0`) driving `3840x2160p24/25/30` (`398.1–497.2 MB/s` packed 16-bpp `UYVY` from `VPH ch52` `0xF3441280`).
  - Range clipping: `hdmi_core_set_csc_range_clip` (`.text+0x9C20`, referenced from `.data+0x48B4`) sets `addr_4100_7 = 1`, `addr_411A = 0`, `addr_411B = 0xFE`, `addr_411C = 0`, `addr_411D = 1`, clamping transmitted bytes to `1..254` (`0x01..0xFE`) to protect HDMI EAV/SAV sync codes (`0x00`/`0xFF`).

## 3. Inference: Viable >=10-Bit Bayer RAW Video Paths and Bandwidth Envelope

Combining the verified hardware contracts above with [E0627](0627-a6400-zima-avc-8bit-ceiling.md) (`ZIMA_AVC` 8-bit ceiling), [E0635](0635-a6400-rcraw-fopd-vs-full-pipeline.md) / [E9105](9105-a6100-rcraw-aif-w2-and-fopd-gates.md) (`FOPD`/`AIF0 W2` `<= 640x480` cap), and [E0715](0715-qemu-f01-playback-capture-uhs-mmio-and-kexec-prereqs.md) (`0xF3030000` `ARW 2.3` `cRAW2` 16-byte hardware block compressor: `1.0 B/px` with two exact 11-bit `min`/`max` endpoints + fourteen 7-bit deltas, matching `IMX284`'s native 11-bit movie ADC domain at 20% lower bitrate than 10-bit linear packing):

| Candidate Format | 10-Bit Packed (`1.25 B/px`) | Hardware `cRAW2` (`1.0 B/px`, 11-Bit) | Path 1: Continuous UHS-I SD (`~70-85 MB/s` V60/V90) | Path 2: `1.0 GiB` `DDR-B` Burst Clip | Path 3: Clean `QFHD` HDMI `UYVY` Tunnel (`14.51 MB/frame` safe `[0x40..0xBF]`) |
| --- | ---: | ---: | --- | --- | --- |
| `1920 x 1080` @ `24p` / `30p` | `62.2` / `77.8 MB/s` | `49.8` / `62.2 MB/s` | **Viable (24/30p)** | `~17-21 s` | Viable |
| `1996 x 1122` (Mode 12/11) @ `24p` / `30p` | `67.1` / `83.9 MB/s` | `53.7` / `67.1 MB/s` | **Viable (`cRAW2` 24/30p; 10b 24p)** | `~16-19 s` | Viable |
| `1996 x 1332` (Mode 10 3:2) @ `24p` / `29.97p` | `79.8` / `99.6 MB/s` | `63.8` / `79.7 MB/s` | **Viable (`cRAW2` on V90 SD)** | `~13-16 s` | Viable |
| `3840 x 2160` (UHD 4K) @ `24p` | `248.8 MB/s` | `199.1 MB/s` | Physically impossible (`>104 MB/s` SDR104 bus) | **Viable (`~5.4-6.9 s` clip)** | **Viable (uncompressed 10/12/14-bit @ 24/25/30p)** |
| `4896 x 2760` (Mode 15) @ `29.97p` | `506.2 MB/s` | `404.9 MB/s` | Impossible | **Viable (`~2.6-3.3 s` clip)** | **Viable (`cRAW2` `13.51 MB/frame` @ 29.97p)** |
| `6000 x 3376` (Mode 16) @ `24p` | `607.7 MB/s` | `486.1 MB/s` | Impossible | **Viable (`~2.2-2.8 s` clip)** | Requires crop or multi-frame split |

- **Path 3 clip-safe encoding note:** Packing 7 payload bits per `UYVY` byte (`0x40 | (b & 0x7F)` in `0x40..0xBF`, strictly inside legal `16..235` video range) survives `hdmi_core_set_csc_range_clip` (`1..254`) and standard capture-card range handling without register patches, yielding `3840 * 2160 * 2 * 7 / 8 = 14,515,200` lossless bytes/frame (`348.4 MB/s` @ `24p`, `435.0 MB/s` @ `29.97p`).

## 4. Verification Commands and Receipts

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_sensor_tables tests.test_knowledge_map tests.test_research_status_sync -v
```

- Added `test_real_a6400_external_raw_40bd_stub_and_dmm_ddrb_unicorn` in [tests/test_sensor_tables.py](../../tests/test_sensor_tables.py), executing `CAMERA_SCN_EXTERNAL_RAW_RECORD.so` (`ON`/`OFF`/`ACQUIRE`/`RELEASE` -> `0x00030001` cmd `0x40BD`) and `av-cam.bin` (`0x63BDB174(0x40BD) -> 0xA6`, `0x63BE06E0` `0x00BF0274` gate, `0x63BE0648` state store, `0x63BDC8D8` `[r4+0x6D4]` completion poll, single zero-init store `0x63D73B68`), plus `DmmConfig.bin` `DDR-B` (`1,024 MiB`), `DDR-A` (`384 MiB`), and unit `0x0332` (`size = 0`) checks.
- Updated [docs/map/sources/imaging-pipeline.toml](../map/sources/imaging-pipeline.toml) (`C18`, `C19`, `F01`, and `CAMERA_SCN_EXTERNAL_RAW_RECORD.so` inventory link) and regenerated `docs/map/` outputs.

## 5. Assumptions, Limitations, and Next Boundary (`imaging-pipeline:F01`)

- **Offline scope:** All findings in this record are static disassembly and bounded Unicorn function execution; no live Movie-mode DMA capture or HDMI capture-card test has been run yet.
- **Remaining open boundary (`F01`):**
  1. Verify in `qemu-a6400` (`still-capture=on`, `sd-uhs-u3=on`) whether `CamPig`/`IDT` (`0xF3000200..0xF3000422`, `idt_opdtop_set_intn_capdat` @ `0x63B3BD1C`) and `0xF3030000` (`cRAW2` `0x00060000`) can compress frames into `DDR-B` (`0x80000000`) on every Movie V-sync alongside active LiveView (`VPH ch51`) without starving `Sgc1`/`PQD_Ydet`.
  2. Verify in `av-cam.bin` / `qemu-a6400` that `VPH ch52` (`0xF3441280`) `3840x2160` `UYVY` line-out passes 16-bit `Cb0 Y0 Cr0 Y1` words to `HDMI TX` (`0xF2A40000`) without horizontal chroma filtering when source and output widths both equal `3840`.
