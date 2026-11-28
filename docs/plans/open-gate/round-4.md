# OpenCode Swarm Mission Plan — Round 4: Closing `R5` (External `CAMOUT` Publisher), `R6` (`0xF3122000`), and Full 4000-Line vs 4K 24p Stock Contracts

> **Target audience:** OpenCode coordinator agent using the Swarm plugin (5 parallel subagents + coordinator synthesis).
> **Mode:** **100% OFFLINE STATIC RE + CROSS-BINARY / BOUNDED UNICORN VERIFICATION ONLY.**
> **Repository constraint (`AGENTS.md` §4):** Every artifact, script edit, evidence doc, map edit, and commit message in this repository **must be strictly English-only**.

---

## 0. Current Ground Truth (`E0725` + `E0726`)

Round 3 (`E0725`) and the subsequent live hardware test (`E0726`) retired almost every speculative theory and narrowed `F03` (`dy > 0` stalls `Lost CAMOUT_V`) to just **two** surviving candidates plus an unresolved question on full-resolution sensor height:

1. **Both FE halves converge on `+0x438`/`+0x43C`, but `R4` is REFUTED live (`E0726`):**
   - `E0725` showed `rec+0x002` (`[0..1)`) and `rec+0x190+4i` (`[1..66)`) converge on `+0x438`/`+0x43C` and noted that `rec+0x194` (`-> +0x428`, `0`) was unpatched while `rec+0x198` (`-> +0x42C`, `3441`) was patched (`R4`).
   - `E0726` tested **`plus24_r4`** (`plus24` + `fe_rec_0a+0x194` [`PA 0x03E15028`] and `fe_rec_19+0x194` [`PA 0x03E15FE8`] set `0 -> 24` with `noreboot`). Result in Movie 4K 24p: **no image at all (black screen)**, requiring a battery pull. **`R4` is dead.**
2. **SGC1/2/3 (`R3`), `rcv_desc` (`R1`), DMM, and numeric budgets (`3480`) are all REFUTED (`E0723`–`E0725`):**
   - SGC state is byte-identical in `plus24_fe[0..1)` (FAIL `ref=17`) and `plus24_rcvonly` (PASS `ref=0`).
   - `0x6454AB60` (`"3360..3488"`) was retracted — it is a struct byte-offset table read by `0x6376E518`.
3. **Only TWO candidates from `E0725` survive, alongside two static diffs we have not yet done:**
   - **`R5`:** The CAMOUT `(vfc, frc)` publish side (`p = *(0x643691FC+4)`, `p[0]/p[1]/p[8]`, callback `u32(0x645CAA40) = 0x639303D1`, and caller `0x639325C4`) has no direct ARM caller/writer traced inside `av-cam.bin`, **OR** the true sensor frame length (`IMX284` active lines emitted to receiver in 4K 24p vs 6000x4000 still) is still stock `3396`/`3442`.
   - **`R6`:** The SGC-adjacent receiver window at `0xF3122000`/`0xF3123000` (`+0x144`, `+0x148`, `+0x160`) written by `0x63799C8C` from `struct+0x10/+0x12`, reached only via `0x63799D04` (which had no static caller in the literal sweep).

---

## 1. Hard Operational Constraints

1. **NO hardware / USB / camera commands.** Never invoke `camera_debugger.py`, `adb`, or USB scripts.
2. **Map & tooling hygiene (`AGENTS.md` §5):**
   - Query `C:\Python314\python.exe -m tools.research.knowledge_map query <term|VA|E-ID>` first.
   - Use **system Python `C:\Python314\python.exe`**. Keep scratch scripts in gitignored `artifacts/scratch/0702/` and delete before finishing.
