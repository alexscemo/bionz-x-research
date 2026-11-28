# OpenCode Swarm Mission Plan — Round 5: Path A (`3:2` @ 24p) User-Space `3840x2160` Origin, `rec_fmt_tbl` / Stride Contract, and Full Pipeline Verification

> **Target audience:** OpenCode coordinator agent using the Swarm plugin (5 parallel subagents + coordinator synthesis).
> **Mode:** **100% OFFLINE STATIC RE + CROSS-BINARY / BOUNDED UNICORN VERIFICATION ONLY.**
> **Repository constraint (`AGENTS.md` §4):** Every artifact, script edit, evidence doc, map edit, and commit message in this repository **must be strictly English-only**.

---

## 0. Current Ground Truth (Path A Live Campaign `A1`–`A11` + Path C Status)

- **Path C (`> 3396` vertical sensor lines, `F04`):** Explicitly deferred as a long-term research frontier per user direction.
- **Path A (`<= 3396` vertical lines, `3:2` at full `24p`):** Live `ILCE-6400` RAM experiments (`A1`–`A11`, all with `noreboot` and clean `STOCK` restore) established the following observed facts:

| Step | Scope | Live Result |
| --- | --- | --- |
| `A1` | `.text` VDF `0x03CAEB1A`/`0x03CAEB2A` (`#4->#6`) + `ZIMA_AVC` (`3840->3240`) | `ref=0`, smooth live-view, LCD stayed 16:9 |
| `A2` | `fe_rec_0a` only (61 horizontal `u16` `6000->5064`) | `ref=47`, black screen |
| `A3` | `fe_rec_0a` only (71 horizontal `u16` from `idx 1` `4896`) + `rcv_desc` + `size_rows[16]+0x1C` + `rt_t14[16]+0x30` | `ref=47`, black screen |
| `A4` | `A3` + `size_rows[16]+0x14,+0x24` (`4->556`, `20->572`) + `rt_t14[16]+0x28,+0x38` | `ref=36`, black screen |
| **`A5`** | **Both** `fe_rec_0a` (`idx 6->1`, 160 non-V `u16`) + `fe_rec_19` (`idx 9->3`, 167 non-V `u16`) + `rcv_desc` (4) + `size_rows[16]` (3) + `rt_t14[16]` (3) (`337` entries, `4896x3376`) | **`ref=0`, smooth live-view**, LCD stayed 16:9 |
| `A6` | `A5` + `A1` (`345` entries) | `ref=0`, smooth live-view, recorded MP4 still 16:9 (`3840x2160`) |
| `A7` | `A6` + `disp_aspect_tbl` 9 LCD/EVF `3:2` heights (`640x360->426`, etc., `354` entries) | `ref=0`, LCD stayed 16:9 |
| `A8` | `A5` + `vdf_tbl[4]` data (`0x03DF19FC` `3840->3240`, `0x03DF1A04` `3840->3264`) + `ZIMA_AVC` (`345` entries) | `ref=0`, recorded MP4 still 16:9 |
| `A9` | `A8` + `vdf_tbl[1,2]` + `0x0454388C` + `ZIMA_AVC_2/3` + `ZIMA_SCALER` (`373` entries) | `ref=0`, recorded MP4 still 16:9 |
| **`A10`** | `A9` + last 4 `(3840, 2160)` pairs in `av-cam.bin` (`0x03E11750`, `0x03E11760`, `0x03E784DE`, `0x04570198`, `377` entries) | **`ref=0`, horizontal stride tearing across full screen** |
| **`A11`** | `A10` without `0x03E784DE` (`376` entries) | **`ref=0`, identical horizontal stride tearing** |

### Key Offline Root-Cause Already Proven Before Round 5
1. `0x63763F54` searches `rec_fmt_tbl` (`0x63E11750`, 16-byte entries `(w, h, flag, ..., id)`) for `(r5->w, r5->h)` and returns `-1` (`0xFFFFFFFF`) if no match is found.
2. Its **sole caller** `0x63956F38` (inside `0x63956E88` <- `0x63957EE6`) stores that result at `[r4+0x48]` and checks `r3 in {0, 19, 1, 20}` to set the 4K flag `[r4+0xC0]`. When `0x03E11750`/`0x03E11760` were patched to `3240` in `A10`/`A11`, `0x63763F54` returned `-1` and cleared `[r4+0xC0]`, causing the horizontal stride tear.
3. Because **every** `(3840, 2160)` pair and `movw` pair in `av-cam.bin` and `DmmConfig.bin` was already accounted for, **`(3840, 2160)` in `[r5+4]`/`[r5+6]` is coming from Linux user-space (`im.elf`) over UIPC**.
4. Offline scan of `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/` found `(3840, 2160)` in:
   - `libmpr.so` (`6` hits: `0x7d7450`, `0x7d8ce8`, `0x8fb0a8`, `0x97a0ac`, `0x97a0b8`, `0xa6bbfc`)
   - `libObj.so` (`1` hit at `0xe98bf6`: `(1610, 1078, 3840, 2160, 2880, 2160, 3240, 2160)` — note `3240x2160` is already adjacent!)
   - `viewUnified2.so` (`2` hits: `0x7d2da0`, `0x80facc`)

