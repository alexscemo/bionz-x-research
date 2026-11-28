# OpenCode Swarm Mission Plan — Round 3: Why *Any* FE Field Raised Above Stock Stalls `CAMOUT_V` (`+0x002` vs `[1..66)` Isolation)

> **Target audience:** OpenCode coordinator agent using the Swarm plugin (5 parallel subagents + coordinator synthesis).
> **Mode:** **100% OFFLINE STATIC RE + BOUNDED UNICORN VERIFICATION ONLY.**
> **Repository constraint (`AGENTS.md` §4):** Every artifact, script edit, evidence doc, map edit, and commit message in this repository **must be strictly English-only**.

---

## 0. New Live Ground Truth (`E0724`, Do Not Re-Test or Re-Prove Closed Facts)

Since Round 2 (`E0723`), a live camera isolation & binary-search campaign (`E0724`) eliminated `R1` (`rcv_desc`), ruled out any numeric ceiling above stock, and narrowed the entire failure mechanism to an astonishingly sharp paradox inside `fe_rec_0a` (`0x63E14E94`) / `fe_rec_19` (`0x63E15E54`):

### 0.1 Proven Live Results (`E0721` + `E0724`)

1. **`plus8` (`dy = +8`, minimal Bayer-aligned upward step) FAILS:**
   - `Y=(338, 3404)`, `FE=(3450, 3400, 5832)`, `rcv_desc +8`, timing stock.
   - Result: `refresh_cnt = 39`, `side_cnt = 2`, `sticky = 0x01`, `rcv_ovf = 0`, `sdvga_ovf = 0`.
   - **Proved:** There is no numeric wall above stock (`3392..3480`); the upward ceiling is **strictly the stock geometry itself**.
2. **`rcv_desc` (`R1`) is REFUTED live:**
   - `plus24_norcv` (6 `rcv_desc` entries kept **stock**, `sensor + fe_rec + size_rows + rt_t14` `+24`): **FAILS** (`refresh_cnt = 44`, `rcv_ovf = 0`, `sdvga_ovf = 0`).
   - `plus24_rcvonly` (`sensor + 6 rcv_desc` `+24`, `fe_rec + size_rows + rt_t14` **stock**): **PASSES** at smooth full FPS (`refresh_cnt = 0`, `vfc` advancing `55323 -> 60253`).
3. **Live `sgc1_win` (`block +0x40..+0x42`, `PA 0x04339E38..0x04339E3A`) captured in Movie 4K 24p:**
   - MSC: `sgc=(858,1750)`, `win=(0x00, 0x00, 0x00)`.
   - Movie 4K 24p: `sgc=(862,1740)`, **`win=(0x01, 0x1d, 0x1b)`** (`29`, `27`) — small mode-dependent byte window offsets, **not** stock height values (`3376..3447`).
4. **Full 66-Offset FE Binary Search (`fe_rec_0a`/`fe_rec_19` + `sensor +24`, leaving `size_rows`/`rt_t14`/`rcv_desc`/`timing` stock):**
   - `plus24_fe [0..33)` (`+0x002..+0x1A0`): **FAIL** (`refresh_cnt = 20`)
   - `plus24_fe [0..16)` (`+0x002..+0x0AA`): **FAIL** (severe stutter / USB slow)
   - `plus24_fe [0..8)` (`+0x002..+0x032`): **FAIL** (`refresh_cnt = 53`)
   - `plus24_fe [0..4)` (`+0x002..+0x016`): **FAIL** (`refresh_cnt = 32`)
   - `plus24_fe [0..2)` (`+0x002, +0x00A`): **FAIL** (`refresh_cnt = 19`)
   - `plus24_fe [0..1)` (**ONLY `fe_rec + 0x002`: `3442 -> 3466`** + sensor): **FAIL** (`refresh_cnt = 17`, `rcv_ovf = 0`, `sdvga_ovf = 0`)
   - **`plus24_fe [1..66)` (exact complement: `+0x002` kept stock `3442`, all other 65 FE fields `+24` + sensor):** **ALSO FAILS** (`refresh_cnt = 22`, `rcv_ovf = 0`, `sdvga_ovf = 0`).

### 0.2 The Exact Question Round 3 Must Answer

