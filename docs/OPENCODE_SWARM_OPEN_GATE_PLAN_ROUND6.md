# OpenCode Swarm Mission Plan — Round 6: `av-cam` Error Ring (`avlog`), `apl_mov` Dual-Pipe (`3200`/`3264`), `ZIMA_AVC`/DMM, `libmpr.so` MP4 Muxer, and Movie LCD Aspect

> **Target audience:** OpenCode coordinator agent using the Swarm plugin (5 parallel subagents + coordinator synthesis).
> **Mode:** **100% OFFLINE STATIC RE + CROSS-BINARY / BOUNDED UNICORN VERIFICATION ONLY.**
> **Repository constraint (`AGENTS.md` §4):** Every artifact, script edit, evidence doc, map edit, and commit message in this repository **must be strictly English-only**.

---

## 0. Current Ground Truth After Live `A12`–`A19` (`E0729` + Live Follow-ups)

Live `ILCE-6400` RAM experiments `A12`–`A19` (all `noreboot on`, fail-closed, cleanly restored to stock) established:

| Step | Scope | Live Result |
| --- | --- | --- |
| `A5` / `A6` | `fe_rec_0a`/`fe_rec_19` (`4896x3376`, stock `idx1/idx3`) + `rcv_desc` + `size_rows` + `rt_t14` | **`ref=0`, smooth live-view**, valid `3840x2160` MP4 recorded |
| `A12` | `A10` minus `rec_fmt_tbl[0..1]` (`0x03E11750`, `0x03E11760` kept stock `3840`) | **`ref=0`, clean 16:9** (eliminated `A10`/`A11` stride tearing) |
| `A16` | `A12` + `tbl_0x34[0] = 6528` (`0x03E11890..A8`) only | **Continuous L/R (no split)**, horizontal stride lines, `ref=157` |
| `A13` / `A14` | `A16` + `desc_0xAC[0] w[5,7,9]` (`1684/421` or `1728/432`) | **Left half clean, right half vertically shifted** (dual-pipe split), `ref=18` / `10` |
| `A15` / `A17` | `A14` + `desc_0xAC[0] w[38]=1728` (with/without `w[28]=1792`) | **Black screen (no sensor image)**, `ref=16` / `29`; `.bss` `0x041F2AC8` remained `1984` |
| `A18` / `A19` | `A14` + `.text` `0x03761C64` (`movw r4, #3240` -> `1620` right-pipe offset) | **Black screen**, `ref=18` / `20`; `.bss` `0x041F2AC8` remained `1984` |

### Key Static Discoveries Already Verified Before Round 6
1. **`0x63761C58` (`0x63761C64`):** Contains hardcoded `mov.w r4, #3840` (`0xF44F 0x6470`) -> `lsrs r4, r4, #1` (`1920`) for `id == 19`/`20`, called from `0x6376334A`, `0x637633F4`, and `0x6376358A` inside `0x637631E4` to compute the right-pipe buffer offset. Because `A13`/`A14` left this at `1920` while `tbl_0x34[0]` was `6528`, the right half of the frame started at the wrong offset.
2. **Alignment gap:** `1620` (`3240 / 2`) is not 16- or 64-byte aligned (`1620 % 64 = 20`, `1620 % 16 = 4`), whereas stock 4K uses `1920 = 30*64`, `1984 = 31*64`, `7680 = 120*64`. Candidates `3200` (`1600 = 25*64`, `6400 = 100*64`, `200x135` MBs) and `3264` (`1632 = 102*16`, `6528 = 102*64`, `204x135` MBs) are macroblock-aligned.
3. **Observability gap:** `0x6394EC5C` -> `0x6394E4E4` (using `[0x6394ECC4] + pc`) writes formatted `av-cam` error strings into a `~0x9FF0`-byte RAM ring buffer. Decoding this ring allows future `ro_page` / `avlog` probes to print Sony's exact `CamCore` / `YC` / `SDVGA` error messages instead of guessing from `ref` counts.

---

## 1. Hard Operational Constraints

