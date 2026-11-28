# E0753 - ILCE-6400 Open Gate: `rec_fmt_tbl` Id Contract, Per-Id Geometry Descriptors, and the Step `A12` Patch Rule

> **Renumbered 2026-11-28:** drafted locally as `E0729`; that ID was taken on `origin/main` by an unrelated record, so this record is `E0753`. Cross-references were remapped (`E0729`→`E0753`, `E0730`→`E0752`, `E0731`→`E0754`, `E0732`→`E0755`); content is otherwise unchanged.

- **Date:** 2026-11-27
- **Scope:** 100% offline static reverse engineering and bounded Unicorn verification of the ILCE-6400 `av-cam.bin` recording-format selection chain (`0x63957ED8` -> `0x63956E88` -> `0x63956F38` -> `0x63763F54` -> per-id descriptor copies), plus the user-space `(3840, 2160)` carriers in `libmpr.so` / `libObj.so` / `viewUnified2.so`, the FE 3:2 geometry question, the LCD aspect question, and the VDF/`ZIMA_AVC` stride question. No camera, USB, `camera_debugger.py`, or `adb` command was executed.
- **Predecessors:** `E0720`-`E0728`. Live campaign `A1`-`A11` (recorded in `docs/OPENCODE_SWARM_OPEN_GATE_PLAN_ROUND5.md` §0) supplies the physical observations; this record supplies the offline mechanism.
- **Inputs (read-only):**
  - `artifacts/hardware/av-cam-camera-original.bin`, `16,803,048` B, SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`, ORIL base VA `0x635C6000` (`file_offset = VA - 0x635C6000`, `PA = VA - 0x60000000`).
  - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/{bin/im.elf,lib/libmpr.so,lib/libObj.so,lib/viewUnified2.so}`.
  - `firmware/extracted_a6400/firmware.tar_unpacked/0800_appli/setting/DmmConfig.bin` (77,108 B; contiguous-pair scan: `0` u16-pair and `0` u32-pair hits for `(3840,2160)`, `(3240,2160)`, `(3264,2160)`, `(2880,2160)`, `(4096,2160)`; movw/movt immediate sequences were not exhaustively scanned).
- **Tooling:** `capstone 5.0.9`, `unicorn 2.1.4`, `pyelftools 0.33` under `.\venv\Scripts\python.exe`; WSL `arm-linux-gnueabi-objdump` available. Scratch drivers: `artifacts/scratch/r5/{dis_avcam.py,xref_index.py,pc_tables.py,find_const.py,tables.py,unicorn_rec_fmt.py,unicorn_desc.py,vdf_dump.py,fe_diff.py,pair_scan.py,align.py,elf_probe.py,dis_elf.py}`. All are non-destructive in-process analyses of the reference images.

## 1. Verified Call Graph (observed, static + Unicorn)

```
0x63957ED8  (entry; no direct BL/BLX caller in the image)
  └─ bl 0x63956E88                              @0x63957EE6   (sole call site)
       ├─ copies request fields:  w = [src+4] -> [r4+4]  @0x63956E9E
       │                          h = [src+6] -> [r4+6]  @0x63956EA8
       ├─ bl 0x63CCB44A                                      @0x63956EFA
       ├─ bl 0x63763F54  (r0=handle, r1=&[r4+4], r2=&[r4+0x44],
       │                  r3=[r4+0x4C], stack0=&[r4+0x48])   @0x63956F38
       │    └─ searches rec_fmt_tbl @0x63E11750 for (w,h[,sec])
       │       miss -> writes -1 to *stack0, returns 1
       └─ stores: [r4+0x48] = id   (0, 19, 1, 20, ... or -1)
                  [r4+0xC0] = (id in {0,19,1,20})   "4K" flag
                  [r4+0xC1] = (id in {2,11,3,4,5,8,9,10,12,13,14,15,16})
                  [r4+0xC2], [r4+0xC8], [r4+0xC9], [r4+0xC3], [r4+0xC4] ...
  └─ bl 0x63763B4C  @0x63957EF0
  └─ bl 0x63956DFC  @0x63957F06   -> uses [r4+0x48] as table index (see §3)
  └─ bl 0x6395787C / 0x63957A90 / 0x63957D94 / 0x639583B8 / 0x6395859C ...
```

`xref_index.py` (Thumb `BL`/`BLX` plus ARM `BL`/`BLX` over every halfword/word offset)
reports `127,119` distinct call targets in the image and:

```
0x63956e88: 1 caller(s) ['0x63957ee6']
0x63763f54: 1 caller(s) ['0x63956f38']
0x63957ed8: 0 caller(s) []
```

- **Observed:** `0x63956E88` has exactly one call site and `0x63763F54` exactly one caller, matching the plan's premise.
- **Observed:** the function at `0x63957ED8` (which contains `0x63957EE6`) has **no** direct call site in `av-cam.bin`, and no absolute `0x63957ED8`/`0x63957ED9` pointer exists in the image. Its registration path (function-pointer table, UIPC dispatcher, or cross-body entry) is **unknown** at this boundary; see §10.

### 1.1 What the cluster is (observed)

The cluster is Sony's **movie `apl_mov_*` YC/APL configuration**, not a VDF/AVC encoder path:

- the miss log at `0x63F002DE` reads
  `"YC: apl_mov_get_table(core:%d): table not found (p..."` (string at `0x63F002DE`,
  `core:%d` at `0x63F002F4`), and `0x63CCB446` = `lsrs r0,r0,#0x1f` is the core selector
  used for that field;
- the debug dump routine `0x639583F4` prints `"PROC SIZE"` (`0x63F36DCB`),
  `"PROC FRATE"` (`0x63F36E0F`) and `"INTERLACE"` (`0x63F36E38`);
- the request fields are therefore **PROC SIZE x/y**, i.e. `w@+4`/`h@+6` are the requested
  process size copied verbatim, and there is **no command/message-id field** in the
  payload. A "UIPC command id" cannot be recovered from this structure; the plan's
  question 1 is ill-posed as written.

**Entry-path audit (parallel, not re-run here):** all static routes were tested and
excluded — `BL`/`BLX`, `B`/`B.W`, register-matched `ldr[pc]`+`add rD,pc`, `ADR`/`ADR.W`
+ `add.w pc`, self-relative `u32` entries, `movw`/`movt`, 123,796 image pointers, and the
same value in the other 799 firmware files. The remaining explanation is a runtime-written
slot or a run-time `base+index`, which can only be settled by reading the live `av-cam`
process for `d9 7e 95 63` — hardware access this plan excludes.

### 1.2 Graph status: which segments are verified, and the userspace link

The plan's deliverable asks for a verified chain *userspace -> UIPC -> av-cam ->
VDF/ZIMA_AVC/SDVGA*. Measured against the evidence in this record:

| segment | status |
| --- | --- |
| av-cam: request struct -> `0x63763F54` lookup -> id -> per-id descriptors | **verified** (§2, §3, Unicorn) |
| av-cam: descriptor/id -> `ZIMA_AVC` table | **verified** (six PC-relative references to `0x63E3B554` at `0x639DCF66`, `0x639DCFAA`, `0x639DD054`, `0x639DD1A4`, `0x639DD20A`, `0x639DD26A`) |
| av-cam: a second `(w,h)`-keyed record walk (`0x639D7E5E`, base `0x63E3645C`, stride `0x54`) | **verified** (§6.2) — proves the `+4/+6` record idiom, but consumes monitor sizes, not the 4K family |
| av-cam: descriptor/id -> `vdf_tbl` | **unverified**: the four base candidates have **zero** references under 16-bit `ldr+add`, 32-bit `ldr.w` literal, `movw/movt`, absolute `u32`, self-relative, `u16` halves, TBH, or `.data` slots |
| userspace `libmpr.so` -> message -> av-cam request struct | **not verified, and the stated mechanism is refuted** (below) |
| `0x63957ED8` entry edge | **not present in the static image** (§1.1, §1.3) |

**`libmpr.so` transports a size *id*, not a `(w,h)` pair (observed).** The function at
`0x272570` is not an "id accessor"; it is a `(w,h,kind) -> id` table search:

```
0x272570: push.w {r4,r5,r6,r7,r8,lr} ; sub sp,#0xd8
0x272578: mov r4,r1          ; w
0x27257a: mov r5,r2          ; h
0x27259e: ldrh r1,[r7,r2]    ; rec.w
0x2725a0: cmp  r1,r4 ; bne next
0x2725a8: ldrh r0,[r1,#2]    ; rec.h
0x2725aa: cmp  r0,r5 ; bne next
0x2725ae: ldrb r1,[r1,#4]    ; rec.kind
0x2725b0: cmp  r1,r8 ; bne next
0x2725ba: mla  r2,#0xc,r3,r1 ; ldr r2,[r2,#-0xd0] ; str r2,[r6]   ; *out = rec+8 (id)
```

and its caller `RecStartAvcHdContent` (`0x2B4F6C`) writes only the resulting id:

```
0x2b5012: adds r0,r7,#4      ; &request[+4]
0x2b5014: ldrh r1,[r6,#8]    ; w    = param+8
0x2b5016: str  r0,[sp]       ; arg5 = &request[+4]
0x2b5018: ldrh r2,[r6,#0xa]  ; h    = param+0xa
0x2b501a: ldrb r3,[r6,#0xc]  ; kind = param+0xc
0x2b501c: ldr  r0,[r5]
0x2b501e: bl   0x272570      ; (w,h,kind) -> id, stored at request+4
```

- **Observed:** the userspace request field at `+4` is the mapped picture-size id
  (`13`/`14` for 3840x2160 per the 18-entry map at `0x97A0AC`), not a u16 width. av-cam's
  request reader takes a **width** at `+4` and compares it against real sizes, so the
  libmpr request cannot be the structure `0x63956E88` consumes.
- **Observed:** a `strh`-pair scan of `libmpr.so` (pattern `strh [Rn,#4]` + `strh [Rn,#6]`,
  16- and 32-bit forms) returns 139 windows overall but **zero** in the `AviSeqRec`
  region `0x2A0000..0x2CFFFF`, so no code there packs a `(w,h)` message.
- **Conclusion:** the plan's root-cause statement "`(3840,2160)` in `[r5+4]`/`[r5+6]` is
  coming from Linux user-space (`im.elf`) over UIPC" is **refuted as stated** for
  `libmpr.so`. Either another producer exists (not identified) or the value is computed on
  the av-cam side. This is a premise correction, not a proof of a local origin.

### 1.3 Entry edge of `0x63957ED8`: exhaustive static negative (observed)

`reg_scan.py` tests every fixed-image encoding that could hold a function address:

```
target 0x63957ed8 in av-cam-camera-original.bin
  LE u32 0x63957ed8: 0 []
  LE u32 0x63957ed9: 0 []
  BE u32 0x63957ed8: 0 []
  BE u32 0x63957ed9: 0 []
  self-relative u32: 0 []
  u16 halves d87e9563: 0 []
  TBH table entries: 0 []
```

added to the parallel audit's negatives (`BL`/`BLX`, `B`/`B.W`, `ldr[pc]+add`,
`ADR`/`ADR.W`, `movw`/`movt`, 123,796 image pointers, and the other 799 firmware files).
The same scan for `0x63956E88` is also all-zero, which is expected (it is only reached by
the `bl` at `0x63957EE6`, a direct call the BL index does find). The `0x63957ED8` edge is
therefore **created at run time** (a pointer written into a `.bss`/`.data` slot by a
registration call, or a `base+index` computed at run time). No static target exists for a
registration scan to find, including the `libObj.so` size block at `0xE98B28` (which holds
`(640,480)`, `(1920,288)`, `(1920,424)`, `(1080,608)`, `(1080,810)` — sizes, not
pointers; `0x63957ED8`/`0x63957ED9` occur **0** times in every userspace library).

## 2. `rec_fmt_tbl` Contract (observed)

Table VA `0x63E11750`, file offset `0x84B750`, 16-byte entries, 8-byte aligned:

| idx | VA | w | h | +4 | +8 sec | +12 id |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | `0x63E11750` | 3840 | 2160 | 1 | 2 | **0** |
| 1 | `0x63E11760` | 3840 | 2160 | 1 | 3 | **19** |
| 2 | `0x63E11770` | 4096 | 2160 | 1 | 2 | **1** |
| 3 | `0x63E11780` | 4096 | 2160 | 1 | 3 | **20** |
| 4 | `0x63E11790` | 1920 | 1080 | 1 | 1 | 3 |
| 5 | `0x63E117A0` | 2048 | 1080 | 1 | 1 | 4 |
| 6 | `0x63E117B0` | 960 | 1080 | 1 | 1 | 5 |
| 7 | `0x63E117C0` | 640 | 480 | 1 | 1 | 6 |
| 8 | `0x63E117D0` | 768 | 480 | 1 | 1 | 7 |
| 9 | `0x63E117E0` | 1280 | 720 | 1 | 1 | 8 |
| 10 | `0x63E117F0` | 1440 | 812 | 1 | 1 | 9 |
| 11 | `0x63E11800` | 1440 | 920 | 1 | 1 | 10 |
| 12 | `0x63E11810` | 1440 | 1080 | 1 | 1 | 11 |
| 13 | `0x63E11820` | 960 | 540 | 1 | 1 | 12 |
| 14 | `0x63E11830` | 1440 | 540 | 0 | 1 | 14 |
| 15 | `0x63E11840` | 1920 | 540 | 0 | 1 | 15 |
| 16 | `0x63E11850` | 960 | 540 | 0 | 1 | 16 |
| 17 | `0x63E11860` | 720 | 240 | 0 | 1 | 17 |
| 18 | `0x63E11870` | 720 | 288 | 0 | 1 | 18 |
| 19 | `0x63E11880` | 0 | 0 | 0 | 0 | **-1 (sentinel)** |

`0x63763F54` disassembly (observed; `--start 0x63763F54`):

```
0x63763f54: push.w {r0,r1,r4,r5,r6,r7,r8,lr}   ; 5th arg (out ptr) at [sp,#0x20]
0x63763f58: mov r5, r1                          ; r5 = &{u16 w, u16 h}
0x63763f5c: mov r6, r3                          ; section selector
0x63763f5e: bl  0x63ccb446                      ; r0>>31 -> 0/1 (table class)
0x63763f6c: ldrh r3, [r5]                       ; w
0x63763f7c: ldrh.w r7, [table + idx*16]         ; entry.w
0x63763f80: cmp r7, r3 ; bne next
0x63763f84: ldrh.w r8, [r5,#2]                  ; h
0x63763f88: ldrh r7, [entry,#2] ; cmp ; bne next
0x63763f8e: ldr r7,[r4] ; subs r7,#1 ; it ne ; movne r7,#1
0x63763f90: ldrb.w r8,[entry,#4] ; cmp r8,r7 ; bne next
0x63763f9e: ldr r7,[entry,#8] ; cmp r6,r7 ; bne next
0x63763fa4: ldr r6,[entry,#0xc]                 ; id
0x63763fca: mov.w r6,#-1 ; movs r4,#1           ; not found
0x63763fd0: ldr r2,[sp,#0x20] ; str r6,[r2]     ; *out = id or -1
0x63763fea: mov r0,r4 ; pop.w {...,pc}          ; 0 = found, 1 = not found
```

The scan starts at index `ip = [0x641F2C38 + class*4]`. That slot is zero in the static
image; if firmware populates it with the table length `N`, the scan starts at entry `N`
(the sentinel), wraps to 0 and stops after one full circle. Both cases return the same
result for every case measured here.

### 2.1 Bounded Unicorn verification (`unicorn_rec_fmt.py`)

The lookup is executed in Unicorn over a copy of the reference image with a synthetic
request struct; the out pointer receives the id.

```
image: artifacts/hardware/av-cam-camera-original.bin size=16803048 sha256=dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209
stock tbl / key 3840x2160 sec2     -> id 0
stock tbl / key 3840x2160 sec3     -> id 19
patched tbl / key 3840x2160        -> id -1
patched tbl / key 3240x2160        -> id 0
stock tbl / key 3240x2160          -> id -1
stock tbl / key 4096x2160 sec2     -> id 1
stock tbl / key 4096x2160 sec3     -> id 20
```

- **Observed:** the section selector (the `r3` argument, `[r4+0x4C]` in the caller) selects
  the `id 0` vs `id 19` / `id 1` vs `id 20` variant of the same `(w,h)`.
- **Observed:** stock table + `(3840,2160)` -> id `0`; the A10-style table edit
  (`3840 -> 3240` at `0x63E11750` and `0x63E11760`) + the unchanged `(3840,2160)`
  request -> id `-1`. This is exactly the state `A10`/`A11` created.
- **Observed:** with the same patched table, a `(3240,2160)` request would match and
  return id `0` — i.e. the failure is a *key/table mismatch*, not an encoder limit.
- **Observed:** `(4096,2160)` returns id `1`/`20`, confirming the section word semantics
  (entry `+8` = 2 vs 3).

## 3. What an Id Indexes: Per-Id Descriptor Records (observed)

`0x63956DFC` (first callee after the lookup) indexes four per-id descriptor records
within its own body (a parallel audit reports eight across the whole `apl_mov` cluster;
only these four were re-verified here):

| copier | record size | table base (Unicorn-captured source register for id 0) | mechanism |
| --- | --- | --- | --- |
| `0x63763D00` | `0xAC` | `0x63E11D28` | `mla` + `memcpy` (`0x63AC7970`) |
| `0x63763D44` | `0xA0` | `0x63E0E664` | `mla` + `memcpy` |
| `0x63763D60` | `0x1A4` | `0x63E0F384` | `mla` + `memcpy` |
| `0x63763D1C` | `0x34` | `0x63E11890` | inline `ldm`/`stm`, no `memcpy` call |