Both subsets are **independently fatal**:
- Raising **just `fe_rec + 0x002`** (`3442 -> 3466`) while leaving `[1..66)` stock stops `CAMOUT_V` (`ref=17`),
- Leaving `+0x002` at stock `3442` while raising **`[1..66)`** (`+0x00A = 3392 -> 3416`, etc.) **also** stops `CAMOUT_V` (`ref=22`),
- While keeping **ALL** FE fields stock (`test1`, `timing4`, `plus24_rcvonly`) or moving ALL of them **down** (`shrink`, `dy=-160`) passes cleanly.

Therefore the failure is **NOT** a mismatch *between* `fe_rec + 0x002` and `fe_rec[1..65]`. Rather, **every FE stage whose programmed vertical count exceeds stock waits for lines that never arrive at FE** — meaning something **upstream of (or at the entry to) FE** is STILL capped at the stock line count (`3396` active / `3442` total lines), **even when `reglist` col 11 `0x000A..0x000D` is patched**!

We must pinpoint **in exact ARM/Thumb disassembly + MMIO writes**:
1. What hardware MMIO register(s) `fe_rec + 0x002` (`3442`) and `+0x00A` (`3392`) map to (computed addresses `0xF3xxxxxx + offset` in `0x63CD3xxx`/`0x63CD4xxx`, left open in `E0723` §5.4), AND
2. Why the FE input stream still stops at `3396` / `3442` lines (whether `IMX284` col 11 vs col 0 leaves an actual height/SLVS/OB/timing register untouched, OR `CAMIN` / `SLVS` receiver driver between sensor and FE crops/caps at `3396` / `3442`, OR `SGC2` gates `CAMOUT_V` via `0x6376910C`).

---

## 1. Hard Operational Constraints

1. **NO hardware / USB / camera commands.** Never invoke `camera_debugger.py`, `adb`, or USB scripts.
2. **Map & tooling hygiene (`AGENTS.md` §5):**
   - Query `C:\Python314\python.exe -m tools.research.knowledge_map query <term|VA|E-ID>` first.
   - Use **system Python `C:\Python314\python.exe`** on `artifacts/hardware/av-cam-camera-original.bin` (ORIL VA `0x635C6000`). Keep temporary scripts in scratch memory or gitignored scratch dir and remove before finishing.
3. **Strict evidence bar (`AGENTS.md` §4):** Every register write, struct field, or formula must cite exact **VA**, **file offset**, and **disassembly**. Separate **observed** vs **inferred**. Reject any candidate that contradicts `shrink`, `test1`, `plus24_rcvonly`, `plus24_fe [0..1)`, or `plus24_fe [1..66)`.
4. **Sequential write ownership:** Only the **coordinator** writes `docs/evidence/0700-*.md`, edits `docs/map/sources/imaging-pipeline.toml`, and runs `knowledge_map build`/`check` and `unittest`.

---

## 2. Swarm Topology — 5 Non-Overlapping Subagents

Launch all 5 subagents in parallel. Each owns a strictly disjoint code slice.

### Subagent 1 (`fe-002-mmio-tracer`): Exact Computed MMIO Destinations of `fe_rec + 0x002` (`3442`) and `+0x00A` (`3392`)
- **Scope:** Trace dataflow forward from `0x63771C78` -> `0x63CCF5A2` -> `0x63CCF5C6` -> descriptor object `+0x438..+0x658`, specifically tracing **`rec+0x002` (`3442`)** and **`rec+0x00A` (`3392`)** through `0x63CCBFD0`, `0x63CD3xxx`, `0x63CD4xxx`, `0x63768DBA..F0`, and `0x63CD463C`/`0x63CD4600`.
- **Specific questions to answer:**
  1. Recall `E0723` §5.4 flagged that computed `0xF3xxxxxx + field` MMIO addresses inside `0x63CD3xxx`/`0x63CD4xxx` were not resolved by a literal sweep. Disassemble `0x63CD2000..0x63CD7000` (and callers from `0x63CC0000..0x63CD2000`) to decode **every computed MMIO offset** (`[base + #off]`) written from the descriptor slots fed by `+0x002` (`3442`) and `+0x00A` (`3392`).
  2. Identify which hardware subunit (`CAMIN`, `FE` stage-0/1/2/3, `OB` clamp, `H/V` crop, `DMA`, `VSYNC` wait-line / IRQ trigger) owns the register programmed by **`fe_rec + 0x002` (`3442`)** vs **`fe_rec + 0x00A` (`3392`)**.
  3. Explain why raising **ONLY** `fe_rec + 0x002` (`3442 -> 3466`) without touching any of the other 65 FE fields (`+0x00A`, etc.) already halts `CAMOUT_V` (`ref=17`), and simultaneously why keeping `+0x002 = 3442` stock while raising `[1..66)` (`+0x00A = 3392 -> 3416`, etc.) ALSO halts `CAMOUT_V` (`ref=22`). What exact hardware stage sits immediately upstream of `fe_rec + 0x002`?