1. **NO hardware / USB / camera commands.** Never invoke `camera_debugger.py`, `adb`, or USB scripts.
2. **Map & tooling hygiene (`AGENTS.md` §5):**
   - Query `.\venv\Scripts\python.exe -m tools.research.knowledge_map query <term|VA|E-ID>` first.
   - Use `.\venv\Scripts\python.exe` (or `C:\Python314\python.exe` if needed for `capstone`/`unicorn`) + WSL `arm-linux-gnueabi-objdump` / `arm-linux-gnueabi-as` / Ghidra headless (`tools.research.rea_wsl_batch`).
   - Keep scratch scripts in gitignored `artifacts/scratch/` and clean up temporary files before finishing.
3. **Available local firmware tree (read-only inputs):**
   - `artifacts/hardware/av-cam-camera-original.bin` (`16,803,048` B, ORIL base VA `0x635C6000`, `PA = VA - 0x60000000`).
   - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/` (`bin/im.elf`, `lib/libmpr.so`, `lib/libObj.so`, `lib/viewUnified2.so`, etc.).
   - `firmware/extracted_a6400/firmware.tar_unpacked/0800_appli/setting/DmmConfig.bin`.
4. **Strict evidence bar (`AGENTS.md` §4):** Cite exact file paths, file offsets, **VAs**, **PAs**, and **disassembly**. Separate **observed** vs **inferred**.

---

## 2. Subagent Assignments (5 Parallel Workers)

### Subagent 1 (`avcam-error-log-ring`): `av-cam` Ring Logger (`0x6394EC5C` -> `0x6394E4E4`)
- **Scope:** Disassemble `0x6394EC5C`, `0x6394E4E4`, and any reader/dumper of `[0x6394ECC4] + pc` and `S-Ele Refresh` (`0x0434FD7E` / `0x04367AF0` / `0x63907870`) in `av-cam-camera-original.bin`.
- **Questions to answer:**
  1. What are the exact `.bss` VA and physical address (`PA = VA - 0x60000000`) of the `0x6394E4E4` log ring buffer, write cursor, wrap/count fields, and record header format (`+16` bytes)?
  2. Does `0x6394E4E4` record `CamCore` / `YC` / `SDVGA` / `apl_mov` errors (including `0x637632AE`, `0x637632E6`, `0x637635EA`, `0x63761C84`), and is the ring readable page-by-page via `ro_page` (`<= 256` B per call)?
  3. Provide the exact C struct / read recipe for an `avlog` command in `a6400_og_ram`.

### Subagent 2 (`apl-mov-dual-pipe-3200-3264`): `0x63761C58`, `0x637631E4`, and Alignment Proof
- **Scope:** Disassemble `0x63761C58`, `0x63762EB0..0x63762FA0`, `0x637631E4..0x63763700`, `0x637617FC`, `0x63761BF8`, and trace all `.bss` readers of `0x641F2AC8` (`desc_0xAC`), `0x641F1C38` (`tbl_0x34`), `0x641F194C`, and `0x641F19D4`.
- **Questions to answer:**
  1. Why did `A18`/`A19` (`0x03761C64` = `movw r4, #3240` -> `1620`) fail with `ref=20` and leave `0x041F2AC8` at `1984`? Was it `1620` alignment (`% 16 != 0` / `% 64 != 0`), an upstream check before `0x6376309C`, or `0x63761C92` (`r4 / r2`)?
  2. What are the exact computed offsets and alignments in `0x63761C58` and `0x637631E4` for **`3200`** (`1600`) vs **`3264`** (`1632`)?
  3. Verify `0x63761C58`, `0x6376309C`, and `0x637631E4` in bounded Unicorn for `3200x2160` and `3264x2160`, and check whether any other hardcoded `3840` (`0xF00`) / `1920` (`0x780`) / `1984` (`0x7C0`) exists in `0x6376xxxx` or `0x6395xxxx`.