---

## 1. Hard Operational Constraints

1. **NO hardware / USB / camera commands.** Never invoke `camera_debugger.py`, `adb`, or USB scripts.
2. **Map & tooling hygiene (`AGENTS.md` §5):**
   - Query `python -m tools.research.knowledge_map query <term|VA|E-ID>` first.
   - Use `.\venv\Scripts\python.exe` (or `C:\Python314\python.exe` if needed) + WSL `arm-linux-gnueabi-objdump` / Ghidra headless (`tools.research.rea_wsl_batch`).
   - Keep scratch scripts in gitignored `artifacts/scratch/` and clean up temporary files before finishing.
3. **Available local firmware tree (read-only inputs):**
   - `artifacts/hardware/av-cam-camera-original.bin` (`16,803,048` B, ORIL base VA `0x635C6000`, `PA = VA - 0x60000000`).
   - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/` (`bin/im.elf`, `lib/libmpr.so`, `lib/libObj.so`, `lib/viewUnified2.so`, etc.).
   - `firmware/extracted_a6400/firmware.tar_unpacked/0800_appli/setting/DmmConfig.bin`.
4. **Strict evidence bar (`AGENTS.md` §4):** Cite exact file paths, file offsets, **VAs**, and **disassembly**. Separate **observed** vs **inferred**.

---

## 2. Subagent Assignments (5 Parallel Workers)

### Subagent 1 (`uipc-3840-origin`): `av-cam` UIPC Entry & Downstream of `0x63957EE6`
- **Scope:** Trace upward from `0x63957EE6` -> `0x63956E88` -> `0x63956F38` in `av-cam-camera-original.bin`, and downward from `[r4+0x48]` / `[r4+0xC0]` (`0x63956F76..`).
- **Questions to answer:**
  1. Which UIPC command / callback populates `r5` (`[r5+4]=width`, `[r5+6]=height`) passed into `0x63956E88`?
  2. What does `0x63956F76..` (and `0x63957EE6`'s callee `0x6376394C` / `0x63957E88`) do with `[r4+0x48]` (`0` or `19` from `0x63E11750`) and `[r4+0xC0]`?
  3. Does `0x63956E88` also feed the live-view/SDVGA scaler or only recording (`VDF`/`AVC`), and why did `0x63763F54` returning `-1` cause horizontal stride tearing on the live-view LCD?

### Subagent 2 (`userspace-libmpr-libobj`): `libmpr.so`, `libObj.so`, and `viewUnified2.so` `3840x2160` Tracing
- **Scope:** Static disassembly / ELF section & xref analysis of:
  - `libmpr.so` (`0x7d7450`, `0x7d8ce8`, `0x8fb0a8`, `0x97a0ac`, `0x97a0b8`, `0xa6bbfc`)
  - `libObj.so` (`0xe98bf6`, where `3240x2160` sits right next to `3840x2160`)
  - `viewUnified2.so` (`0x7d2da0`, `0x80facc`)
- **Questions to answer:**
  1. Which exact table/function in `im.elf` (`libmpr.so` / `libObj.so`) constructs the UIPC message carrying `(3840, 2160)` for XAVC S 4K 24p?
  2. What is the `libObj.so` `0xe98bf6` table (`(1610, 1078, 3840, 2160, 2880, 2160, 3240, 2160)`), and what selects `3840x2160` vs `3240x2160` there?
  3. Are the target entries in `.data`/`.data.rel.ro` (writable in process RAM) or `.rodata` (`r--p`, requiring `ptrace` or `nflasha15` overlay / runtime hook)?

### Subagent 3 (`fe-4896-vs-5064-crop`): `A5` (`4896x3376`) vs Exact `3:2` (`5064x3376`) FE Geometry
- **Scope:** Compare `fe_rec_0a` (`idx 6` vs `idx 1`) and `fe_rec_19` (`idx 9` vs `idx 3`) in `av-cam-camera-original.bin`, plus `0x63CCF5A2`, `0x63CCF5C6`, `0x63CCBFD0`, `size_rows[16]`, and `rt_t14[16]`.
- **Questions to answer:**
  1. In `A5` (`4896x3376`, `1.450:1`), what exact stage-2/3 horizontal width do `2016 -> 2224` and `+0x20C` (`6281 -> 5038`) represent?
  2. Can we derive a clean, `ref=0` **`5064x3376` (`1.500:1` exact `3:2`)** parameter set for `fe_rec_0a` + `fe_rec_19` + `rcv_desc` + `size_rows[16]` + `rt_t14[16]` (fixing what `A2` missed: `fe_rec_19`, `H_start`, `+0x20C`, `2016->2224`, `rcv_desc`, `size_rows`, `rt_t14`), or is `4896x3376` (`1.45:1`) preferable because it uses Sony's exact `idx 1`/`idx 3` horizontal constants?
  3. Verify the derived `FE` + `rt_t14` transform in bounded Unicorn if applicable.

### Subagent 4 (`lcd-liveview-169-to-32`): Movie Mode LCD / EVF Live-View Aspect (`16:9 -> 3:2`)
- **Scope:** Trace `zima_disp_tbl` (`0x63e38e98..0x63e3a540`), `disp_aspect_tbl` (`0x63e784c0..0x63e785f0`), `SDVGA` (`0x63f2b360`/`0x63f2b8d8`), and `viewUnified2.so` / `image_DSC_Only_169.uxc` in `nflasha15`.
- **Questions to answer:**
  1. Why did Movie 4K 24p LCD live-view stay `16:9` in `A5`–`A9` even after `disp_aspect_tbl` (`A7`) was patched?
  2. Which exact table or function sets the Movie-mode LCD preview box (`640x360` vs `640x424`/`640x426`) and OSD aspect?
  3. Does switching the user-space `(3840, 2160)` source (Subagent 2) automatically change the preview/playback aspect, or is a separate SDVGA/display patch needed?

### Subagent 5 (`mp4-encoder-and-ar-metadata`): `3240x2160` vs Stride Alignment (`3264`/`3200`) & MP4 Muxer
- **Scope:** Trace `vdf_tbl` (`0x6371B764` -> `0x63DF199C..0x63DF1B64`), `ZIMA_AVC` (`0x639DCF48..0x639DD36C` -> `0x63E3B56C..0x63E3C420`), `0x6454388C`, and `0x64570198` in `av-cam.bin`, plus XAVC S / MP4 container constraints in `libmpr.so`.
- **Questions to answer:**
  1. Does `ZIMA_AVC` / `VDF` / `SDVGA` require horizontal width or buffer stride alignment to `32`, `64`, or `128` pixels (note `vdf_tbl[6]` in Sony ROM already has `(3240, 2160, 3264, 2160)` where `3264 = 51 * 64`, whereas `3240 % 64 = 40`)?
  2. Are there any hardware/firmware limits rejecting `3240x2160` vs `3200x2136` vs `3264x2176` (`16x16` macroblock alignment: `2160 = 135 * 16`, `3240 % 16 = 8`, whereas `3264 % 16 = 0` and `3200 % 16 = 0`!)?
  3. Which exact fields in `vdf_tbl`, `ZIMA_AVC`, `rec_fmt_tbl` (`0x63E11750`), and `libmpr.so` must stay mutually consistent so the MP4 file is valid `3:2` without tearing?

---

## 3. Coordinator Synthesis Deliverable

Once all 5 subagents report back, the coordinator must synthesize a single evidence document under `docs/evidence/` containing:
1. **Verified Call Graph:** Exact user-space (`libmpr.so` / `libObj.so`) -> UIPC -> `av-cam.bin` (`0x63957EE6` -> `0x63956E88` -> `0x63956F38` -> `0x63763F54`) -> `VDF`/`ZIMA_AVC`/`SDVGA` data flow.
2. **Minimal Safe Patch Set (Step A12 candidate):** Exact target addresses (both `av-cam` PA and `im.elf` / `libmpr.so` / `libObj.so` offsets, noting whether `.data` or `.rodata`), macroblock/stride alignment (`3240` vs `3264` vs `3200`), and rollback plan.
3. **Explicit Unknowns / Next Boundary:** Any remaining unverified runtime-populated tables before live hardware testing.