**Layout note (observed):** the `0x34` stride table starts at `0x63E11890`, i.e. immediately
after the `rec_fmt_tbl` sentinel at `0x63E11880`. The two tables are contiguous, so a new
`rec_fmt_tbl` entry cannot be appended without relocating the stride table; the sentinel at
`0x63E11880` is the last usable slot.

`unicorn_desc.py` executes each copier for ids `0`, `1`, `19`, `18` and `-1`:

```
== desc_0xAC @ 0x63763d00 (record 0xac)
  id   0: source 0x63e11d28
    first16 words: 0x0 0x0 0x0 0x0 0x0 0x7c0 0x870 0x7c0 0x21c 0x1f0 0x87 0x2 ...
  id   1: source 0x63e11dd4
    first16 words: 0x0 0x0 0x0 0x0 0x0 0x840 0x870 0x840 0x21c 0x210 0x87 0x2 ...
  id  19: source 0x63e129ec
    first16 words: 0x0 0x0 0x0 0x0 0x0 0x7c0 0x870 0x7c0 0x21c 0x1f0 0x87 0x2 ...
  id  -1: source 0x63e11c7c
    first16 words: 0x10e00 0x1e00 0x1e00 0x18e00 0x10e00 0x200 0x80 0x0 0x28000 ...
== tbl_0x34 @ 0x63763d1c (record 0x34)
  id   0: first16 words: 0x1e00 0x1e00 0x1e00 0x1e00 0x10e00 0x1e00 0x1e00 0x18e00 0x10e00 0x200 0x40 0x0 0x28000
  id   1: first16 words: 0x2000 0x2000 0x2000 0x2000 0x10e00 0x2000 0x2000 0x18e00 0x10e00 0x200 0x40 0x0 0x28000
  id  -1: first16 words: 0x10 0xf002d0 0x0 0x1 0x11 0x12002d0 0x0 0x1 0x12 0x0 0x0 0x0 0xffffffff
== tbl_0xA0 @ 0x63763d44 (record 0xa0)
  id   0: source 0x63e0e664
  id  -1: source 0x63e0e5c4   (values 0x3c, 0x3c, 0x3d, 0x3, 0xb, 0x14, 0x1c, ...)
== tbl_0x1A4 @ 0x63763d60 (record 0x1a4)
  id   0: source 0x63e0f384
  id  -1: source 0x63e0f1e0   (values 0xc, 0x4, 0x8090a0b, 0x50607, ...)
```

Interpretation (marked by evidence class):

- **Observed:** id `0` (`3840x2160`, XAVC 4K) copies a `0xAC` record whose words
  `[5..11]` are `1984, 2160, 1984, 540, 496, 135, 2`; id `1` (`4096x2160`) copies
  `2112, 2160, 2112, 540, 528, 135, 2`. `1984 = 1920 + 64` and `2112 = 2048 + 64`
  (one SDVGA/ISP pipe width plus margin), `135 = 2160 / 16` (macroblock rows).
- **Observed:** the `0x34` record's leading words are byte strides: `7680 = 2 * 3840`
  for id `0`, `8192 = 2 * 4096` for id `1`.
- **Observed:** with id `-1`, all four copiers read the record *before* the table
  (`base - stride`) and return unrelated data (e.g. `0x10E00`, `0x1E00`, `0xF002D0`;
  the `0x34` copier reads `0x63E1185C`, inside the tail of `rec_fmt_tbl`, and yields
  strides `16 / 15729360 / 0 / 1`), i.e. the descriptor is replaced by garbage.
- **Inference (strong):** the full-screen horizontal stride tearing seen in `A10`/`A11`
  is the downstream consequence of id `-1`, principally through the `0x34` record whose
  leading words are the byte line strides (`7680 x4 = 2 * 3840`). That record becomes
  `16 / 15729360 / 0 / 1`, which is the only stride source identified in this chain.
- **Correction (parallel audit, `[r4+0xC0]`):** a per-callee disassembly search of the
  seven immediate callees (`0x63956DFC`, `0x6395787C`, `0x63957A90`, `0x63957D94`,
  `0x639583B8`, `0x6395859C`, `0x63957934`, each disassembled from its entry point) found
  **no reader** of `[struct+0xC0]`; the flag is written from the id but is not the stride
  selector. The stride tear is attributed to the id-indexed records above, not to the
  flag. A parallel audit reports the flag merely selects `7MOD`/`8MOD`
  (`[struct+0xD4]`/`[struct+0xD8]`; `+0xD8` is read at `0x63956E1E`), but that gating was
  not re-verified here.

## 4. Correction to the `A10` Patch Description (observed)

`docs/OPENCODE_SWARM_OPEN_GATE_PLAN_ROUND5.md` lists the last four `A10` entries as
"`(3840, 2160)` pairs". They are not all pairs:

| PA | VA | stock bytes | meaning |
| --- | --- | --- | --- |
| `0x03E11750` | `0x63E11750` | `00 0f 70 08 01 00 02 00 00 00 00 00` | `rec_fmt_tbl[0]` = (3840,2160,flag 1,sec 2,**id 0**) |
| `0x03E11760` | `0x63E11760` | `00 0f 70 08 01 00 03 00 00 00 00 00` | `rec_fmt_tbl[1]` = (3840,2160,flag 1,sec 3,**id 19**) |
| `0x03E784DE` | `0x63E784DE` | `00 0f 70 08` | width/height pair inside the display-size table at `0x63E784C0` (`0x63E784DE = 3840`, `0x63E784E0 = 2160`) |
| `0x04570198` | `0x64570198` | `00 0f 00 00 70 08 00 00 00 00 00 00 01 00 00 00 ...` | `{u32 3840, u32 2160, u32 0, u32 1, u32 1, u32 3, u32 5, u32 7, u32 8, ...}` record, preceded by a function-pointer array at `0x64570180` (`0x63D611AD`, `0x63D61B77`, `0x63D61B8B`, ...) |

Only the first two participate in the `0x63763F54` lookup. The other two are data
records and cannot by themselves change the returned id (proven in §2.1: the patched
table returns `-1` for a `(3840,2160)` key regardless of those two).

## 5. Step `A12` Candidate (derived, not yet tested on hardware)

**Rule (observed):** the request key `(w,h)` copied at `0x63956E9E`/`0x63956EA8` and the
table entry must agree; an unmatched key returns `-1` and every per-id descriptor copy
then reads out of range. Therefore a 3240-wide build must either keep the request at
`3840x2160` (and change geometry in the per-id descriptors) or move key and table
together.

**A12 (minimal, control-flow-safe):** `A10` **minus** the two `rec_fmt_tbl` writes
(`0x03E11750`, `0x03E11760`), i.e. 375 entries = `A9` + `0x03E784DE` + `0x04570198`.

| entry | VA | PA (RAM write) | file offset | action in A12 |
| --- | --- | --- | --- | --- |
| `rec_fmt_tbl[0]` w | `0x63E11750` | `0x03E11750` | `0x84B750` | **restore stock 3840** |
| `rec_fmt_tbl[1]` w | `0x63E11760` | `0x03E11760` | `0x84B760` | **restore stock 3840** |
| display size pair w | `0x63E784DE` | `0x03E784DE` | `0x8B24DE` | keep 3240 (data only, no lookup role) |
| `{u32 3840, u32 2160}` record | `0x64570198` | `0x04570198` | `0xFAA198` | keep 3240 (data only, no lookup role) |
| `A5` FE/rcv/size_rows/rt_t14 set | see `E0727`/plan §0 | | | keep (337 entries) |
| `A8`/`A9` VDF/`ZIMA_AVC` set | see plan §0 | | | keep (through 373 entries) |

- Expected from the offline model: id returns to `0`, `[r4+0xC0]` sets again, no
  out-of-range descriptor copy, so the `A10`/`A11` tearing should disappear while the
  display-table and `0x64570198` 3240 edits remain in place.
- Not changed by A12: the id-`0` `0xAC`/`0x34`/`0x1A4` records still describe
  `3840x2160` (`1984x2160` per pipe, `7680` stride), so a recorded MP4 is still
  expected to report `3840x2160`.

No user-space object changes in A12: the normal-order `(3840,2160)` keys and the
`libObj.so` tuple are `.rodata`/const pools; the writable `.data` copies (37 in
`libmpr.so`) are a catalogue that does not feed the transmitted size id (§6), so a
userspace-side key change would need an overlay or load-time hook and is deferred to A13+.

**A13 (geometry change, needs the descriptor pass):** keep `rec_fmt_tbl[0]` stock and
rewrite the id-`0` descriptor fields to the 3240 geometry:

| record | VA (id 0) | field | stock | 3240 candidate |
| --- | --- | --- | --- | --- |
| `0xAC` | `0x63E11D28 + 5*4` = `0x63E11D3C` | per-pipe width | 1984 | 1684 (`= 3240/2 + 64`) |
| `0xAC` | `0x63E11D40` | height | 2160 | 2160 (unchanged) |
| `0xAC` | `0x63E11D44` | per-pipe width copy | 1984 | 1684 |
| `0x34` | `0x63E11890 + 0..0x0C` | byte strides (`2 x align_up(w,64)`) | 7680 x4 | **6528** (`2 x 3264`), see §5.1 |
| `0x34` | `0x63E11890 + 0x10` | | 69120 | 58752 (`= 9 * 6528`) |
| `0x34` | `0x63E11890 + 0x1C` | | 101888 | unknown (needs the reader) |