### Subagent 2 (`imx284-col0-vs-col11-tracer`): Full `reglist` Col 0 vs Col 11 Diff + Dynamic Sensor Writes
- **Scope:** Inspect ALL `125` rows of `reglist` (`0x6454C484`, comparing `col 0` full 3:2 still `6000x4000` vs `col 10` 4K30p vs `col 11` 4K24p), plus all callers of `0x6379D618` (the `reglist` applicator) and **every other function in `0x6379B000..0x637A3000`** that writes IMX284 SPI/I2C registers dynamically at mode switch or per-frame!
- **Specific questions to answer:**
  1. List EVERY row out of `125` where **`col 0`** differs from **`col 11`**, including pointer addresses `[ptr-2]` and staging values! (In Round 2 we verified `col 10` vs `col 11` = rows 10-13 only; we have NOT yet audited all `col 0` vs `col 11` differences or non-`reglist` writes.)
  2. Trace all callers of `0x6379D618` and surrounding sensor driver functions (`0x6379C000..0x637A3000`): does firmware overwrite, mask, clamp, or re-send `0x000A..0x000D` (or any readout height / `VMAX` / `WIN` / `SLVS` / `MIPI` / `OP` line register) **after** `reglist` is applied for Movie 4K 24p (`mode 16` / `col 11`)? Specifically verify whether patching `reglist` col 11 `0x000A..0x000D` (`test1` / `plus24_rcvonly`) actually changes the lines emitted by the sensor on the wire!
  3. Locate `0x000A..0x000D` consumers / `0x63F109E1` (`mode_to_col`) and check if ANY separate table (like camera-mode `reglist` or dynamic exposure/VMAX calculator in `0x6379xxxx`/`0x638xxxxx`) keeps transmitted active lines fixed at `3396` (`3442` total) despite `reglist` col 11 changes.

### Subagent 3 (`camin-slvs-receiver-tracer`): Upstream `CAMIN` / SLVS-EC / Sub-LVDS Receiver Between Sensor & FE
- **Scope:** Trace all hardware blocks and driver modules sitting **before `FE`** (receiving raw pixels from IMX284 into FE/RCV): search strings & code for `CAMIN`, `SLVS`, `MIPI`, `CSI`, `RCV`, `S-Ele` receiver config, `0xF310xxxx` / `0xF311xxxx` / `0xF3120xxx` / `0xF300xxxx` receiver base addresses, and check any non-`fe_rec` readers/tables (including checking whether any of the small unpatched `.data` clusters near `0x6454AB60`, `0x64547d20`, `0x644f9330`, `0x64022a40`, `0x64025b00` are read by code).
- **Specific questions to answer:**
  1. Locate the exact driver functions that configure the sensor-to-FE receiver (`CAMIN` / `SLVS` / `RCV` hardware MMIO registers, NOT `fe_rec` and NOT the unused `rcv_desc` ROM `0x63E19720` that `E0724` already proved inert!).
  2. Determine **which table or constant** programs the receiver's expected vertical line count / end-of-frame / crop / EMB-skip / OB-line count in Movie 4K 24p (`mode 16/17`). Remember `plus24_rcvonly` proved `rcv_desc` (`0x63E19720`) does NOT cause or fix the stall — so WHERE does the *real* receiver line count come from?
  3. Verify whether the real receiver stops passing lines after `3396`/`3442` lines (which would immediately pass `test1`/`plus24_rcvonly`/`shrink` yet stall BOTH `fe[0..1)` `3442->3466` AND `fe[1..66)` `3392->3416`!).

