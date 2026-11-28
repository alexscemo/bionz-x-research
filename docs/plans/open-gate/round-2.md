# OpenCode Swarm Mission Plan — Round 2: Root-Causing `Lost CAMOUT_V` Above `V_ACTIVE = 3392` (`plus24` / `test4`)

> **Target audience:** OpenCode coordinator agent using the Swarm plugin (parallel subagents + coordinator synthesis).
> **Mode:** **100% OFFLINE STATIC RE + BOUNDED UNICORN VERIFICATION ONLY.**
> **Repository constraint (`AGENTS.md` §4):** Every artifact, script edit, evidence doc, map edit, and commit message in this repository **must be strictly English-only**.

---

## 0. Mission Goal & Ground Truth (Do Not Re-Prove What Is Closed)

### 0.1 Proven Live & Static Baseline (`E0720`, `E0721`, `E0722`)

All work targets **exact byte-verified firmware**:
- Binary: `artifacts/hardware/av-cam-camera-original.bin` (`16,803,048` B, SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`, ORIL base VA `0x635C6000`).
- Open-gate tool & current 158-entry patch table: `tools/research/a6400_open_gate.py`, `tools/research/a6400_og_ram_table.h`, `tools/research/a6400_og_ram.c`.
- Prior static evidence: `docs/evidence/0722-a6400-open-gate-upper-ceiling-static-analysis.md`.

**Closed facts (live camera tests in `E0721` + static proofs in `E0722`):**
1. **`test1` PASSES:** Patching only `IMX284` `Y_START = 30` (`% 4 == 2`) and `Y_SIZE = 4020` (`reglist` col 11 `0x000A..0x000D`) with stock FE/RCV/timing runs smoothly with normal colors and no reboot.
2. **`timing4` PASSES:** Patching only `timing_rom_ntsc`/`pal` + `rt_t14` (`24p = (824, 1820)`, `25p = (791, 1820)`) runs smoothly with no reboot.
3. **`shrink` (`dy = -160`) PASSES:** Applying `-160` across all `158` entries with stock timing runs smoothly at full FPS with `refresh_cnt = 0`.
4. **`plus24` (`dy = +24`, `V_ACTIVE = 3400`, timing stock) & `test4` (`dy = +160`, `V_ACTIVE = 3536`, timing `(824, 1820)` / `(791, 1820)`) FAIL:** Both trigger `S-Ele Refresh` (`0x639078C4` increments `refresh_cnt`, `0x63907870` faults with `str r7, [r7]` when `0x0434FD7E` bit `0x40` is clear).
5. **Live `watch` trace (`E0721` §4) answered `E0722` §8's decision gate:**
   - `sgc1_HV` (`0x04339E30` / `0x04339E34`, VA `0x64339E30` / `0x64339E34`) **automatically switched** from `(858, 1750)` (MSC) to **`(824, 1820)`** at `t = 19800 ms` under `test4`. `SGC1` `H/V` period is **not** stuck on stock.
   - `rcv_ovf` (`0x044B02CA`) = `0`, `sdvga_ovf` (`0x044B02C9`) = `0`, `state_byte` (`0x0434D9AB`) = `0x00` throughout.
   - Exclusively `latch` (`0x044B02B9`, `l = 1`, `Lost CAMOUT_V`) fired every `vfc + 8` ticks (`11431..11615`, i.e., missing for `8` ticks / ~133 ms), incrementing `refresh_cnt` `0 -> 24 -> 38`.
6. **`DMM` is ruled out (`E0722` §5):** `DmmConfig.bin` (`0x63DF199C`) rule-5 slot (`0x2EF9200 = 49,254,912` B) accommodates `6000x4000x2 = 48,000,000` B.
7. **Sensor `Y_SIZE` register names verified (`E0722` §6):** `0x000A/0x000B` = `Y_START`, `0x000C/0x000D` = `Y_SIZE`, `0x000E/0x000F` col 11 = `6152`.

### 0.2 The Exact Paradox Round 2 Must Solve

Why does **`CAMOUT_V`** match/arrive every frame when `dy = -160` (`shrink`) AND when only sensor `Y_SIZE = 4020` is changed (`test1`), **yet miss every `8` `vfc` ticks when `FE`/`RCV` height goes above `3392` (`plus24`: `3400`; `test4`: `3536`)** — even with zero `rcv_ovf` and zero `sdvga_ovf`?

Specifically, static RE must identify **exact code paths, MMIO writes, and unpatched fields/tables** explaining **both** sides:
- Why `dy < 0` (`shrink`) keeps `CAMOUT_V` alive (`refresh_cnt = 0`), AND
- Why `dy > 0` (`plus24`, `dy = +24`, `V_ACTIVE = 3400`) causes `Lost CAMOUT_V` (`0x644B02B9` latch) to assert every `8` `vfc` ticks.

---

## 1. Hard Operational Constraints for All Agents

1. **NO hardware / USB / camera commands.** Never invoke `camera_debugger.py`, `adb`, USB scripts, or write to physical devices.
2. **Map & tooling hygiene (`AGENTS.md` §5):**
   - Start with `C:\Python314\python.exe -m tools.research.knowledge_map query <term|VA|E-ID>` and `docs/map/` before reading old evidence docs.
   - Use **system Python `C:\Python314\python.exe`** for offline scripts, Capstone/Unicorn probes, and tests. Keep temporary scripts in scratch memory or delete temp scripts before finishing unless explicitly kept as verified repo tests.
   - Follow `docs/REA_WORKFLOW.md` / `tools.research.symbol_index` if Ghidra/SQLite symbols exist; use direct ARM/Thumb disassembly (`capstone`) over `av-cam-camera-original.bin` mapped at `0x635C6000` whenever inspecting exact instructions.
3. **Strict evidence bar (`AGENTS.md` §4):**
   - Every claimed instruction, register write, struct offset, or table lookup must cite exact **VA**, **file offset**, **disassembly**, and **callers/data refs** observed directly in `artifacts/hardware/av-cam-camera-original.bin`.
   - Separate **observed** vs **inferred**. Do **not** propose live hardware write patches unless backed by an exact unpatched VA/offset and an explicit model explanation covering `shrink` vs `plus24` vs `test1`.
4. **Sequential write ownership:** Only the **coordinator** edits `docs/evidence/0698-*.md`, `docs/map/sources/imaging-pipeline.toml`, and runs `knowledge_map build` / `check` and `a6400_open_gate --verify` / `unittest`. Subagents perform read-only binary/code analysis and return structured findings with exact VAs, file offsets, and assembly snippets.

---

## 2. Swarm Topology — 5 Non-Overlapping Subagents

Launch all 5 subagents in parallel in Wave 1. Each subagent owns a strictly disjoint subsystem/address space so findings never duplicate and every boundary between `IMX284` -> `CAMIN/RCV` -> `FE` -> `SGC` -> `CAMOUT_V` -> `S-Ele` is covered.

### Subagent 1 (`fe-mmio-unpacker`): Full FE/RCV Descriptor Consumers & `0xF300xxxx`/`0xF301xxxx` MMIO Writes
- **Scope:** Disassemble all callers and readers of:
  - `0x63771C78` (helper returning `fe_rec_0a` `0x63E14E94` / `fe_rec_19` `0x63E15E54`),
  - `0x63CCC03E` (2-halfword copy helper) and all callers besides `0x63764578`,
  - Every `u16` and `u32` offset across the entire `fe_rec_0a` (`0x63E14E94..0x63E153D4`, `+0x000..+0x53F`) and `fe_rec_19` (`0x63E15E54..`), plus `rcv_desc` (`0x63E19720..0x63E197E0`).
- **Specific questions to answer:**
  1. Compare **all unpatched fields** in `fe_rec_0a` / `fe_rec_19` (notably `+0x028 = 3096`, `+0x252/+0x256 = 1506`, `+0x264 = 3012`, `+0x2C8 = 868` vs `15100`, `+0x2CE = 1668`, plus every non-zero halfword/word NOT in the `66` patched offsets) and `rcv_desc` (`0x63E19720..0x63E197E0`, e.g., `0x63E19754 = 3396`, `0x63E19768 = 3392`, `0x63E19794 = 3396`, `0x63E19798 = 3376`, `0x63E1979C = 6000`, `0x63E197AA = 3392`, `0x63E197B0 = 3447`, and surrounding words). Which functions read each unpatched height/line/crop/OB/blanking/timeout field, and what MMIO addresses (`0xF30xxxxx` / `0xF31xxxxx`) do they write?
  2. When `a6400_open_gate.py` adds `dy = +24` (`3376 -> 3400`, `3392 -> 3416`, `3396 -> 3420`) to the current `66` FE offsets and `rcv_desc` list, does **any downstream FE/RCV/CAMIN/CAMOUT height-end, active-line count, line-counter target, DMA write height, or frame-valid gate** remain <= `3392` or `3396`, or get violated (`requested_lines > upstream_lines`, `crop_end > input_lines`, `DMA_wait_lines > received_lines`, etc.)?
  3. Notice `test1` worked with sensor `Y_SIZE = 4020` while FE/RCV stayed at stock `3376/3392/3396`. What happens physically when sensor emits `3376` (`shrink`: FE patched to `3216`, fewer than sensor), vs sensor emits `3400` (`plus24`: FE patched to `3400`), vs `test1` (sensor `4020`, FE stock `3392`)? Does `FE` or `CAMIN` fail when a specific internal stage waits for `X > Y` lines?

### Subagent 2 (`camout-v-latch-tracer`): Disassembly of `Lost CAMOUT_V` (`0x63907D00..0x63908450`), `camout_vfc`, and `CAMOUT_V` Signal Source
- **Scope:** Complete disassembly and data/IRQ cross-referencing around:
  - `0x63907D00..0x63908450` (including `0x63907E14..0x63907E6A` where `Lost CAMOUT_V` string `0x63F2B2E7` is logged and `0x63908394` reading `[imaging+0x514]`),
  - Writers and readers of `0x644B02B9` (`latch`), `0x643679DC` (`camout_vfc`), `0x6436799C` (`camout_frc`), and `[imaging+0x514]`,
  - The ISR / hardware interrupt / callback that drives `camout_vfc` and whatever `r0` vs `r3` are compared at `0x63907E14` / `0x63907E20`.
- **Specific questions to answer:**
  1. Reconstruct the exact control-flow and formula from `0x63907D00` to `0x63908450`: what are `r0` and `r3` at `0x63907E14`, where is each incremented/written in the entire firmware, what triggers the `8`-tick threshold (`vfc + 8`), and what sets `0x644B02B9`?
  2. Trace upstream from the `camout_vfc` / `[imaging+0x514]` updater: what exact hardware block (`CAMOUT`, `CAMIN`, `FE`, `SDVGA`, `VSYNC`, `VD`, `SGC2`, `SGC3`, IRQ number / VIC / GIC vector, MMIO status register) produces the event that normally advances the matching counter every frame?
  3. Explain why `Lost CAMOUT_V` triggers only when configured `FE`/`RCV` height `> 3392` (e.g., `+24`), whereas `shrink` (`-160`) and `test1` (sensor `4020`, FE stock) keep the counter advancing normally.

### Subagent 3 (`sgc2-sgc3-mmio-tracer`): Complete `SGC1`/`SGC2`/`SGC3` Register Programming Map (`0xF3120200`, `0xF3120300`, `0xF3120400`, `+0x1000`)
- **Scope:** Disassemble `0x6376A9E0`, `0x638B6894`, `0x638B8CE8`, and all code referencing `0xF31202xx`, `0xF31203xx`, `0xF31204xx`, `0xF31212xx`, `0xF31213xx`, `0xF31214xx`, plus readers of `rt_t10` (`0x64200EFC`), `rt_t14` (`0x6420128C`), `rt_t18` (`0x64201B74`), `rt_t1c` (`0x6442F0C8`), and `0x643423B8+0x82C/+0x830`.
- **Specific questions to answer:**
  1. Map every `SGC1`, `SGC2`, and `SGC3` MMIO offset written during Movie 4K 24p/25p (`mode 16/17`) setup and frame-by-frame updates (`0xF3120200..0xF31204FF` and `+0x1000`).
  2. Recall `0x63F2B2E7` logs `sgc2[hq, vq]` alongside `Lost CAMOUT_V`! What are `hq` and `vq` in `SGC2`, where are `SGC2` / `SGC3` registers programmed, and which struct/table feeds `SGC2` and `SGC3`?
  3. Check whether `SGC2` or `SGC3` (or `SGC1` besides `+0x220`) writes **any** vertical blanking, line window, `CAMOUT_V` trigger line, `VD` gate, or `V_TOTAL`/`V_ACTIVE` threshold derived from an **unpatched** table (`rt_t10`, `rt_t18`, `rt_t1c`, `size_rows`, or static constants) — while remembering that `SGC1` `0x04339E30/34` (`(824, 1820)`) was already confirmed patched/active live.

### Subagent 4 (`imx284-full-wire-tracer`): Complete 125-Row `IMX284` `reglist` & Sensor Transfer Audit
- **Scope:** Inspect `reglist` (`0x6454C484`, all `125` rows, comparing `col 0` 3:2 still vs `col 10` 4K30p vs `col 11` 4K24p vs `col 12` vs adjacent columns), the sensor-register formatting/transfer routines (`0x6379E8FC`, `0x6379ECCC`, `0x6379ED1C`, `0x6379EFB8`, `0x6379F00C`), and send tables (`0x6454CF80`).
- **Specific questions to answer:**
  1. List **every row** out of `125` where `col 11` (`4K 24p`, `3376`) differs from `col 0` (full-height 3:2) or `col 10` (`4K 30p`, `2760`), grouped by register block (`row 15`, `rows 24-27` `0x001D..0x0020`, `row 31/32`, `row 69`, `row 72`, `row 81`, `row 88`, `row 113`, `row 116`, MIPI/SLVS packet/line-count/EBD/cropping/binning/VMAX registers).
  2. Identify whether any other register besides `0x000A..0x000D` caps or describes transmitted lines (e.g., MIPI CSI `Y_OUT_SIZE` / line count, digital crop window height, embedded data line position, `VMAX` vs `Y_SIZE` ceiling, or frame-footer / `VSYNC` / `XVS` timing register) or interacts with `rt_t14` `V` (`1820` vs `1750`).
  3. Reconcile `test1` (`Y_SIZE = 4020` on wire, `plus24` has `Y_SIZE = 3420` or `3400`): remember `test1` **passed** with `Y_SIZE = 4020`! Explain what `reglist` rows actually differ between `test1` (`Y_SIZE = 4020` on sensor, FE stock) and `plus24` (`Y_SIZE` patched +24 AND FE/RCV patched +24), proving whether the sensor wire side can already emit `> 3392` valid lines cleanly.

### Subagent 5 (`camcore-latch-callers`): CamCore Pipeline Height Tables, Interpolation/Clamp Tables, and the 3 Other Latch Callers
- **Scope:** Inspect:
  - The 3 CamCore latch callers of `0x63907B74` / `0x644B02B9` (`0x63911EAC`, `0x6391383E`, `0x63914094`) and surrounding mode/state logic,
  - Height-keyed / 4K-mode lookup tables near `0x6454AB60` (`[3360..3488]`), `0x63E48EA0`, `0x63E4AC80`, `0x63E58000`, `0x63E87A50`, and `size_rows[16,17]` (`+0x00..+0x7F`, not just `+0x3c`),
  - Any `CAMIN` / `SDVGA` / `CAMOUT` / `AE` / `AWB` / `AF` / `LDEC` / `R2Y` / `MD` / `Vout` block that computes vertical line coordinates, tile counts, buffer strides, interrupt line numbers, or DMA block counts from `size_rows[16,17]+0x3c`, `fe_rec`, or `rcv_desc`.
- **Specific questions to answer:**
  1. Audit every word of `size_rows[16]` and `size_rows[17]` against `size_rows` of taller/still modes and shorter modes (`1080p` / `4K 30p`). Which offsets inside `size_rows` besides `+0x3c` encode vertical size (`3376`/`3392`/`3396`/`3447`/`1688`/`2160`/etc.), tile heights, DMA split lines, or IRQ trigger lines?
  2. Check whether any vertical pipeline module (`CAMIN`, `FE`, `SDVGA`, `CAMOUT`, `Venc`, `Vout`) performs a runtime comparison, clamp, switch-case, or lookup against `3376`, `3392`, `3396`, `3442`, `3447`, `3480`, or `1688`, OR hits a tile/strip boundary when height increases past `3392` (e.g., `> 3392` vs `<= 3392`, or crossing `3392`/`3400`!) while working smoothly for `shrink` (`3216..3236`).
  3. Inspect the 3 CamCore functions (`0x63911EAC`, `0x6391383E`, `0x63914094`) to verify none of them share a common pipeline stall or mode-validation root cause with `Lost CAMOUT_V`.

---

## 3. Coordinator Synthesis Protocol (After All 5 Subagents Report)

1. **Cross-check all 5 reports for direct contradictions or gaps.**
   - Every candidate unpatched offset, constant, or MMIO write **must** pass the **Two-Experiment Filter**:
     - Why did `shrink` (`dy = -160`) **PASS** (`refresh_cnt = 0`)?
     - Why did `test1` (`Y_SIZE = 4020`, FE/RCV stock) **PASS**?
     - Why did `plus24` (`dy = +24`, FE/RCV `3376->3400` / `3392->3416` / `3396->3420`) **FAIL** with `Lost CAMOUT_V` every `8` `vfc` ticks and zero `rcv_ovf` / `sdvga_ovf`?
   - Any hypothesis that breaks `shrink` or `test1` without explaining why is **rejected**.
2. **Optional bounded Unicorn check (offline only):**
   - If the coordinator finds the candidate function computing the faulty MMIO register / `CAMOUT_V` trigger / height table, optionally run a small bounded Unicorn probe on `av-cam-camera-original.bin` (read-only input!) under `C:\Python314\python.exe` with stock vs `shrink` vs `plus24` inputs to confirm the register/return difference deterministically. Record exact Unicorn inputs, start/stop VAs, and observed registers.
3. **Update repository evidence & map (strictly English-only):**
   - Write a thorough, structured evidence report to `docs/evidence/0723-a6400-open-gate-camout-v-plus24-root-cause.md` following `AGENTS.md` §4 (exact command/inputs, binary identity, observed disassemblies, verified root cause or exact remaining open boundary, failed/excluded hypotheses, repeatability).
   - Update `docs/map/sources/imaging-pipeline.toml` with all newly discovered `0698` nodes, symbols, MMIO mappings, and edges, then run:
     ```powershell
     C:\Python314\python.exe -m tools.research.knowledge_map build
     C:\Python314\python.exe -m tools.research.knowledge_map check
     ```
   - Do **not** modify `tools/research/a6400_open_gate.py` or `a6400_og_ram_table.h` blindly. Only propose (or cleanly gate behind `--verify`-tested) a new patch hypothesis if the root cause is proven from assembly + verified via:
     ```powershell
     C:\Python314\python.exe -m tools.research.a6400_open_gate --verify
     C:\Python314\python.exe -m unittest tests.test_a6400_open_gate -q
     ```
4. **Final check before coordinator finishes:**
   - Verify no non-English text anywhere in modified/created files.
   - Confirm no temporary scratch files remain in the repository root.
   - Return a clear summary citing `docs/evidence/0723-a6400-open-gate-camout-v-plus24-root-cause.md`, exact VAs/offsets, why `shrink` & `test1` pass while `plus24` triggers `Lost CAMOUT_V`, and the exact read-only live verification check (or single targeted live test) recommended next.