The remaining `0x1A4` and `0xA0` fields, and the `101888` stride, are **unknown** — they
must be read by the VDF/AVC/SDVGA path before A13 is attempted.

### 5.1 Alignment evidence from the captured descriptors (observed + arithmetic)

All four captured id-`0`/id-`1` geometry constants are exact multiples of 64:

| field | id 0 (3840x2160) | id 1 (4096x2160) |
| --- | --- | --- |
| per-pipe width (`0xAC` `[5]`/`[7]`) | 1984 = 31 x 64 | 2112 = 33 x 64 |
| byte stride (`0x34` `[0..3]`) | 7680 = 120 x 64 = 2 x 3840 | 8192 = 128 x 64 = 2 x 4096 |

For a 3240-wide pipeline the naive substitutions are **not** aligned; Sony's own rule
(`align_up(w,64)`) is what the last row of the bullets below applies:

| candidate | per-pipe width | /64 | byte stride (naive `2w`) | /64 | stride as `2 x align_up(w,64)` |
| --- | --- | --- | --- | --- | --- |
| **3240** | 1620 | 25.31 (not 16-aligned either) | 6480 | 101.25 | **6528** (`2 x 3264`) |
| **3264** | 1632 (16-aligned) | 25.5 | 6528 | **102** | 6528 |
| **3200** | 1600 | **25** | 6400 | **100** | 6400 |

- **Observed:** every stock descriptor constant is 64-byte aligned, including the byte
  stride.
- **Observed (stride law):** the `0x34` leading word is `2 x align_up(width, 64)`. For
  widths that are already 64-aligned it degenerates to `2 x width`
  (`3840 -> 7680`, `4096 -> 8192`, `2048 -> 4096`, `960 -> 1920`, `640 -> 1280`,
  `768 -> 1536`), and the non-aligned id 9 / id 10 (`1440x812`, `1440x920`) both give
  `2944 = 2 x 1472`, i.e. the rounding is applied before doubling. Sources:
  `0x63E11890/18C4/1960/1994/19C8/19FC` (aligned ids) and `0x63E11A30/1A64/1A98`
  (id 8/9/10).
- **Observed (see §7):** Sony's own `vdf_tbl` uses the same rounding: `w=3240` carries
  `stride=3264`, and `1440 -> 1472`, `1620 -> 1664`. The 64-pixel rounding is a data-level
  convention, not a code mask.
- **Resolved:** for a 3240 build the `0x34` stride is `6528` (`2 x 3264`), not `6480`.
  This follows from the stock law and matches the native VDF row, so the earlier
  `6480`-or-`6528` ambiguity is closed.
- **Unknown:** whether the `0xAC` per-pipe width (`1984` for 3840, i.e. `1920 + 64`) is
  `active/2 + 64` or `round_up(active/2, 64) + 64`; the stock values are consistent with
  both (`1920 -> 1984`, `2048 -> 2112`), so 3240 gives `1684` or `1728` respectively.

**Rollback:** all three tables are in the `av-cam.bin` RAM image; the existing tooling
restores them from `artifacts/hardware/av-cam-camera-original.bin` by exact baseline
bytes (`tools/research/a6400_open_gate.py::apply_patches`/`revert_patches` refuse a write
whose current bytes differ from the expected baseline). `A12`/`A13` are RAM-only, so a
power cycle plus `putback` restores the stock image.

## 6. User-Space `(3840, 2160)` Carriers (observed)

`00 0f 70 08` (`u16 3840, u16 2160`, or `u32 0x08700F00`) occurrences, with ELF section
and flags (`elf_probe.py`):

| file | offset/VA | section | flags | note |
| --- | --- | --- | --- | --- |
| `libmpr.so` | `0x7d7450` | `.text` (`0x193560..0x95dd74`) | R-X | classifier literal pool (`GetAspectRatio` at `0x7d7304`) |
| `libmpr.so` | `0x7d8ce8` | `.text` | R-X | classifier literal pool (`SetRecAttribute` at `0x7d86ec`) |
| `libmpr.so` | `0x8fb0a8` | `.text` | R-X | classifier literal pool (`xavcIfo_DbGlobal.cpp` at `0x8faf80`) |
| `libmpr.so` | `0x8b2c30` | `.text` | R-X | **reversed order** (`70 08 00 0f`), one of the 5 `.text` copies in the table below |
| `libmpr.so` | `0x97a0ac`, `0x97a0b8` | `.rodata` (`0x95dd80..0xb86d6c`) | R-- | data tables |
| `libmpr.so` | `0xa6bbfc` | `.rodata` | R-- | data table |
| `libObj.so` | `0xe98bf6` | `.rodata` (`0xe39c88..0x118ed8c`) | R-- | `(1610,1078,3840,2160,2880,2160,3240,2160)` tuple |
| `viewUnified2.so` | `0x7d2da0`, `0x80facc` | `.rodata` (per section table) | R-- | UI/layout tables |

- **Corrected (writability):** the `(3840,2160)` value also appears in **reverse u16 order**
  (`70 08 00 0f` = height,width). `const_sections.py` classifies it:

  | form | `.data` (WA-, PT_LOAD 6) | `.text` | `.rodata` |
  | --- | --- | --- | --- |
  | `00 0f 70 08` (w,h) | 0 | 3 | 3 |
  | `70 08 00 0f` (h,w) | **37** | 5 | 1 |

  So there **are** runtime-writable copies: 37 in `libmpr.so .data`. The earlier statement
  that no writable carrier exists is refuted; what is true is that the *normal-order* keys
  and the `libObj.so` tuple are read-only.
- **Observed:** the `libmpr.so` `.rodata` hit at `0x97A0AC` is an **18-entry**
  `{u16 w, u16 h, u8 kind, u32 id}` map (12 B per entry, e.g. `{3840,2160,0,13}`,
  `{3840,2160,1,14}`, `{1920,1080,0,11}`), i.e. a picture-size -> encoder-id map. The ids
  (`11`-`14`) match the `ZIMA_AVC` family (§7.2).
- **Observed:** the `libObj.so` hit at `0xE98BF6` is inside a table of `(u16 w, u16 h)`
  pairs: `(1610,1078)`, `(3840,2160)`, `(2880,2160)`, `(3240,2160)`, `(0xFFFF,0xFFFF)`.
  `libObj.so` lists `3240x2160` as a UI size; `libmpr.so` does not.
- **Observed:** `im.elf` (21,868 B) contains **zero** occurrences of any of the five size
  pairs (u16 or u32 form); it mounts the filesystem, `dlopen`s `/usr/lib/libObj.so` and
  `sen.elf`, and reaches `libmpr.so` through `DT_NEEDED`.
- **Reported (parallel userspace audit, not all re-verified here):** the `.text` hits are
  classifier literals (`GetAspectRatio` `0x7d7304`, `xavcIfo_DbGlobal.cpp` `0x8faf80`,
  `mp4FileLib_Rec::SetRecAttribute` `0x7d86ec`); the `0x97A0AC` map is read by an accessor
  at `0x272570` (`AviSeqCommon.cpp`, miss code `0x32000002`); `0xA6BBFC` is the w/h field
  of a 10 x 24-byte metadata frame-size table at `0xA6BB28` consumed by
  `setMetaFrameSize` `0x614A58` via the id map `0xA6B910`; and the request builder
  `RecStartAvcHdContent` (`0x2B4F6C`, `AviSeqRec.cpp`) transmits only the mapped
  **picture-size id** at `request+0x04`, not a literal geometry. The first 0x60 bytes of
  `0x2B4F6C` were re-read here (they read `[r6+4]`/`[r6+0x2c]`, so the exact source field
  offsets are not confirmed at this boundary).
- **Inference:** `3240x2160` is **not representable** in any of the three `libmpr.so`
  maps, so a 3240 request would require an overlay/hook of read-only `.rodata` (plus the
  upstream setting), not a runtime `.data` write. Patching the writable `.data` catalogue
  alone would desync `FindProfileDataIndexForDubbing` without changing the transmitted id
  (parallel audit; not re-verified).
- **Observed:** `libmpr.so` is an `ET_DYN` ARM ELF whose first `PT_LOAD` maps
  `off 0x0 -> vaddr 0x0` (`filesz 0x00BFFAE4`, `flags 5`), so file offsets equal VAs for
  the text and rodata hits above; `.text` is `0x193560..0x95DD74`, `.rodata` is
  `0x95DD80..0xB86D6C`.
- **Observed (`pair_scan.py`, contiguous pairs only):**

  | file | `(3840,2160)` u16-pair | `(3240,2160)` | `(3264,2160)` | `(2880,2160)` | `(4096,2160)` |
  | --- | --- | --- | --- | --- | --- |
  | `libmpr.so` | 6 | **0** | 0 | 0 | 2 |
  | `libObj.so` | 1 | **1** | 0 | 1 | 0 |
  | `DmmConfig.bin` | 0 | 0 | 0 | 0 | 0 |