### Subagent 3 (`zima-avc-dmm-3200-3264`): `VDF`, `DmmConfig.bin`, and `ZIMA_AVC` for `3200`/`3264`
- **Scope:** Inspect `vdf_tbl` (`0x63DF199C`, especially entry 6 `3240/3264`), `ZIMA_AVC` (`0x63E3B554`), `DmmConfig.bin`, and `dv_venc_initialize()` / `sa_avce_*_zma.bin` in `av-cam.bin` and `sabin/`.
- **Questions to answer:**
  1. For **`3200x2160`** (`200x135` MBs, stride `3200` or `3264`) and **`3264x2160`** (`204x135` MBs, stride `3264`), which exact `vdf_tbl`, `ZIMA_AVC`, `ZIMA_SCALER_A/B`, and `DmmConfig.bin` entries must be consistent?
  2. Does `ILCE-6400` have a ZV-E1-style "movie buffer already open" DMM lock if patched after entering Movie mode once, or are `vdf_tbl` / `DMM` re-read on every Movie start?
  3. Verify `dv_venc_initialize()` parameter checks in bounded Unicorn for `3200x2160` and `3264x2160` (vs `3240x2160` where `3240 % 16 = 8`).

### Subagent 4 (`libmpr-mp4-header-and-pasp`): `libmpr.so` MP4 Container (`tkhd`/`stsd`/`PROF`/`pasp`)
- **Scope:** Static disassembly of `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/libmpr.so` around the MP4 writer / `avc1` box builder (`tkhd`, `stsd`, `PROF`, `pasp`), cross-checking the `3840x2160` hits (`0x7d7450`, `0x7d8ce8`, `0x8fb0a8`, `0x97a0ac`, `0x97a0b8`, `0xa6bbfc`) and `AviSeqRec` / `RecStartAvcHdContent`.
- **Questions to answer:**
  1. **For Path A1 (Squeezed 3:2 in `3840x2160`):** Where does `libmpr.so` write `pasp` or `tkhd` display width/height, and can an `SMOD v2` hook set non-square PAR (`3:2` display aspect) without changing the `3840x2160` coded stream?
  2. **For Path A2 (Native `3200x2160` / `3264x2160`):** Which exact instructions/fields in `libmpr.so` set `avc1` `stsd` width, `tkhd` width, and Sony `PROF` width (the 32-bit ARM/Thumb counterpart to `zve1-open-gate-test` `L4-mp4size`), and are they `.text` (`SMOD v2` `mprotect` + `__clear_cache`) or `.data`?

### Subagent 5 (`movie-lcd-aspect-and-5064-fe`): Movie LCD Aspect & `5064x3376` Exact-3:2 FE Check
- **Scope:** Trace `viewUnified2.so` / `libObj.so` `_169` Movie-mode layout selection, `CaptureUtilityImageSize()` (`0x638EDE4C`), and `size_rows[16]` / `rcv_desc` horizontal crop (`4896x3376` `A5` vs `5064x3376` `1.500:1`).
- **Questions to answer:**
  1. Which exact function/table in `viewUnified2.so` or `libObj.so` forces the Movie 4K 24p rear LCD box to `16:9` (`640x360`) instead of `3:2` (`640x426`), and can an `SMOD v2` hook toggle it?
  2. Per `E0729` §9 recommendation, if `size_rows[16]` + `rcv_desc` horizontal fields are set to `5064x3376` while keeping `fe_rec_0a`/`fe_rec_19` at `A5` (`idx1`/`idx3`) or stock (`idx6`/`idx9`), what happens in Unicorn/static geometry?

---

## 3. Coordinator Synthesis Deliverable

Once all 5 subagents report back, the coordinator must synthesize a single evidence document under `docs/evidence/` containing:
1. **`avlog` Recipe:** Exact physical addresses and C struct to read Sony's `0x6394E4E4` error ring via `ro_page` / `a6400_og_ram`.
2. **Path A1 (`3840x2160` Squeezed 3:2) Package:** Exact `libmpr.so` `pasp`/`tkhd` PAR hook (if supported) + `5064x3376` vs `4896x3376` FE/rcv_desc table.
3. **Path A2 (Native `3200x2160` / `3264x2160`) Candidate:** Exact aligned patch set across `apl_mov` (`0x63761C58`, `desc_0xAC`, `tbl_0x34`), `VDF`/`ZIMA_AVC`, `libmpr.so` MP4 header, and `viewUnified2.so`/`libObj.so` LCD aspect, verified in Unicorn before any live test.
4. **Explicit Unknowns / Next Boundary:** Any remaining unverified runtime-populated tables before live hardware verification.