### Subagent 4 (`sgc2-trim-loop-tracer`): Full Disassembly of `0x6376910C` (`/8` Loop) & `SGC1` `win=(0x01, 0x1d, 0x1b)` Consumers
- **Scope:** Complete disassembly and caller/callee trace of `0x6376910C` (and nearby `0x63768xxx..0x6376Axxx`), plus tracking where `SGC1` `block[+0x40..+0x42] = (0x01, 0x1d, 0x1b)` (observed live in `E0724`) is produced (e.g., via `0x638B6124` `memcpy` or siblings) and how `0xF3120264`/`0xF312026C` interact with FE/SGC2.
- **Specific questions to answer:**
  1. Fully reconstruct `0x6376910C`: what hardware registers does it read, how does the `/8` decimation work, and what does `0xF312032C..0xF312035C` (`measured + trim`) actually drive (sync generator trimming vs frame-abort/gating)?
  2. Trace the source struct copied at `0x638B6124` where `(0x01, 0x1d, 0x1b)` comes from in Movie 4K 24p (`mode 16/17`): what do `0x1d` (29) and `0x1b` (27) mean physically (e.g. H/V phase/blanking offset?), and can they depend on FE line count?
  3. Determine conclusively whether `SGC1`/`SGC2` could possibly cause **both** `fe[0..1)` (`+0x002` alone) and `fe[1..66)` (`+0x00A..` without `+0x002`) to fail independently, or whether `SGC` is purely passive/downstream of an FE stall.

### Subagent 5 (`camout-commit-path-tracer`): Trace `0x639303D0` (`camout_vfc` Commit) & `0x639325C4` (`Lost CAMOUT_V`) Back to Hardware IRQ
- **Scope:** Disassemble `0x639303D0` (sole caller of `0x63907B40`, which updates `camout_vfc` `0x643679DC` from `p[1]`) and `0x639325C4` (sole caller of `0x63907D00` watchdog), plus find EVERY writer of `p[0]` and `p[1]` (`p = *(0x643691FC+4)`) and the IRQ/ISR/MMIO event feeding `CAMOUT_V`.
- **Specific questions to answer:**
  1. Trace `p = *(0x643691FC+4)`: where is `p[0]` incremented/updated, where is `p[1]` updated, and what exact hardware interrupt or MMIO status bit triggers `0x639303D0` (frame commit) vs leaves it un-committed so `0x63907D00` sees `*(0x643679DC) != p[0]`?
  2. Reconcile `Lost CAMOUT_V` firing on **both** `fe[0..1)` (`+0x002` `3442->3466`) and `fe[1..66)` (`+0x002` stock, `[1..66)` `+24`): confirm at the register/interrupt level why *either* half of FE requesting `> stock` lines prevents the CAMOUT vertical frame-commit path (`0x639303D0`) from completing!

---

## 3. Coordinator Synthesis Protocol

1. **Require bidirectional proof (must explain ALL 6 live outcomes simultaneously):**
   - `shrink` (`-160`) -> **PASS** (`ref=0`)
   - `test1` (`Y_SIZE=4020`, FE stock) -> **PASS**
   - `timing4` (timing `+160`, FE stock) -> **PASS**
   - `plus24_rcvonly` (`sensor + rcv_desc +24`, FE stock) -> **PASS** (`ref=0`)
   - `plus8` (`+8`), `plus24` (`+24`), `test4` (`+160`), `plus24_norcv` -> **FAIL** (`Lost CAMOUT_V`)
   - `plus24_fe [0..1)` (`+0x002` only) AND `plus24_fe [1..66)` (`+0x002` stock, rest `+24`) -> **BOTH FAIL** (`ref=17` and `ref=22`).
2. **Identify the exact missing upstream/sensor/FE contract:**
   - Either name the exact unpatched register/table/instruction (with VA + file offset) that keeps the FE input stream at stock `3396`/`3442` lines (and verify in bounded Unicorn if applicable), OR identify the exact architectural boundary if runtime-computed/external (`av-cam` vs `bootin`/micro).
3. **Update repository docs (strictly English-only):**
   - Write `docs/evidence/0725-a6400-open-gate-fe-input-cap-and-002-mmio-trace.md` (`AGENTS.md` §4 compliant).
   - Update `docs/map/sources/imaging-pipeline.toml` with `E0725` claims & updated `F03`.
   - Run & verify:
     ```powershell
     C:\Python314\python.exe -m tools.research.knowledge_map build
     C:\Python314\python.exe -m tools.research.knowledge_map check
     C:\Python314\python.exe -m tools.research.a6400_open_gate --verify
     C:\Python314\python.exe -m unittest tests.test_a6400_open_gate -q
     ```
   - Confirm zero non-ASCII/non-English content, remove temporary scratch files, and do **not** modify `a6400_open_gate.py` or `a6400_og_ram_table.h` unless the new fix is 100% proven offline and verified via `--verify`.