- **Observed:** the `libmpr.so` `.rodata` hit at `0x97A0AC` is inside a **12-byte record
  table** `{u32 packed(w,h), u32 flag, u32 id}`: `{3840x2160, 0, 13}`,
  `{3840x2160, 1, 14}`, `{1920x1080, 0, 11}`, ... — i.e. a size-to-encoder-id map. The
  ids (`11`-`14`) are the same family as the `ZIMA_AVC` records (§7.2).
- **Observed:** the `libObj.so` hit at `0xE98BF6` is inside a table of `(u16 w, u16 h)`
  pairs: `(1610,1078)`, `(3840,2160)`, `(2880,2160)`, `(3240,2160)`, `(0xFFFF,0xFFFF)`.
  So `libObj.so` already lists `3240x2160` as a valid UI size, while `libmpr.so` does not.
- **Inference:** the `(3840,2160)` request key is more likely produced from the
  `libmpr.so` size table (which has no 3240 entry) than from `libObj.so`, but the exact
  producer is still **unproven**.
- **Caveat:** the `.text` hits are byte-identical sequences inside constant pools; a
  parallel audit identifies them as classifier literals (`GetAspectRatio`, `SetRecAttribute`,
  `xavcIfo_DbGlobal.cpp`), which matches their position after `pop`/literal boundaries but
  was not re-verified instruction-by-instruction here.
- **Unknown:** which of these constants is the one copied into the UIPC request that
  reaches `0x63956E88` (§10). No writable carrier was found in the three libraries.

### 6.1 Complete `(3840,2160)` carrier inventory in `av-cam.bin` (observed)

The plan's `A10` inventory lists four sites and states that every pair was accounted for.
`pair_classify.py` finds **32** u16 `(3840,2160)` pairs:

| zone | count | VAs |
| --- | --- | --- |
| `rec_fmt_tbl` | 2 | `0x63E11750`, `0x63E11760` |
| recording-parameter family (previously unlisted) | **14** | `0x63E369F4`, `0x63E36A54`, `0x63E36BD4`, `0x63E36C38`, `0x63E36C9C`, `0x63E36D00`, `0x63E36D64`, `0x63E36DC8`, `0x63E37A48`, `0x63E37AAC`, `0x63E37B10`, `0x63E37B74`, `0x63E37BD8`, `0x63E37C3C` |
| `ZIMA_AVC` | 14 | `0x63E3B56C` ... `0x63E3C41C` |
| display/aspect cell | 1 | `0x63E784DE` |
| code/other | 1 | `0x6367F684` |

- **Observed:** 14 of the 32 carriers were not in the campaign's inventory, all inside
  `0x63E369D0..0x63E37C80`. Each record looks like
  `{u32 code (14/4/5), u16 w=3840, u16 h=2160, u32 -1, u32 variant}`, with `0x60`/`0x64`
  spacing (`0x63E369F0` -> `0x63E36A50` is `0x60`; `0x63E36BD0` -> `0x63E36C34` is `0x64`).
- **Observed:** that family has **no** absolute literal and **no** `ldr[pc]+add pc`
  reference; the full negative sweep (also 32-bit `ldr.w`, `movw/movt`, self-relative,
  `u16` halves, TBH, `.data` slots) is in §6.2. Its consumer is unknown.
- **Consequence:** the premise "every `(3840,2160)` pair in `av-cam.bin` was accounted
  for, therefore the key must come from userspace" does not hold. The carrier inventory
  is 32, and the unlisted family could carry the key on the av-cam side.

### 6.2 The unlisted family sits past a `0x16` terminator (observed)

The only nearby `(w,h)`-keyed walk is at `0x639D7E5E` (inside the ZIMA/encoder region).
It resolves its base from `ldr r6,[pc,#0x2ac]` at `0x639D7FBA` → literal `0x0045E49C` +
PC `0x639D7FC0` = **`0x63E3645C`**, and steps records by **`0x54`**:

```
0x639d7e5e: ldr r5,[pc,#0x3dc] ; add r5,pc ; adds r5,#0x18
0x639d7f68: ldr r2,[r5,#-0x18] ; cmp r2,#0x16 ; bne match
0x639d7f82: adds r2,r6,r3               ; r3 = index * 0x54
0x639d7f88: ldrh r0,[r2,#6]             ; rec.h
0x639d7f8a: cmp  r0,r1                  ; vs [obj+0x6EE]
0x639d7f8e: ldrh r1,[r2,#4]             ; rec.w
0x639d7f90: ldrh.w r2,[obj,#0x6EC]      ; vs object w
0x639d7fb6: adds r5,#1 ; adds r3,#0x54  ; next record
0x639d7fbe: ldr r2,[r6,r3] ; cmp r2,#0x16 ; bne match
0x639d8012: str.w r5,[r4,#0x7e8]        ; store matched record pointer
```

Replaying the walk (`walk_check.py`):

```
  i= 0 0x63e3645c head=0x6 w=1920 h= 540 ...
  ...
  i=15 0x63e36948 head=0x7 w=1440 h= 540 ...
  i=16 0x63e3699c head=0x16 w=   0 h=   0  <-- 0x16 marker
  (records after this are skipped by this walk)
```

- **Observed:** the walk terminates at index 16 (`0x63E3699C`, head `0x16`), so the
  `(3840,2160)` family starting at `0x63E369F0` (exactly `0x594 = 17 x 0x54` from the
  base) is **not** reached by it. `0x63E369A0..0x63E369EF` is 0x50 bytes of zero padding
  that separates the two lists.
- **Observed:** this walk is nevertheless a **second verified `(w,h)`-keyed record
  consumer** (the first being `rec_fmt_tbl`): it matches `[rec+4]`/`[rec+6]` and
  `[rec+0xC]`/`[rec+0x10]` against `[obj+0x6EC]`/`[obj+0x6EE]`/`[obj+0x700]`/`[obj+0x704]`
  and caches the record at `[obj+0x7E8]`. It proves the "+4/+6 width/height record" idiom
  is used by more than one subsystem, but its records are monitor sizes
  (1920x540, 1920x1080, 1280x720, 1440x540), not the 4K family.
- **Observed (negative sweep for the family base):** no reference exists under any
  modelled encoding — 16-bit `ldr+add pc` (0), 32-bit `ldr.w` literal (0), `movw/movt`
  (0), absolute `u32` LE/BE (0), self-relative `u32` (0), adjacent `u16` halves (0), TBH
  (0), and `.data` pointer slots (3 candidates; all are instruction-byte coincidences,
  e.g. slot `0x638116B4` is `ldr r3,[r5,#0x64]` + `str r3,[r4,#0x3c]`).
- **Observed (cross-body sweep):** a streaming scan of all **1,124** files under
  `firmware/extracted_a6400` finds **0** occurrences of `0x63E369F0`, `0x63DF19B0` or
  `0x63957ED8`, so no other body references the family, the VDF table, or the entry
  function either.
- **Observed (producer shape):** a `ldrh [rT,#4]` + `ldrh [rT,#6]` → `strh [rD,#4]` +
  `strh [rD,#6]` copy exists 100 times image-wide but **zero** times in the movie cluster
  (`0x63950000..0x63960000`) or the ZIMA region (`0x639D0000..0x639E0000`), so the
  `apl_mov` request struct is not filled by a local table copy.
- **Observed:** the packed `u32` for a 3240 key (`0x08700CA8`) occurs **0** times, so no
  code builds the 3240 `(w,h)` pair as a single immediate/literal either.

## 7. VDF `vdf_tbl` and `ZIMA_AVC` Geometry (observed)

### 7.1 `vdf_tbl` (24-byte records at `0x63DF199C`)

Layout (verified against `dis_avcam --bytes`): `u16 w @+0`, `u16 h @+4`, `u16 stride @+8`,
`u16 buffer_h @+0xC`, `u8 flag @+0x10`, `u32 vdf_id @+0x14`, all with 2-byte padding.
Seven entries, repeated as three byte-identical blocks (21 x 24 B, ends `0x63DF1B94`).

| # | VA | w x h | stride x buf_h | flag | vdf_id |
| --- | --- | --- | --- | --- | --- |
| 0 | `0x63DF199C` | 640x480 | 640x480 | 0 | `0x00240B2E` |
| 1 | `0x63DF19B4` | 1920x1080 | 1920x1080 | 0 | `0x00050B3F` |
| 2 | `0x63DF19CC` | 1440x1080 | **1472**x1080 | 0 | `0x00050B3F` |
| 3 | `0x63DF19E4` | 1620x1080 | **1664**x1080 | 0 | `0x00050B3F` |
| 4 | `0x63DF19FC` | **3840x2160** | 3840x2160 | 1 | `0x00000299` |
| 5 | `0x63DF1A14` | 2880x2160 | 2880x2160 | 1 | `0x00000299` |
| 6 | `0x63DF1A2C` | **3240x2160** | **3264**x2160 | 1 | `0x00000299` (= 665) |

