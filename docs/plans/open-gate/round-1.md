# Swarm Mission: Root-Cause the >3392-Line Upper Ceiling for ILCE-6400 Squeezed 3:2 Open Gate

## Objective & Physical Truth Table (Already Verified on Hardware — Do NOT Re-test Live)

Target binary: `artifacts/hardware/av-cam-camera-original.bin`
- Size: `16,803,048` bytes
- SHA-256: `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`
- ORIL VA base: `0x635C6000` (`PA = VA - 0x60000000`, physical `0x035C6000`)
- Existing offline verifier & patch model: `tools/research/a6400_open_gate.py`, `tests/test_a6400_open_gate.py`
- Existing evidence: `docs/evidence/0720-a6400-squeezed-open-gate-a7iii-port-mapping-and-unicorn-verification.md` and `docs/evidence/0721-a6400-squeezed-open-gate-live-ram-preflight-and-test4.md`

Physical ILCE-6400 (`CXD90045` / `IMX284`) live RAM experiments (`E0721`) proved this exact boundary in 4K 24p/25p (`mode 16 = 0x1f`, `mode 17 = 0x89`):
1. `test1` (`reglist` col 11 ONLY: `Y_START=30 (%4==2)`, `Y_SIZE=4020`, FE & timing stock) -> **PASS** (normal colors, smooth FPS, no reboot).
2. `timing4` (`timing_rom` + `rt_t14` ONLY: `24p=(824,1820)`, `25p=(791,1820)`, sensor & FE stock) -> **PASS** (smooth FPS, no reboot).
3. `shrink` (`dy = -160` across ALL 158 entries: `reglist` `Y_START=422, Y_SIZE=3236`, 66 vertical FE fields incl. `+0x0fa` & `+0x20e=5544`, `V_ACTIVE=3216` in `size_rows+0x3c`/`rt_t14+0x50`, 6 `rcv_desc` fields `0x63e19740`/`0x63e19780` -> `3287`, timing stock) -> **PASS** (full smooth FPS, `S-Ele refresh_cnt = 0`).
4. `plus24` (`dy = +24` across ALL 158 entries: `Y_START=330, Y_SIZE=3420`, FE `3416`, `V_ACTIVE=3400`, `rcv_vtot=3471 < 3480 = VMAX*2`, timing stock) -> **FAIL** (low FPS, `S-Ele refresh_cnt = 27`, forced reboot at `PC 0x63907870` unless debug flag `PA 0x0434FD7E |= 0x40`).
5. `test4` (`dy = +160`, full 158 entries + timing `(824,1820)` / `(791,1820)`) -> **FAIL** (normal colors, low FPS, `refresh_cnt = 33`).

**Core Question to Solve:**
Why does `dy = -160` (`3232` FE / `3287` total) pass with `refresh_cnt = 0`, while even `dy = +24` (`3416` FE / `3471` total, still `< 3480 = 1740*2`) immediately triggers `S-Ele Refresh` (`refresh_cnt = 27`)?

## Hard Constraints (Follow `AGENTS.md`)
- **100% OFFLINE & READ-ONLY on hardware:** Do NOT touch USB, `tools/camera_debugger.py`, or any connected camera.
- **English-only repository policy:** Every file written or edited (evidence, TOML, code, comments) MUST be strictly in English.
- **Python environment:** Use system `python` (`C:\Python314\python.exe`, which has working `ctypes` and `unicorn`), NOT `.\venv\Scripts\python.exe` (broken `_ctypes` DLL).
- **Disassembly / RE tooling:** Use WSL `arm-linux-gnueabi-objdump`, `tools.research.symbol_index`, `tools.research.rea_wsl_batch`, and bounded Unicorn checks.
- **Map discipline:** When finished, create ONE consolidated evidence record `docs/evidence/0697-*.md`, update `docs/map/sources/imaging-pipeline.toml` (and add new entities/claims for `S-Ele`, `SGC1/SGC2`, `DmmConfig.bin`, `FE/RCV`), and run `python -m tools.research.knowledge_map build` and `check`.

---

## Swarm Architecture: Spawn 5 Parallel Research Subagents

Spawn these 5 subagents in parallel (each works read-only on its own subsystem and reports back exact VAs, struct layouts, call chains, and evidence snippets):