3. **Available local firmware tree (offline inputs, read-only):**
   - `artifacts/hardware/av-cam-camera-original.bin` (`16,803,048` B, SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`, ORIL base VA `0x635C6000`).
   - Unpacked ILCE-6400 firmware tree under `firmware/extracted_a6400/firmware.tar_unpacked` (plus `firmware/extracted_a6400/updater.img_unpacked` and `firmware/extracted_a6400a_v101/firmware.tar_unpacked`).
   - Reference docs/code: `docs/evidence/0695-*.md`, `docs/RELATED_REPOSITORIES.md`, `tools/research/a6400_open_gate.py`.
4. **Strict evidence bar (`AGENTS.md` §4):** Cite exact file paths, binary offsets, **VAs**, and **disassembly**. Separate **observed** vs **inferred**.
5. **Sequential write ownership:** Only the **coordinator** writes `docs/evidence/0702-*.md`, edits `docs/map/sources/imaging-pipeline.toml`, and runs `knowledge_map build`/`check` and `unittest`.

---

## 2. Swarm Topology — 5 Non-Overlapping Subagents

Launch all 5 subagents in parallel. Each owns a strictly disjoint artifact/subsystem slice.

### Subagent 1 (`r5-camout-publisher-hunt`): Locate Caller/Registrar of `0x645CAA40`, `0x639325C4`, and External Sub-Firmware (`R5`)
- **Scope:**
  - Inside `av-cam-camera-original.bin`: inspect the table/vtable/object containing `0x645CAA40` (`u32 = 0x639303D1`), `0x639325C4` (how its pointer reaches runtime dispatch), and ALL references across `.text`/`.rodata`/`.data` to `0x6393xxxx` / `0x645CAxxx` / `0x64369xxx` (e.g., GOT/table-base `ldr` + offset, Thumb `+1` pointers, C++ object init / global constructors `.init_array`).
  - Across `firmware/extracted_a6400/firmware.tar_unpacked` (e.g., `im.elf`, `bootin.elf`, `.ko` kernel modules, sub-micro/DSP firmware blobs): search for `CAMOUT`, `CAMIN`, `0x643691FC` / `PA 0x043691FC`, `0x645CAA40`, or the IPC / shared-memory / IRQ transport that delivers `(vfc, frc)` / frame-commit messages from sub-LSI / kernel driver into `av-cam.bin` (`LIRO`).
- **Specific questions to answer:**
  1. How are `0x639303D0` (`0x645CAA40`) and `0x639325C4` registered and dispatched at runtime (exact vtable/struct base and caller chain)?
  2. Where in the unpacked firmware (`av-cam.bin`, `.ko` drivers, `im.elf`, `bootin.elf`, sub-micro binary if present) is `(vfc, frc)` written into the `p = *(0x643691FC+4)` buffer, and does that publisher hold any copy of the stock vertical frame height (`3376`/`3392`/`3396`/`3442`/`3445`/`3447`)?

### Subagent 2 (`r6-receiver-f3122000-tracer`): Trace `0x63799D04` & `0x63799C8C` (`0xF3122000` / `0xF3123000`) (`R6`)
- **Scope:** Disassemble `0x63798800..0x6379AE00` in full (`0x63799C8C`, `0x63799D04`, and surrounding functions), find how `0x63799D04` (`0x63799D05` Thumb entry!) is entered via indirect call / jump table / C++ vtable / function pointer table, and trace `struct+0x10/+0x12` back to its ROM/RAM producer.
- **Specific questions to answer:**
  1. Note that in Round 3 `0x63799D04` had no direct `BL`; check Thumb entry `0x63799D05` in `.rodata`/`.data`/vtables, PC-relative `ADR` (`add rX, pc`), and function-table ranges!
  2. Disassemble `0x63799C8C` (`0xF3122000`/`0xF3123000` `+0x144`, `+0x148`, `+0x160`) completely: what bitfields are written, what units (`H`/`V`/pixels/lines?) does `struct+0x10/+0x12` hold, and what populates `struct+0x10/+0x12` (does it come from `size_rows`, `fe_rec`, `rcv_desc`, or an unpatched ROM table)?
  3. Determine whether `0xF3122000`/`0xF3123000` is a receiver/CAMIN window (`R6`) that could reject FE requests `> stock` when `struct+0x10/+0x12` remains stock, OR if `R6` is populated from something already patched or unrelated.

### Subagent 3 (`imx284-all-cols-and-still-height`): Full 13-Column `reglist` Map (`col 0..12`) & True 6000x4000 Sensor Height Control
- **Scope:** Dump ALL 13 active columns (`col 0..12`) across all 125 rows of `reglist` (`0x6454C484`), identify which column is actual 6000x4000 full-res 3:2 still (`E0725` found `col 0` had `Y_SIZE = 1132`!), and examine `0x6379B000..0x637A3000` + `0x6454CE48..0x6454E000` (including the 68 non-`reglist` staging records noted in `E0723` §4 and `0x63F109E1` `mode_to_col`).
- **Specific questions to answer:**
  1. For each column `0..12`, print `Y_START`, `Y_SIZE`, and any other differing rows (`row 10..13` vs **all** other rows), and match columns to modes via `mode_to_col` (`0x63F109E1` and callers) — **which column(s) actually represent 6000x4000 3:2 full-sensor readout (~4000 lines)?**
  2. Diff that true full-height `~4000`-line column (and the 1080p / `col 10` 4K30p / `col 11` 4K24p columns) across ALL 125 rows to find **every sensor register that changes between 2760p, 3396p, and 4000p**!
  3. Determine how IMX284 knows how many active vertical lines to output (`4000` vs `3396` vs `2780`) — why did `col 0` have `1132` (`4000` in some other register or packed differently?) and is there ANY secondary register or table besides rows 10-13 that must move on ILCE-6400?

### Subagent 4 (`fe-still-4000-vs-4k24p-diff`): Stock 4000-Line `fe_rec` (`idx 5/8/12`) vs `fe_rec_0a`/`fe_rec_19` (`idx 6/9`) & `0x6376E518` / `0x6376ECFC` Audit
- **Scope:** Read ALL 16 records of the FE table (`0x63E12F14`, stride `0x540`), especially **`idx 5, 8, 12`** (which `E0725` §3 found hold `+0x002/+0x00A/+0x190 = 4108/4024/4111` stock!) vs **`idx 6` (`fe_rec_0a`) & `idx 9` (`fe_rec_19`)**, AND trace all callers/inputs of `0x6376E518` (`0x6454AB60` struct-offset table) and `0x6376ECFC` (`0x6454AC44` 21-reg table).
- **Specific questions to answer:**
  1. Compare **`idx 5, 8, 12`** (full-res ~4000-line stock records) field-by-field against **`idx 6/9`** across ALL `0x540` bytes: what **other** fields besides our 66 vertical offsets differ between a working stock `4024`-line FE config and `4K 24p` (`3392`)? Specifically check:
     - `rec+0x190..+0x1E0` (the 21 dwords written by `0x6376ECFC`), including `rec+0x194` (`0`), `rec+0x1C8`/`+0x19C` (`50`/`9`), `rec+0x1D0..+0x1E0` (`+0x488/+0x48C/+0x408/+0x40C/+0x3C80`),
     - Any fields in `rec+0x000..+0x107` or `rec+0x1E4..+0x53F` (such as vertical scalars, start offsets, line-rate dividers, `FE` mode flags, `E0723` `+0x2C8`/`+0x2CE`, or the `0x6376E518` / `[r1+r3+0x3D8]` struct fields!).
  2. Trace `0x6376E518` (`called from 0x63CD0C66`, reading `[r1+r3+0x3D8]`) back to where `r1+0x3D8..` is populated: could any of those 19 dwords come from an **unpatched** table/constant or capped FE stage (`0x0D20..0x0DA0`, `0x150CC`)?

### Subagent 5 (`a7iii-vs-a6400-contract-diff`): Why Did A7 III (`og_m3_v2`) Work With 65 FE Offsets While ILCE-6400 Stalls?
- **Scope:** Inspect `docs/evidence/0695-*.md`, `docs/RELATED_REPOSITORIES.md`, `tools/research/a6400_open_gate.py`, and compare firmware structure against other unpacked Sony `CXD90045` bodies already present locally (`firmware/extracted_a6400a_v101/firmware.tar_unpacked`, `firmware/extracted_zve10_v203*/firmware.tar_unpacked` if unpacked). Also check `CXD90045` vs A7 III differences documented in repo notes/evidence (`E0477–E0509` via `knowledge_map query` first!).
- **Specific questions to answer:**
  1. From `0695` + local upstream docs/code: what exact patch set worked on A7 III (`og_m3_v2`), what hardware blocks differed between A7 III (`IMX410` + its `av-cam`) and ILCE-6400 (`IMX284` + `CXD90045`), and did A7 III have any sensor/receiver/S-Ele/VMAX/FE patch component that `a6400_open_gate.py` omitted, scaled differently, or could not map 1:1?
  2. Check ILCE-6400 vs `ILCE-6400A` (`extracted_a6400a_v101`) or `ZV-E10` (`extracted_zve10_v203_linuxnames`) `av-cam` / `im.elf` / `.ko` if relevant for identifying external `CAMOUT` modules or shared driver symbols.

---

## 3. Coordinator Synthesis Protocol

1. **Synthesize against ALL 16 live outcomes (`E0721`, `E0724`, `E0726`) + static facts (`E0722`, `E0723`, `E0725`):**
   - Must cleanly account for: `test1`/`timing4`/`plus24_rcvonly`/`shrink` PASS, `plus8`/`plus24`/`test4`/`plus24_norcv`/`plus24_fe[0..1)`/`plus24_fe[1..66)` FAIL with `Lost CAMOUT_V`, and `plus24_r4` black screen.
2. **Actionable decision gate:**
   - **If offline analysis identifies the exact missing unpatched register/table/sub-firmware field** (e.g., an IMX284 real-height register outside rows 10-13, a `0xF3122000` `R6` table, an `idx 5/8/12` FE field difference, or an `.elf`/`.ko` module holding the `R5` stock height copy), specify the exact VA/PA/module, verify offset math offline (and bounded Unicorn if in `av-cam.bin`), and propose the single decisive RAM test or read check.
   - **If offline analysis proves `R5` or `R6` lives outside static ARM code (`sub-LSI`/DSP/unclosed struct)**, name the exact binary boundary and list only the minimal `E0647`-governed per-address MMIO / `.ko` inspection needed next.
3. **Update repository docs & verify (strictly English-only):**
   - Write `docs/evidence/0727-a6400-open-gate-r5-r6-and-still-vs-4k-diff.md` (`AGENTS.md` §4 compliant).
   - Update `docs/map/sources/imaging-pipeline.toml` with `E0727` claims & updated `F03`.
   - Run & verify:
     ```powershell
     C:\Python314\python.exe -m tools.research.knowledge_map build
     C:\Python314\python.exe -m tools.research.knowledge_map check
     C:\Python314\python.exe -m tools.research.a6400_open_gate --verify
     C:\Python314\python.exe -m unittest tests.test_a6400_open_gate -q
     ```
   - Confirm zero non-ASCII/non-English content and remove `artifacts/scratch/0702/` temp scripts before finishing.