- **Observed:** stock Sony data already contains `(3240x2160, stride 3264, flag 1, id 665)`,
  i.e. the 3:2 width the campaign is trying to reach is an existing Sony geometry whose
  stride is the 64-pixel rounding of the width (`3264 = 51 x 64`). The plan's
  `vdf_tbl[6] = (3240,2160,3264,2160)` is confirmed exactly (entry 6 from `0x63DF199C`).
- **Observed:** the 16:9 4K record is entry 4 `(3840x2160, 3840x2160, flag 1)`. Frame rate
  is **not** in this table: 4K 24p and 25p share entry 4.
- **Observed:** the stride column is `align_up(w, 64)` in every case where it differs from
  the width (`1440 -> 1472`, `1620 -> 1664`, `3240 -> 3264`); it never rounds to `3200`.
- **Inference:** the correct 3240 substitution is the *pair* `w=3240` + `stride=3264`.
  Patching only `w` while leaving stride 3840 would give a 7680-byte pitch for a 3240
  frame; the stock rounding is 3264, not 3200.

### 7.2 `ZIMA_AVC` (40-byte records at `0x63E3B554`)

`0x63E3B554..0x63E3CF94` holds **168** records of **40 bytes** (168 x 40 = 0x1A40; the
lookup loop bounds at `cmp r8,#0xa8`). The packed `(u16 w, u16 h)` pair sits at `+0x18`;
there is **no stride field**. All 168 widths are multiples of 16; heights are not
(180 / 270 / 360 / 540 / 1080 are stock).

- **Observed:** the table contains `14` `(3840,2160)` pairs (first at `0x63E3B56C`, then
  every `0x28`: `0x63E3B594`, `0x63E3B5BC`, `0x63E3B5E4`, `0x63E3B60C`, `0x63E3B634`, ...)
  and `50` `(1920,1080)` pairs, with **zero** `(3240,2160)`, `(2880,2160)` or
  `(3264,2160)` pairs.
- **Observed:** `ZIMA_AVC` has no 3:2 entry; the `A8`/`A9` edits necessarily patched the
  14 `(3840,2160)` pairs in place.
- **Observed:** `vdf_tbl` supports 3240 natively while the encoder table does not, so the
  two are not mutually consistent for 3240 in stock.
- **Reported (parallel, not re-verified):** the encoder parameter block has **no pitch
  field**, so an encoder handed a 3240 width may derive `3248 = align_up(3240,16)` while
  the buffer pitch is 3264 — a concrete A13 tearing risk.

### 7.3 Consistency matrix for a 3240x2160 A13 build

| table | VA | stock | 3240 target |
| --- | --- | --- | --- |
| `vdf_tbl` 4K entry 4 w / stride | `0x63DF19FC` (w), `0x63DF1A04` (stride) | 3840 / 3840 | 3240 / 3264 — **or** select stock entry 6 at `0x63DF1A2C`, which already is 3240/3264 |
| `ZIMA_AVC` 4K pairs (x14) | `0x63E3B56C + 0x28*n` (w/h at `+0x18`) | 3840x2160 | 3240x2160 (no new row; patch in place) |
| `rec_fmt_tbl[0]` key | `0x63E11750` | 3840x2160 -> id 0 | keep 3840x2160 (or move the request key with it) |
| per-id `0xAC` per-pipe width | `0x63E11D3C`, `0x63E11D44` | 1984 | 1684 (or 1728 if 64-padded) |
| per-id `0x34` byte stride | `0x63E11890 + 0..0x0C` | 7680 | **6528** (`2 x align_up(3240,64) = 2 x 3264`) |

- **Observed:** no mask/round of the horizontal size exists anywhere on the
  vdf -> descriptor -> encoder path; the rounding is pre-computed in the data. There are
  only two `movw #0xCA8` sites in the image (both object sizes/strides, not widths) and no
  `movw #0xCC0` at all.
- **Observed:** `0x6454388C` and `0x64570198` are static `.data` records with **no** code
  reference in `av-cam.bin` (no PC-relative `add`, no absolute literal); the only slots
  holding their addresses are `0x645C5AA8` and `0x645C70D4`, themselves unreferenced by
  code. Their consumer is unknown, so the `A10`/`A12` edit at `0x64570198` is inert as far
  as this analysis can prove.

### 7.4 Muxer (observed, `libmpr.so`)

`libmpr.so` contains the ISO-BMFF writer (`ftyp/moov/mvhd/trak/tkhd/edts/mdia/stsd/avc1/
avcC/mdat` fourcc set; XAVC `mdat` writer near `0x7E0A90`), and the trailer wrappers
delegate to undefined `ff4_l_vurec_*` imports (no `libff4*.so` in this tree). No
hard-coded `3840`/`2160`/`3240` constant appears on the box-writing path, so the container
writer is alignment-agnostic; the 16-alignment constraint lives in the H.264 SPS.
Whether `tkhd` and the SPS derive from a single value is **unproven**.

## 8. Display Size Table: `0x63E784DE` Is a Size-Normalisation Clamp (observed)

The table really starts at `0x63E784BE` (not `0x63E784C0`). It holds three 16-byte records
at `0x63E784BE`, `0x63E784CE`, `0x63E784DE`, each eight `u16` = four `(w,h)` cells in the
order **16:9, 4:3, 3:2, 1:1**:

| record | 16:9 | 4:3 | 3:2 | 1:1 |
| --- | --- | --- | --- | --- |
| `0x63E784BE` | 7952x4472 | 7072x5304 | 7952x5304 | 5304x5304 |
| `0x63E784CE` | 960x540 | 960x720 | 960x640 | 960x960 |
| `0x63E784DE` | **3840x2160** | 3840x2880 | 3840x2560 | 3840x3840 |

An 11-row x 6-column `(w,h)` ladder continues at `0x63E784EE` (element stride 4), and the
consumer indexes it as `6*a + b` (`mla r1,#6,r1,r2` at `0x638EDCC2`).

`pc_tables.py` finds exactly one consumer of the 16:9 cell at `0x63E784DE`: the function
`CaptureUtilityImageSize()` at `0x638EDE4C` (verified by the strings `"UtilImgSize"` at
`0x63F267BE`/`0x63F26800` and `"normalization Limit"` at `0x63F267D3`).

```
0x638ede4c: ldr  r3,[r1,#0x34]          ; aspect enum
0x638ede58: cmp  r3, #3
0x638ede5c: tbb  [pc, r3]               ; 0x638EDE60 = 0e 10 02 04  (4 aspects)
0x638ede86: tbb  [pc, r7]               ; 0x638EDE8A = 03 2b 2e 38 43 48  (SizeType 0..5)
0x638edeb2: ldrh.w r6,[r3, r5, lsl #2]  ; max_w from the selected family
0x638edeb8: cmp  r1, r6 ; bhi clamp    ; request wider than max -> clamp + log
0x638edee6: ldr  r1,[pc,#0x94]          ; literal for 0x63E784DE (family cell)
0x638edef4: blx  0x63ac7970             ; memcpy 4 bytes = (u16 w,u16 h)
```

- **Observed:** the table is a **maximum-size normaliser**. The cell is selected by the
  *aspect enum* (`[ctx+0x34]`, 4-way `tbb`) and the family by a `SizeType` (`tbb` 0..5),
  never by the incoming frame size; the requested `(w,h)` is then clamped against it.
- **Observed:** `0x63E784DE` is the **16:9** cell of the 3840 family — so the `A10` edit
  `3840 -> 3240` changed the 16:9 ceiling, not the aspect selection.
- **Inference (strong):** `A7`/`A10` could not change the LCD aspect: a 3:2 cell is only
  used when the aspect enum is the 3:2 index, and the enum lives in the caller's context,
  not in this table. This is the code-level explanation for the LCD staying 16:9 in
  `A5`-`A9`, and it matches `A11` (dropping the `0x63E784DE` patch changed nothing).
- **Observed (parallel):** `zima_disp_tbl` has **zero** static references in the image and
  is 58 records x 100 bytes; the Movie-mode LCD window/OSD aspect is a userspace
  (`im.elf`/`viewUnified2.so`, `_169` layout family) decision, so `3840 -> 3240` cannot
  change it automatically.
- **Unknown:** the only single-byte av-cam lever a parallel audit proposes is the aspect
  `tbb` at `0x638EDE60` (`0e -> 02`, i.e. force the 3:2 index); it is **inferred and
  untested**, and it interacts with the `rec_fmt_tbl` tear risk (§2).

## 9. FE Record Geometry: Why `A5` Used Stock `idx1`/`idx3` (observed)

FE record table base `0x63E12F14`, stride `0x540` (verified: `idx 6` = `0x63E14E94` =
`fe_rec_0a`, `idx 9` = `0x63E15E54` = `fe_rec_19`; `idx 1` = `0x63E13454`,
`idx 3` = `0x63E13ED4`).

`fe_diff.py` compares the stock 4K records against the `A5` sources field by field:

| comparison | differing u16 | delta `-616` (vertical family) | other |
| --- | --- | --- | --- |
| `fe_rec_0a` idx 6 vs idx 1 | 226 | 61 | 165 |
| `fe_rec_19` idx 9 vs idx 3 | 233 | 61 | 172 |

The same 61 fields drop by exactly `-616` in both stripes, i.e. `idx 1` and `idx 3` are
the same Sony mode recorded for the two stripes. Representative fields:

| offset | idx 6/9 (4K) | idx 1/3 (A5 source) | delta |
| --- | --- | --- | --- |
| `+0x000` | 3232 | 2880 | -352 |
| `+0x002` | 3442 | 2826 | -616 |
| `+0x008` | 3196 | 2836 | -360 |
| `+0x00A` | 3392 | 2776 | -616 |
| `+0x050` | 2016 | 2224 | +208 |
| `+0x060` | 1968 | 2180 | +212 |
| `+0x20C` | 6281 | 5038 | -1243 |
| `+0x20E` | 5818 | 4761 | -1057 |
| `+0x250`, `+0x254` | 848 | 694 | -154 |

Structure (observed from `0x63CCF5A2`/`0x63CCF5C6`):

- `fe_rec_0a` is a sequence of **20-byte source groups**; `0x63CCF5A2` expands one group
  into a `0x28`-byte destination rect: `0x63CCF582` copies `r0+0/r0+2` to `r1+0/+4`,
  then `0x63CCF58C` copies `r0+4..+6` and `r0+0xC..+0xE` to `r1+8/+0xC` and
  `r1+0x18/+0x1C`, i.e. 20 source bytes -> `0x28` destination bytes.
- `0x63CCF5C6` first `memcpy`s `0x438` bytes from `fe_rec+0x108` to the destination
  (verified: `mov.w r2,#0x438; add.w r1,r5,#0x108; blx 0x63AC7970`), then expands groups
  at `+0x00`, `+0x14`, `+0x28`, `+0x3C`, ...
- `+0x20C`/`+0x20E` behave as a **Q12 fixed-point pair**: across the 16 records,
  `+0x20E = 4096` (1.0) in 8 records and `+0x20C = 4096` in 4 records (e.g. idx 7, 10,
  13, 15), with `idx 6/9 = 1.533/1.420`. Treating them as pixel widths is not supported.

Width observations (u16 pattern counts over the whole image): `4896` x448, `5064` x31,
`6000` x148, `6024` x64, `6048` x47. So `5064` does occur as a byte pattern, but not in
the FE record table; a `5064` window is not a stock FE geometry.

- **Observed:** `A5` did not change a handful of horizontal constants — it substituted a
  complete, self-consistent Sony record (226 / 233 changed u16 per stripe, 61 of them the
  exact `-616` vertical family). That is why it produced `ref=0` where the partial edits
  (`A2`-`A4`) produced `ref=47`/`ref=36` and a black screen.
- **Observed:** the `+0x20C` `6281 -> 5038` change cited in the plan is part of that
  whole-record substitution and is consistent with a Q12 scalar, not a standalone width.
- **Inference:** deriving a `5064x3376` variant by scaling `idx 1`/`idx 3` would touch the
  same 226/233 fields with hand-computed values, i.e. a much larger unverified edit than
  reusing Sony's constants. Recommendation: keep `4896x3376` (stock `idx1`/`idx3`) for
  `A12`, and treat exact-3:2 as a separate derivation with a cheaper first experiment:
  patch only `size_rows[16]` + the `rcv_desc` horizontal block and leave the FE record
  stock, which tests whether the FE record is authoritative at all.
- **Unknown:** the physical unit of the `+0x000`/`+0x008` values and of the `2826`/`2776`
  vertical fields (they are not the sensor line count `3396`/`3376`). A parallel audit
  reads `6000` = active sensor columns, `6024` = readout window (`m_left+H+m_right`,
  `6024` everywhere) and `6048` = H period, with `4896` as Sony's own mode-15 window;
  those identifications are **not** re-verified here.

## 10. Explicit Unknowns / Next Boundary

1. **Entry path of `0x63957ED8`.** Exhausted statically: `BL/BLX`, `B/B.W`,
   `ldr[pc]+add pc`, `ADR/ADR.W`, `movw/movt`, absolute `u32` (LE/BE, Thumb bit set and
   clear), self-relative `u32`, adjacent `u16` halves, TBH jump tables, 123,796 image
   pointers, every userspace library, and the other 799 firmware files (§1.3). The edge is
   created at run time. Settling it needs a live watchpoint on `0x63957ED8` (or a read of
   the `av-cam` process memory for `d9 7e 95 63`), which is hardware access outside this
   plan; "which UIPC command populates the request" is also **ill-posed**, because the
   structure carries no command-id field (§1.1).
2. **Producer of the `(3840,2160)` request.** The `libmpr.so` path is now **ruled out as
   stated**: its builder converts `(w,h,kind)` to a picture-size **id** and writes the id
   at `request+4` (§1.2), and no code in the `AviSeqRec` region packs a `(w,h)` pair. The
   av-cam carrier inventory is also incomplete (32 pairs, 14 previously unlisted, §6.1),
   so the "must come from userspace" argument is not supported. The producer is **unknown**
   and must be found by a live breakpoint on the reader (`0x63956E9E`/`0x63956EA8`) or on
   `0x63957ED8`.
3. **Movie-mode LCD/EVF aspect.** The av-cam side is a size *normaliser* selected by an
   aspect enum and a `SizeType` (§8); it cannot switch the preview aspect by itself. The
   actual Movie-mode window/OSD aspect is a userspace `_169` layout decision in
   `im.elf`/`viewUnified2.so`, and the enum value that Movie 4K 24p passes is **unproven**.
   The proposed single-byte `tbb` edit at `0x638EDE60` (`0e -> 02`) is inferred and
   untested.
4. **`0x64570198` reader.** The `{u32 3840, u32 2160, ...}` record is preceded by code
   pointers at `0x64570180`; its consumer and field semantics are **unproven**.
4a. **Consumers of the 14-record family (`0x63E369F0`) and of `vdf_tbl`.** Both bases are
   unreachable under every modelled encoding: 16-bit `ldr+add pc`, 32-bit `ldr.w`
   literal, `movw/movt`, absolute `u32` (LE/BE, Thumb bit set/clear), self-relative
   `u32`, adjacent `u16` halves, TBH jump tables, and `.data` pointer slots (§6.2, §1.3).
   A streaming scan of all 1,124 files under `firmware/extracted_a6400` also finds zero
   references from any other body. The only nearby walk (`0x639D7E5E`) stops at a `0x16`
   terminator before the family.
   The next boundary is therefore either a **boot-time copy** (the tables are consumed at
   init and the runtime path uses a `.bss` copy, which a static image cannot show) or a
   **`base+index` computed from a value loaded elsewhere**. Both require a live watchpoint
   on the table range, i.e. hardware access outside this plan.
5. **`0x1A4` descriptor fields and the `101888` stride** are unread; they must be mapped
   before A13.
6. **Physical verification.** `A12`/`A13` have not been run on the camera in this
   record; the plan's operational constraints forbid hardware access here.

## 11. Cross-check of Parallel Strand Reports

All five parallel strands reported. Their load-bearing claims were re-tested here; the
table below separates what was confirmed, corrected, or adopted on report.