### Subagent 1: `sele-refresh-tracer` — `S-Ele Refresh` & `av-cam` Log Path
- **Scope:** `0x639077dc..0x63909b00` (`S-Ele Refresh` state machine) and `0x6394e384..0x6394ecc4` (`av-cam` UIPC logger).
- **Tasks:**
  1. Trace every code path that increments `refresh_counter` (`0x64367af0`) or sets `refresh_state` / `refresh_latch` around `0x639077dc..0x63909b00`.
  2. Find all callers of `Lost CAMOUT_V` (`0x63f2b2e7`), `RCV overflow` (`0x63f2b33f` — callers already seen near `0x63908aa0` and `0x63909a1e`), and `SDVGA_SRC overflow` (`0x63f2b360`), and map the exact RAM structs storing the last refresh reason (`refresh_latch`, `sgc2[hq, vq]` snapshot, overflow flags) so future live runs can read the exact cause directly from RAM without `alog`.
  3. Trace where `0x6394e4e4` (`0x635dd470` UIPC) delivers `av-cam` log lines on Linux (`kikilog` / `osal_ulogio`) and whether a live RAM ring or proc/dev node holds them.
- **Deliverable:** Exact VA/PA map of `refresh_state` / `refresh_latch` / error-cause bitfields and the exact condition that triggers `S-Ele Refresh` on frame height mismatch.

### Subagent 2: `sgc-timing-tracer` — `SGC1` / `SGC2` Hardware Timing & `rt_t10`/`rt_t14`/`rt_t18`/`rt_t1c`
- **Scope:** All `SGC` references (`39` strings in `av-cam.bin`), `ImagerDrvParam_IMX284` (`0x6379d1d8..0x6379d660`, `0x63ceff4e`, `0x63cf0018`, `0x635c8838`, `0x635c8a48`).
- **Tasks:**
  1. Map all consumers of `rt_t10` (`0x64200efc`, stride `0x30`, incl. `+0x12`), `rt_t14` (`0x6420128c`, stride `0x78`), `rt_t18` (`0x64201b74`, stride `0x34`), and `rt_t1c` (stride `0x12`).
  2. Locate `sgc1_param` / `sgc2_param` (analogous to A7 III `sgc1_param`) and trace which MMIO registers receive `HMAX`, `VMAX`, `CAMOUT_V` watchdog, and vertical active/total counts when entering 4K 24p/25p.
  3. Determine whether `SGC1`/`SGC2` has any hardcoded vertical threshold at `3392`/`3442`/`3447`/`3480` that breaks `plus24` (`dy=+24`), and whether `rt_t10 + 0x12` or `rt_t18` must be updated when `(h, v)` changes for `test4`/`test3`/`test2`.
- **Deliverable:** Complete `SGC1`/`SGC2` + `rt_t10/14/18/1c` consumer map with exact VAs and MMIO register contract.

### Subagent 3: `fe-rcv-tracer` — `fe_rec_0a`/`fe_rec_19` & `rcv_desc` (`0x63e19740`/`0x63e19780`)
- **Scope:** Callers of FE selectors `0x63771d78` & `0x63771de4` (`fe_rec_0a = 0x63e14e94`, `fe_rec_19 = 0x63e15e54`, `1344` B) and readers of `0x63e19740` / `0x63e19780` (`rcv_desc`).
- **Tasks:**
  1. Trace the function(s) that unpack the `1344`-byte FE record into hardware MMIO registers (`0xF300xxxx`/`0xF301xxxx`). Verify whether ANY field outside our 66 vertical offsets (e.g. a line-buffer limit, DMA stride/height, OB/margin, vertical phase, or `0x20e`/`0x268`/`0x2c8`/`0x2ce` companion) imposes a `<= 3392` or `<= 3442` cap.
  2. Trace all callers/readers of `0x63e19740` and `0x63e19780`, plus check if a second `rcv_desc` or companion table exists for `fe_rec_19` (right stripe) or `4K 24p/25p` (`mode 16/17`).
  3. Explain why `dy = -160` works cleanly with `refresh_cnt = 0` while `dy = +24` fails immediately.
- **Deliverable:** Field-by-field FE/RCV MMIO mapping and any missing vertical/buffer fields in `fe_rec` or `rcv_desc`.