| strand claim | status in this record |
| --- | --- |
| `0x63F002DE` is an `"apl_mov_get_table(core:%d)"` miss log; the cluster is movie `apl_mov_*` YC/APL config | **re-verified** (§1.1: exact string at `0x63F002DE`, `core:%d` at `0x63F002F4`, `PROC SIZE`/`PROC FRATE`/`INTERLACE` strings at `0x63F36DCB`/`0x63F36E0F`/`0x63F36E38`) |
| request has no command id; `w/h` are "PROC SIZE x/y" | **re-verified** by the same strings; no id field in the 21-field copy |
| `[r4+0xC0]` never reaches a stride/width | **partially re-verified**: no reader of `[struct+0xC0]` found in the seven immediate callees; `[struct+0xD8]` is read at `0x63956E1E`. The reported `7MOD`/`8MOD` gating was **not** re-verified |
| the `0x34` record for id `-1` reads `0x63E1185C` (`16 / 15729360 / 0 / 1`) | **re-verified** (§3, this record's `unicorn_desc.py` captured the same source and words) |
| `fe_rec_0a` is 20-byte groups expanded to `0x28` rects, plus a `0x438` copy at `+0x108` | **re-verified** from `0x63CCF5A2`/`0x63CCF5C6` disassembly (§9) |
| `idx6->idx1` = 226 diffs with 61 at exactly `-616`; `idx9->idx3` = 233 | **re-verified** (`fe_stats.py`) |
| `+0x20C`/`+0x20E` are a Q12 pair with `4096 = 1.0` | **re-verified** (`+0x20E = 4096` in 8/16 records, `+0x20C = 4096` in 4/16) |
| `5064` is "nowhere in the image" | **refuted as stated**: `5064` occurs 31 times as a u16 pattern image-wide (but not in the FE record table, so it is still not a stock FE window) |
| entry path of `0x63957ED8` is statically unreachable | **adopted as reported** (audit not re-run; consistent with this record's own zero-caller result) |
| `disp_aspect_tbl` starts at `0x63E784BE`: three 16-byte records (16:9/4:3/3:2/1:1) plus an 11x6 ladder at `0x63E784EE` indexed `6a+b` | **re-verified** (byte dump; `mla r1,#6,r1,r2` at `0x638EDCC2`) |
| the consumer is `CaptureUtilityImageSize()` at `0x638EDE4C`, selecting by aspect enum (`tbb` `0e 10 02 04`) and `SizeType` (`tbb` `03 2b 2e 38 43 48`) | **re-verified** (strings `UtilImgSize` / `normalization Limit`; both `tbb` tables) |
| `zima_disp_tbl` has zero static references and is 58 x 100 bytes | **adopted as reported** (not re-run) |
| `vdf_tbl` starts at `0x63DF199C` with the 16-bit-with-padding layout and 7-entry blocks; entry 6 = 3240/3264/id 665 | **re-verified** (`tables.py struct 0x63DF199C 7 24 6`) |
| `ZIMA_AVC` starts at `0x63E3B554`, 40-byte records, `(w,h)` at `+0x18`, 168 entries | **re-verified** (byte dump; 168 x 40 = 0x1A40 ends at `0x63E3CF94`) |
| the per-id `0x34` stride is `2 x align_up(w,64)`, so 3240 -> 6528 | **re-verified** (id 9/10 `1440 -> 2944 = 2 x 1472`) |
| `0x6454388C` / `0x64570198` have no code reference and only unreferenced `.data` pointer slots | **re-verified** (`find_const.py`: one slot each; `pc_tables.py`: no `add rX,pc` target) |
| the nine userspace hits are file offsets == VAs in `PT_LOAD[0]`, 3 `.text` + 6 `.rodata` | **re-verified** (`elf_probe.py`, section table) |
| `3840x2160` in reverse u16 order has 37 **writable** `.data` copies in `libmpr.so` | **re-verified** (`const_sections.py`: 43 hits = 37 `.data` / 5 `.text` / 1 `.rodata`) |
| `im.elf` delegates and contains no size encoding | **re-verified** (`pair_scan.py im.elf`: 0 hits for all five pairs) |
| `0x97A0AC` is an 18-entry `{w,h,kind,id}` map read by `0x272570`; `0x2B4F6C` transmits a size **id**, not geometry; 3240 is absent from all maps | **re-verified in the opposite direction**: `0x272570` is a `(w,h,kind) -> id` search (§1.2), and `0x2B4F6C` stores that id at `request+4`; both were disassembled here |
| `av-cam.bin` contains only the four `A10` `(3840,2160)` sites | **refuted**: 32 u16-pair carriers, 14 of them in an unlisted family at `0x63E369D0..0x63E37C80` (§6.1) |
| `0x63957ED8` has no static reference | **extended**: absolute LE/BE, self-relative, adjacent halves and TBH are also all-zero (§1.3) |

## 12. Reproducibility

```
.\venv\Scripts\python.exe -m tools.research.a6400_open_gate --plan
.\venv\Scripts\python.exe -m tools.research.a6400_open_gate --verify
.\venv\Scripts\python.exe artifacts\scratch\r5\xref_index.py --rebuild 0x63956E88 0x63763F54 0x63957ED8
.\venv\Scripts\python.exe artifacts\scratch\r5\pc_tables.py --rebuild 0x63E11750 0x63E784DE 0x63E11D28 0x63E11890
.\venv\Scripts\python.exe artifacts\scratch\r5\tables.py recfmt
.\venv\Scripts\python.exe artifacts\scratch\r5\unicorn_rec_fmt.py
.\venv\Scripts\python.exe artifacts\scratch\r5\unicorn_desc.py
.\venv\Scripts\python.exe artifacts\scratch\r5\vdf_dump.py
.\venv\Scripts\python.exe artifacts\scratch\r5\fe_diff.py
.\venv\Scripts\python.exe artifacts\scratch\r5\fe_stats.py
.\venv\Scripts\python.exe artifacts\scratch\r5\pair_scan.py
.\venv\Scripts\python.exe artifacts\scratch\r5\strings.py "PROC SIZE" "core:%d"
.\venv\Scripts\python.exe artifacts\scratch\r5\dis_avcam.py --start 0x63956DFC --len 0x40
.\venv\Scripts\python.exe artifacts\scratch\r5\dis_avcam.py --start 0x63CCF5A2 --len 0x60
.\venv\Scripts\python.exe artifacts\scratch\r5\dis_avcam.py --bytes 0x63E784B8 --len 0x40
.\venv\Scripts\python.exe artifacts\scratch\r5\dis_avcam.py --start 0x638EDE4C --len 0x40
.\venv\Scripts\python.exe artifacts\scratch\r5\dis_avcam.py --start 0x638EDCBA --len 0x10
.\venv\Scripts\python.exe artifacts\scratch\r5\tables.py struct 0x63E11960 4 0x34 1
.\venv\Scripts\python.exe artifacts\scratch\r5\tables.py struct 0x63E11A30 2 0x34 1
.\venv\Scripts\python.exe artifacts\scratch\r5\tables.py struct 0x63DF199C 7 24 6
.\venv\Scripts\python.exe artifacts\scratch\r5\const_sections.py lib/libmpr.so 0x0F000870
.\venv\Scripts\python.exe artifacts\scratch\r5\const_sections.py lib/libmpr.so 0x08700F00
.\venv\Scripts\python.exe artifacts\scratch\r5\scan_wh.py av
.\venv\Scripts\python.exe artifacts\scratch\r5\scan_wh.py lib/libmpr.so
.\venv\Scripts\python.exe artifacts\scratch\r5\pair_classify.py 3840 2160
.\venv\Scripts\python.exe artifacts\scratch\r5\reg_scan.py 0x63957ED8
.\venv\Scripts\python.exe artifacts\scratch\r5\lit_scan.py 0x63E369D0 0x63E37C80
.\venv\Scripts\python.exe artifacts\scratch\r5\lit_scan.py 0x63DF1900 0x63DF1C00
.\venv\Scripts\python.exe artifacts\scratch\r5\ptr_into.py 0x63E369D0 0x63E37C80
.\venv\Scripts\python.exe artifacts\scratch\r5\ptr_into.py 0x63DF1900 0x63DF1C00
.\venv\Scripts\python.exe artifacts\scratch\r5\walk_check.py
.\venv\Scripts\python.exe artifacts\scratch\r5\scan_pair_copy.py 0x63950000 0x63960000
.\venv\Scripts\python.exe artifacts\scratch\r5\scan_pair_copy.py 0x639D0000 0x639E0000
.\venv\Scripts\python.exe artifacts\scratch\r5\tree_scan.py 0x63E369F0 0x63DF19B0 0x63957ED8
.\venv\Scripts\python.exe artifacts\scratch\r5\elf_xref.py lib/libmpr.so 0x272570 0x2b4f6c
.\venv\Scripts\python.exe artifacts\scratch\r5\dis_elf.py lib/libmpr.so 0x272570 0x80
.\venv\Scripts\python.exe artifacts\scratch\r5\dis_elf.py lib/libmpr.so 0x2b4ff0 0x70
.\venv\Scripts\python.exe artifacts\scratch\r5\align.py
.\venv\Scripts\python.exe artifacts\scratch\r5\elf_probe.py lookup lib/libmpr.so 0x7d7450 0x7d8ce8 0x8fb0a8 0x97a0ac 0x97a0b8 0xa6bbfc
```

Baseline regression check (unchanged tooling, all steps `putback=EXACT`):

```
VERIFY PASS putback: patches=152 Y=(342,3396) FE=(3442, 3392, 5818) t14_24p=(862,1740) t14_25p=(827,1740) putback=EXACT
VERIFY PASS test4  : patches=152 Y=(262,3556) FE=(3602, 3552, 6092) t14_24p=(824,1820) t14_25p=(791,1820) putback=EXACT
VERIFY PASS test3  : patches=152 Y=(186,3708) FE=(3754, 3704, 6353) t14_24p=(791,1896) t14_25p=(759,1896) putback=EXACT
VERIFY PASS test2  : patches=152 Y=(30,4020)  FE=(4066, 4016, 6888) t14_24p=(731,2052) t14_25p=(701,2052) putback=EXACT
VERIFY PASS shrink : patches=152 Y=(422,3236) FE=(3282, 3232, 5544) t14_24p=(862,1740) t14_25p=(827,1740) putback=EXACT
VERIFY PASS plus24 : patches=152 Y=(330,3420) FE=(3466, 3416, 5859) t14_24p=(862,1740) t14_25p=(827,1740) putback=EXACT
VERIFY PASS plus8  : patches=152 Y=(338,3404) FE=(3450, 3400, 5832) t14_24p=(862,1740) t14_25p=(827,1740) putback=EXACT
```

No tracked file was modified by the analysis; no camera, USB or storage write was
performed.