### Subagent 4: `dmm-buffer-tracer` — `DmmConfig.bin` & RAW/FE Memory Pool Allocations
- **Scope:** `firmware/extracted_a6400/firmware.tar_unpacked/0800_appli/setting/DmmConfig.bin` (`77,108` B), `0x63ddf414` (`E0578`), and `av-cam.bin` DMM/buffer allocation routines for 4K 24p/25p (`6000×3376`).
- **Tasks:**
  1. Parse `DmmConfig.bin` structure and identify all pools used in 4K 24p/25p Movie mode (RAW input, FE stripe buffers, YCC/VPI buffers).
  2. Cross-reference `0x63ddf414` and any other DMM/buffer size tables in `av-cam.bin` that reference `6000`, `3376`, `3392`, `3442`, `3232`, or computed byte sizes (`6000 * 3376 * bpp`, stripe `3232 * 3442 * bpp`, etc.).
  3. Determine whether a fixed pool/slot size or hardware DMA end-address check overflows when `height > 3376`/`3392`/`3442` (`dy = +24`), and if so, identify exact RAM addresses to enlarge or redirect it (e.g. borrowing from unused still-burst headroom).
- **Deliverable:** `DmmConfig.bin` + `av-cam.bin` 4K buffer pool map, byte-size arithmetic vs `dy = +24/+160/+312/+624`, and exact patch candidates if DMM is the ceiling.

### Subagent 5: `imx284-reg-tracer` — `IMX284` Sensor Readout & SPI/I2C Window Transfer
- **Scope:** `C_ImagerCtrl_IMX284` (`0x6379c000..0x637a1000`), `reglist` (`0x6454c484`), register staging array (`0x645a1d00..0x645a2050`), and SPI/I2C send list (`0x6454cf80..`).
- **Tasks:**
  1. Verify that `reg 0x000a/0x000b` (`Y_START` @ `0x645a1fe8`/`0x645a1ed0`) and `reg 0x000c/0x000d` (`Y_SIZE` @ `0x645a1dac`/`0x645a1f24`) are transmitted unmodified to `IMX284` in 4K 24p/25p (`mode 16/17`).
  2. Check ALL 125 rows of `reglist` (`0x6454c484`, especially rows `0..124` where `col 11` differs from `col 0` or `col 12` — e.g. `row 12/13` `c0=0x046c=1132` vs `c11=0x0d44=3396`) and `0x6379f00c` / `0x6379eccc` / `0x6379ed1c` / `0x6379efb8` to determine whether `IMX284` has a **second** vertical window / end-row / OB / VMAX register (e.g. `Y_END`, `WIN_V`, `VMAX`, or `0x001d..0x0020`) that clamps actual sensor output at `3396` lines even when `reg 0x000c/0x000d` is increased!
  3. Confirm how `0x6379f00c` computes and writes `0x001d..0x0020` (`VMAX` / shutter) and whether `VMAX = 1740` (`3480` lines) or another sensor register caps readout at `3396` lines.
- **Deliverable:** Complete `IMX284` vertical readout & VMAX register transfer proof; confirm or rule out sensor-side clamping at `3396` lines.

---

## Coordinator Synthesis & Verification Phase (After All 5 Subagents Report)
1. **Cross-check all 5 reports** against the physical truth table (`test1=PASS`, `timing4=PASS`, `shrink(-160)=PASS`, `plus24(+24)=FAIL`). Eliminate any hypothesis that contradicts `shrink = PASS` or `plus24 = FAIL`.
2. **Identify the exact root cause** of the `> 3392/3442` ceiling (and any secondary requirements for `test4`/`test3`/`test2` up to `dy = +624`).
3. **Update offline model & tests (if a concrete fix is found):**
   - Update `tools/research/a6400_open_gate.py` & `tests/test_a6400_open_gate.py` with the newly identified contract(s) and Unicorn verification.
   - Run `python -m tools.research.a6400_open_gate --verify` and `python -m unittest tests/test_a6400_open_gate.py tests/test_sensor_tables.py tests/test_knowledge_map.py -q`.
4. **Update repository knowledge map & evidence (English-only):**
   - Write `docs/evidence/0722-a6400-open-gate-upper-ceiling-static-analysis.md` documenting all 5 subsystems (`S-Ele`, `SGC1/SGC2`, `FE/RCV`, `DmmConfig.bin`, `IMX284`), exact VAs, call chains, and the synthesized fix/next boundary.
   - Update `docs/map/sources/imaging-pipeline.toml` with the new entities/claims and `E0722`.
   - Run `python -m tools.research.knowledge_map build` and `python -m tools.research.knowledge_map check` (must exit 0: `knowledge map OK`).
5. **Do NOT touch the physical camera.** Present the final root cause, exact new RAM patch table (ready for a single-shot live test), and verification receipts.
