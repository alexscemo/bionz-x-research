# E0754 - ILCE-6400 Open Gate Round 6: `avlog` Read Recipe, `pasp`/`tkhd` Findings, and the `3200`/`3264` Candidate Patch Sets

> **Renumbered 2026-11-28:** drafted locally as `E0731`; that ID was taken on `origin/main` by an unrelated record, so this record is `E0754`. Cross-references were remapped (`E0729`→`E0753`, `E0730`→`E0752`, `E0731`→`E0754`, `E0732`→`E0755`); content is otherwise unchanged.

- **Date:** 2026-11-27
- **Scope:** 100 % offline static reverse engineering plus bounded Unicorn execution of original
  instruction bytes over the reference `av-cam` RAM image, the ILCE-6400 user-space libraries and
  `DmmConfig.bin`. No camera, USB, `camera_debugger.py`, `adb`, service-mode, storage or network
  operation was performed. Every input was opened read-only.
- **Predecessors:** `E0753` (Round 5 synthesis: `rec_fmt_tbl` id contract, per-id descriptors,
  `vdf_tbl`, `ZIMA_AVC`, display normaliser, FE records) and `E0752` (live `A12`–`A19` dual-pipe
  results and the `.bss` baseline). This record supersedes specific `E0753` statements, listed in
  section 5.
- **Inputs (read-only):**
  - `artifacts/hardware/av-cam-camera-original.bin`, 16,803,048 B (`0x10064E8`), SHA-256
    `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`. ORIL base VA `0x635C6000`,
    `file_offset = VA - 0x635C6000`, `PA = VA - 0x60000000`, VA range `0x635C6000..0x645CC4E8`.
    The dump is a **pre-init** image: its whole `.bss` span (`0x64056000..0x644E6000`, 4,672 KiB) is
    zero, so no runtime counter can be recovered from it.
  - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/`
    (`lib/libmpr.so` `12,740,352` B SHA-256 `40a86215a4a03d30be0f12a492739898c509a5eb221586e774478c545b09bb12`,
    `lib/viewUnified2.so`, `lib/libObj.so`, `bin/im.elf`, `kmod/dmm.ko`, `sabin/sa_avce_*_zma.bin`,
    `share/app/image_cmn_169.uxc`, `share/app/image_EVF.uxc`).
  - `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin`
    (16,674,456 B, SHA-256 `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`) — a
    **different build** of the same body; its tables sit `0x12588` lower than the capture's.
  - `firmware/extracted_a6400/firmware.tar_unpacked/0800_appli/setting/DmmConfig.bin` (77,108 B,
    SHA-256 `3e68b4f482ba24fceea1b572c8e2c5ee132f3fdd93ade64e634df47448bd3abd`).
- **Tooling:** `.\venv\Scripts\python.exe` with `capstone 5.0.7`, `unicorn 2.1.4`, `pyelftools`;
  reused read-only `artifacts/scratch/r5/{dis_avcam,xref_index,pc_tables,tables,reg_scan,pair_scan,
  lit_scan,movw_scan,elf_probe,dis_elf,fe_diff}.py`. New drivers live under
  `artifacts/scratch/r6/{s1_avlog,s2-dualpipe,s3-vdf-dmm,s4-libmpr,s5-lcd-aspect}/` (gitignored).
- **Labels:** **Observed** = read from the bytes, a capstone decode, or a run that is reproducible
  with the commands in section 8. **Reported** = taken from `E0753`/`E0752` and not re-run here.
  **Inferred** = a conclusion the bytes support but do not prove.

---

## 1. `avlog`: the `0x6394E4E4` log sink and its exact read recipe

### 1.1 What the two functions are (Observed)

`0x6394EC5C` is the single image-wide `printf`-style text log entry. It is reached from **6,181**
branch sites (5,630 `BL` + 551 `B.W` tail calls); 5,597 resolve to a literal format string
(3,975 distinct). The four sites named in the Round 6 plan resolve as follows:

| plan site | actual branch | format literal | string |
| --- | --- | --- | --- |
| `0x637632AE` | `bl` at `0x637632B6` | `0x63EFFB93` | `YC: [Error] MemOpen (%X)` |
| `0x637632E6` | `b.w` at `0x637632EC` | `0x63F00102` | `YC: [Error] sapl_SetDmaAddrMEOut - MEMORY_MEPRIVATE` |
| `0x637635EA` | `bl` at `0x637635F2` | `0x63F00225` | `YC: [Error] sapl_SetDmaAddrOut - HME_COPY_SRC_ADDR is NULL` |
| `0x63761C84` | `bl` at `0x63761C88` | `0x63EFFB2D` | `YC: apl_mov_mem_GetImageAddressOffset(): small_ratio is ZERO` |

Observed accounting block (`--start 0x6394EC80 --len 0x48`):

```
0x6394ec84  ldr   r4,[pc,#0x3c]      ; literal 0x00A29D7A at 0x6394ECC4
0x6394ec92  add   r4,pc              ; r4 = 0x64378A10   (byte budget)
0x6394ec96  bl    0x6394E4E4         ; emit(level=1, buf, len)  -> unconditional
0x6394ec9a  rsb.w r3,r5,#0x9e00
0x6394eca0  add.w r3,r3,#0x1f0       ; r3 = 0x9FF0 - len
0x6394eca4  cmp   r2,r3 ; bhs skip   ; budget >= 0x9FF0-len -> stop duplicating
0x6394ecae  bl    0x6394E4E4         ; emit(level=3, buf, len)  -> budgeted duplicate
0x6394ecb4  adds  r3,#0x10           ; + 16-byte ring-slot header
0x6394ecb6  adds  r5,r3,r5
0x6394ecb8  str   r5,[r4]            ; budget += 0x10 + len
```

and the emitter (`--start 0x6394E4F4 --len 0x50`) allocates `len + 0x20` from a platform pool and
fills a 24-byte header, it does **not** write a static array:

```
0x6394e4f8  add.w r2,r2,#0x20        ; size = len + 0x20
0x6394e4fe  bl    0x635DD534         ; pool alloc(area=0x008F013A, &p, len+0x20, 0)
0x6394e510  strh  r3,[r4,#2]         ; +0x02 = 3   (category)
0x6394e512  strh  r2,[r4]            ; +0x00 = 2   (type = record)
0x6394e514  str   r6,[r4,#4]         ; +0x04 = level
0x6394e516  str   r5,[r4,#8]         ; +0x08 = len
0x6394e518  bl    0x6394D70C         ; timestamp at +0x10 (8 bytes from platform blob)
0x6394e524  blx   0x63AC7970         ; memcpy(p+0x18, src, len)
0x6394e532  bl    0x635DD470         ; hand the object to the consumer
```

**Consequence (Observed):** the `0x9FF0` figure is a **byte budget for level-3 duplication**, not a
`0x9FF0`-byte array in this image. There is no absolute `u32 == 0x64378A00`/`0x64378A10`, no `0x9FF0`
array base, and no reader of the budget other than the two logger sites.

### 1.2 State block, `.bss` proof, and the capacity (Observed)

| VA | PA | object |
| --- | --- | --- |
| `0x64378A00` | `0x04378A00` | `u32 magic` init `0x000000B5` (`0x6394E6BA`) |
| `0x64378A04` | `0x04378A04` | `u32 version` init `4` |
| `0x64378A08` | `0x04378A08` | `u32 ctx` |
| `0x64378A0C` | `0x04378A0C` | `u32 cleared` |
| `0x64378A10` | `0x04378A10` | **`u32 ring_bytes`** — the `0x9FF0` budget |
| `0x6434FC90` | `0x0434FC90` | `ImagingMain` context; `+0xEE = 0x6434FD7E` |
| `0x6434FD7E` | `0x0434FD7E` | `ImagingMain+0xEE`: bit `0x40` = noreboot, bit `0x02` = suppress |
| `0x64367AF0` | `0x04367AF0` | `u32` refresh counter = the campaign's `ref=NN` |
| `0x643679AC` | `0x043679AC` | `u32` side counter |
| `0x6444CE22` | `0x0444CE22` | log control block; `+0x06` = category mask |
| `0x6444CDAA` | `0x0444CDAA` | category table, 5 x `0x18` |
| `0x644B068C` | `0x044B068C` | log enable byte |
| `0x644B06C9` | `0x044B06C9` | log level byte |
| `0x645C61D0` | `0x045C61D0` | slot -> `0x6434FC90` (observed bytes `90 fc 34 64`) |
| `0x645C5A54` | `0x045C5A54` | slot -> `0x6434D99C` (`+0xF = 0x6434D9AB`) |
| `0x645C1820` | `0x045C1820` | slot -> `0x6444CE22` |

Writability is established from content and code (the dump has no section table): every object above
except the `0x645Cxxxx` slots lies in the all-zero span `0x64056000..0x644E6000`, and the block
dumps as 16 zero bytes. `0x6394EC3C` (`log_ring_reset`) zeroes `0x64378A10` and posts an 8-byte
all-zero record at level `0x80000000` (the control/event class).

`0x63907870` is a **deliberate fault**, not an accident (`--start 0x6390784A --len 0x2C`):

```
0x6390784a  ands r7,r7,#0x40        ; bit 0x40 = noreboot
0x6390784e  bne  0x63907872         ; noreboot -> skip the fault
0x63907856  ldr.w r3,[r8]           ; r8 = 0x64367AF0
0x6390785a  cmp  r3,#0xa
0x6390785c  bls  0x63907872
0x63907862  bl   0x6394EC5C         ; "FW: S-Ele Refresh ng force exception" (0x63F2B29B)
0x63907868  str.w r7,[r8]           ; refresh_cnt = 0
0x6390786e  str   r7,[r3]           ; side_cnt   = 0
0x63907870  str   r7,[r7]           ; r7 == 0 -> store to address 0 -> data abort
```

This matches `E0721` (the reboot trace names `PC 0x63907870`) and explains why `noreboot on`
(`PA 0x0434FD7E |= 0x40`) both suppresses the reboot and lets `ref` keep counting.

### 1.3 Record layouts (Observed for the message; the 16-byte slot size is proven, its field map is not)

```c
/* Platform-pool message object: alloc size = length + 0x20. All fields observed. */
struct avlog_msg {
    uint16_t type;        /* +0x00 : 2 = record, 0x0F = "allocation failed / dropped" */
    uint16_t category;    /* +0x02 : 3 on the printf path; other values for typed logs */
    uint32_t level;       /* +0x04 : 0 (untyped) / 1 / 3 / 0x80000000 (control) */
    uint32_t length;      /* +0x08 : payload bytes, <= 0x80 on this path */
    uint32_t unused;      /* +0x0C : never written by any emitter (heap residue) */
    uint8_t  timestamp[8];/* +0x10 : 8 bytes of a 0x20-byte platform time blob */
    uint8_t  payload[];   /* +0x18 : length bytes, NOT NUL-terminated */
};

/* Ring-slot view: the writer advances by 0x10 + length, so the slot header is 16 bytes.
   Field placement below is INFERRED; only the 16-byte size is proven. */
struct avlog_slot {
    uint16_t type; uint16_t category; uint32_t level; uint32_t length; uint32_t unknown;
    uint8_t  payload[];   /* at +0x10, NOT NUL-terminated */
};
```

The plan's "`+16` bytes: level/sub-id/timestamp/length" is **correct for the message** (level
`+0x04`, category `+0x02`, length `+0x08`, timestamp 8 bytes at `+0x10`) and **wrong for the slot**:
a timestamp cannot fit in 16 bytes together with the other three fields.

Payload encoding is the formatter output: plain ASCII with `%d`/`%x`/`%s` substituted, truncated to
128 bytes, no trailing NUL and no guaranteed newline.

### 1.4 `avlog` Part A — the always-safe page list (7 pages, 11 required `ro_page` calls = 1,456 bytes; 12 calls / 1,472 B with the optional slot)

`ro_page <PAGE_PA> <OFF> <LEN>` maps one 4 KiB page and returns at most 256 B.

| page PA | off | len | what |
| --- | --- | --- | --- |
| `0x04378000` | `0x0A00` | `0x20` | `avlog_state` (`0x64378A00..0x64378A13`) |
| `0x04378000` | `0x0100` | `0x20` | `0x64378100` init struct, handles `0x64378118/1C` |
| `0x0444C000` | `0x0D00` | `0x100` | category table `0x6444CDAA` |
| `0x0444C000` | `0x0E00` | `0x40` | control block `0x6444CE22`, mask at `+6` |
| `0x044B0000` | `0x0600` | `0x100` | enable byte `0x644B068C`, level byte `0x644B06C9` |
| `0x0434F000` | `0x0C00` | `0x100` | `ImagingMain` head `0x6434FC90` |
| `0x0434F000` | `0x0D00` | `0x100` | noreboot/suppress byte `0x6434FD7E` |
| `0x04367000` | `0x0900` | `0x100` | side counter `0x643679AC` |
| `0x04367000` | `0x0A00` | `0x100` | **`ref=NN` counter `0x64367AF0`** |
| `0x045C5000` | `0x0A00` | `0x10` | slot `0x645C5A54` |
| `0x045C6000` | `0x01D0` | `0x10` | slot `0x645C61D0` |
| `0x045C1000` | `0x0820` | `0x10` | slot `0x645C1820` (optional) |

Each page must also pass `tools/devmem_policy.py` and be confirmed as system RAM, as
`a6400_usb_debugd.c::klog_inspect` already does via `system_ram_contains`.

### 1.5 `avlog` Part B — the hypothesised `0x9FF0` stream (opt-in, falsifiable)

Base hypothesis: records start at `VA 0x64378A14` (`PA 0x04378A14`), last byte `0x64382A04`.
Eleven pages: `0x04378000 0x04379000 0x0437A000 0x0437B000 0x0437C000 0x0437D000 0x0437E000
0x0437F000 0x04380000 0x04381000 0x04382000` (176 `ro_page` calls at 256 B each).

Decode, with the exact-termination test that makes it falsifiable:

```
1. magic == 0x000000B5 && version == 4          else "logger not initialised"
2. budget = *0x64378A10; require budget <= 0x9FF0
3. if budget == 0 -> "stream empty / reset since boot"; stop
4. offset = 0; while offset < budget:
       hdr = 16 bytes at 0x64378A14 + offset
       type = u16(+0x00), category = u16(+0x02), level = u32(+0x04), len = u32(+0x08)
       reject unless type in {2, 0x0F}, len <= 0x80, offset + 0x10 + len <= budget
       emit(level, category, payload = bytes at +0x10 .. len)
       offset += 0x10 + len                    /* no padding, no wrap */
5. accept only if offset == budget exactly; else "ring: unlocated"
6. newest record = last accepted record (append-only, no wrap, no index field)
7. allowance: a level-3 allocation failure posts a payload-less 0x0F marker but the caller still
   adds 0x10 + len, so accept sum <= budget while reporting a dropped-marker count
```

If step 5 fails, the honest result is `ring: unlocated` — not zeros. The next step is then a live
read of the pointer written at `sp+4` by `0x635DD534` (`0x6394E4FE`) or a breakpoint on
`0x6394E4E4`, both of which are hardware work.

### 1.6 Which messages discriminate the `A13`/`A15`–`A19` failures (Observed strings, sites re-checked)

| failure class | strings | sites |
| --- | --- | --- |
| sensor/pipe not producing | `FW: No Read. cause:SGC1 is not valid` `0x63F2E38D`; `... MIPI bridge constraint` `0x63F2E453`; `... overflow countermeasure at HS#0` `0x63F2E47D`; `... waiting for DFE DDR power supply` `0x63F2E4B0`; `FW: [%s] SGC1 start failed...` `0x63F1D654`; `FW: IIF%d SGC1 act...` `0x63F0063E` | `0x6392E5BC`, `0x6392E726`, `0x6392E76A`, `0x6392E7AC`, `0x638B6658`, `0x63765A62` |
| key/descriptor mismatch (`A10`/`A12` class) | `YC: apl_mov_get_table(core:%d): table not found (...)` `0x63F002DE` | `0x63763FE6` |
| encoder stalled | `YC: [Error] sapl_mov_stop - TIMEOUT` `0x63EFFD12`; `FW: Sequence_Movie: APL_ERR:0x%02x apl_mov_start error!` `0x63F30256`; `... apl_mov_set_dma_addr error!` `0x63F30217`; `YC: [Error] MemOpen (%X)` `0x63EFFB93`; `... sapl_SetDmaAddrMEOut` `0x63F00102`; `... HME_COPY_SRC_ADDR is NULL` `0x63F00225` | `0x637625DC`, `0x6393B3CC`, `0x6393B394`, `0x637632B6`, `0x637632EC`, `0x637635F2` |
| timing overrun | `FW: VD_Sequence: YC out window overflow %dus (%dus)` `0x63F2F0EA`; `FW: VD_Sequence: overflow %dV => %dV %dus` `0x63F2F138` | `0x63932796`, `0x63932896` |

**Caveat (Observed negative):** `FW: Lost CAMOUT_V ...` (`0x63F2B2E3`), `FW: SDVGA_SRC overflow VD
Seq Refresh!` (`0x63F2B35A`) and `FW: SRCkick_forSDVGA acqId[%d]` (`0x63F2B8C8`) have **zero** static
references under 16-bit `ldr+add pc`, absolute `u32` LE/BE and self-relative scans, while the
neighbouring `RCHVkick acqId[%d]` resolves from `0x6390ACC8`. They are candidates, not proven
emissions.

Discrimination rule: `No Read`/`SGC1 start failed` present -> upstream; `apl_mov_get_table ... not
found` with no `No Read` -> key/descriptor mismatch; `sapl_mov_stop - TIMEOUT`/`MemOpen` -> encoder
side; `VD_*: overflow` -> timing at the configured height.

### 1.7 `avlog` is a new command; `alog` and `klog` read different regions (Observed)

- `tools/research/a6400_og_ram.c::cmd_alog` (lines 491-554) reads PA `0x03524FC0`, length `0x60040`,
  i.e. `[0x03524FC0, 0x035C5000)`. The av-cam image starts at PA `0x035C6000`; the regions are
  disjoint with one 4 KiB page between them, and the log state is `0x00E53A40` B higher.
- The kernel cmdline in `reports/a6400_hw_dump.txt` reserves `klog.addr=0x035A5000 klog.size=0x20000`
  and `alog.addr=0x03524FC0 alog.size=0x60040`; `E0721` recorded that `alog` held only `EVENTS`.
- Therefore `avlog` must implement section 1.4/1.5, not reuse `cmd_alog`'s base or key filter.

**Live contradiction to resolve (Reported, `E0752`):** the live read of `PA 0x04378A10` after a
Movie 4K 24p session returned `0`. Under the model above the budget advances on every level-3
duplicate, so a session that logs anything should leave it non-zero unless (a) the consumer clears it
externally, (b) logging is disabled upstream, or (c) the camera build differs from the captured
image. The two decisive reads are `PA 0x04378A00` (must be `B5 00 00 00`/`04`) and two reads of
`PA 0x04378A10` around a known `S-Ele Refresh` message.

---

## 2. Path A1 - 3:2 display from a `3840x2160` stream

### 2.1 `libmpr.so` has no `pasp` box (Observed negative)

| probe | result |
| --- | --- |
| byte string `pasp` in `libmpr.so` | 0 |
| byte string `psap` (the reversed storage form Sony uses for fourccs) | 0 |
| 32-bit constant `0x70617370` / `0x70736170` materialised by `movw`/`movt`/`mov.w`/literal pool/`ADR` | 0 |
| `pasp` anywhere in the 799 extracted files | 0 |
| `VPRF`, `hvc1`, `hvcC`, `hev1` anywhere in the tree | 0 |

Box types are stored **byte-reversed in `.text` literal pools**, not as strings, so `find("tkhd")`
returns zero for every box. Verified tag words: `0x00870700 = 0x746B6864` (`tkhd`), `0x0086FC6C =
0x66747970`-style layout for `trak`, `0x00870AE0 = 0x61766331` (`avc1`).

A `pasp` for a 3:2 display of a 16:9 raster would be 16 bytes
(`00 00 00 10 70 61 73 70 00 00 00 1B 00 00 00 20`, `hSpacing=27`, `vSpacing=32`), but inserting it
requires growing `avc1`, `stsd`, `stbl`, `minf`, `mdia`, `trak` and `moov` by 16 bytes and fixing
`stco`/`co64` offsets. That is a structural rewrite, not a constant patch, so **`pasp` is not a
usable lever on this body**.

### 2.2 Where the container display size is written (Observed)

| field | write site | provenance | stock |
| --- | --- | --- | --- |
| `tkhd` height (u32 16.16) | `0x00870738` `str r2,[r0,#0x64]` in `0x0087072C` | **argument r2**, verbatim | `0x08700000` (2160) *Inferred from ABI* |
| `tkhd` width (u32 16.16) | `0x00870746` `str r1,[r0,#0x60]` in `0x0087072C` | **argument r1**, verbatim | `0x0F000000` (3840) *Inferred* |
| `avc1`/`stsd` height (u16) | `0x00870A98` `ldrh r3,[r4,#0xa]` -> `0x00870A9A` `strh r3,[r7,#0x1a]` | **descriptor +0x0A** | `0x0870` |
| `avc1`/`stsd` width (u16) | `0x00870A94` `ldrh r3,[r4,#8]` -> `0x00870A96` `strh r3,[r7,#0x18]` | **descriptor +0x08** | `0x0F00` |
| Sony `PROF`/`VPRF` size | **does not exist** | — | — |

Byte check (`libmpr.so`, VA == file offset):

```
0x870730  d0 f8 d8 50  00 af  0d bb  42 66  08 22  c0 f8 38 21  01 26  11 4a  05 2b  01 66
                   ^ldr.w r5,[r0,#0xd8]      ^0x870738 str r2,[r0,#0x64]                    ^0x870746 str r1,[r0,#0x60]
0x870A90  f9 60  bb 60  23 89  3b 83  63 89  7b 83
                          ^ldrh r3,[r4,#8]  ^strh [r7,#0x18]  ^ldrh r3,[r4,#0xa]  ^strh [r7,#0x1a]
```

`0x0087072C` accepts only `kind == 5`, is idempotent-guarded by `model+0xD8 == 'vide'`, and never
reads the sample entry; `0x00870A74` never reads `model+0x60`/`+0x64`. The two are **independent
inputs**, and `avcC`/SPS are written afterwards (`0x00870C14`, `0x00870CD8`) without ever parsing a
VUI or an aspect ratio, so nothing here would reject a display size that disagrees with the SPS.

### 2.3 The A1 hook (SMOD v2, `.text` only) and the `pasp` fallback

Cheapest legal A1 package, coded stream untouched (`W=3840`, `H_display=2560 = 3840/1.5`):

| # | target | stock bytes | patched | effect |
| --- | --- | --- | --- | --- |
| 1 | `tkhd` height store `0x00870738` | `42 66` (`str r2,[r0,#0x64]`) | code cave: `r2 <- 2560<<16` then `str` | display height 2160 -> 2560 |
| 2 | `stsd` height store `0x00870A9A` | `7b 83` (`strh r3,[r7,#0x1a]`) | code cave: `r3 <- 0x0A00` then `strh` | sample-entry height 2160 -> 2560 |
| 3 | wrapper redirects | `0x00858260` `b.w 0x87072C`; `0x008582AA` `b.w 0x870A74` | retarget into the cave, tail-jump back | entry points for the caves |

Site 1 alone is the minimal "display-only" lie (players that trust `tkhd` show 3:2, strict parsers
still see 2160 in `stsd`/SPS). Site 2 must be added if the player prefers the sample entry; it also
makes the file internally inconsistent with the SPS, which is exactly what the ZV-E1 `L4-mp4size`
patch does. Both are `.text`, so both need `mprotect` + `__clear_cache` (`SMOD v2`), not a `.data`
poke. No `.data`/`.rodata` size constant exists to patch.

### 2.4 FE geometry: `5064x3376` (exact 3:2) versus `4896x3376` (`A5`)

`size_rows[16]` is a `u32` row at `0x63E25430` (PA `0x03E25430`, dump offset `0x0085F430`,
stride `0x4C`, 19 mode rows). Observed row: `3,0,4,24,4,4,12,6000,12,20,0,0,48,2,8,3376,8,2,1,3`,
i.e. `+0x14 m_left = 4`, `+0x1C H = 6000`, `+0x24 m_right = 20`, `+0x3C V = 3376`; invariant
`m_left + H + m_right = 6024` and `H period = 6048`.

`rcv_desc` mode-16 block at `0x63E1978E` (PA `0x03E1978E`, dump `0x0085378E`) is `u16`:
`32,48,6048,3396,10,3376,16,6000,0,1,1,1,1,2,3392,4,6024,3447`, i.e. `+0x0C = 16`,
`+0x0E H = 6000`, `+0x1E m_left = 4`, `+0x20 H+24 = 6024`.

| # | object | field | VA | PA | dump off | stock | 5064 | type |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `size_rows[16]` | `+0x14` | `0x63E25444` | `0x03E25444` | `0x0085F444` | 4 | **472** | u32 |
| 2 | `size_rows[16]` | `+0x1C` | `0x63E2544C` | `0x03E2544C` | `0x0085F44C` | 6000 | **5064** | u32 |
| 3 | `size_rows[16]` | `+0x24` | `0x63E25454` | `0x03E25454` | `0x0085F454` | 20 | **488** | u32 |
| 3b | `size_rows[17]` | same three | `0x63E25490/98/A0` | `0x03E25490...` | `0x0085F490...` | same | same (row 17 is byte-identical) | u32 |
| 4 | `rcv_desc` m16 | `+0x0C` | `0x63E1979A` | `0x03E1979A` | `0x0085379A` | 16 | **484** | u16 |
| 5 | `rcv_desc` m16 | `+0x0E` | `0x63E1979C` | `0x03E1979C` | `0x0085379C` | 6000 | **5064** | u16 |
| 6 | `rcv_desc` m16 | `+0x1E` | `0x63E197AC` | `0x03E197AC` | `0x008537AC` | 4 | **472** | u16 |
| 7 | `rcv_desc` m16 | `+0x20` | `0x63E197AE` | `0x03E197AE` | `0x008537AE` | 6024 | **5088** | u16 |

Arithmetic: `3376 * 1.5 = 5064` exactly; `472 + 5064 + 488 = 6024` (readout preserved);
centre `472 + 2532 = 3004` equals stock `4 + 3000`; `5064 mod 4 = 0` (Bayer phase). There is **no**
16/64 alignment law on this path — stock already violates 16 (`6024 mod 16 = 8`, `556/572 mod 16 =
12`) — so `5064 mod 16 = 8` violates nothing. `4896` is Sony's 16:9 mode-15 width (`4896x2760`), which
is why `A5`'s `4896x3376` measured 1.4502:1.

Must stay: `V = 3376`, `+0x30 = 48`, `rcv_desc +0x04 = 6048` (line period), the vertical
`rcv_desc` fields (`3396/3376/3392/3447`), the timing ROM (`h = 862`/`827`, `v = 1740`),
`rec_fmt_tbl` keys, `disp_aspect_tbl`, and the frame-buffer/DMM stride.

The FE record still contradicts 5064 if left stock: `idx 6 +0x24 = 3004 = 6000/2 + 4`;
`idx 9 +0x44 + +0xEC = 3168 + 2856 = 6024 = 6000 + 24`; `idx 1 +0x24 = 2452 = 4896/2 + 4`;
`idx 3 = 2784 + 2136 = 4920 = 4896 + 24`. A-prime (patch only rows 1-7, leave the FE records stock)
is therefore the decisive, cheap experiment: a correct 5064 picture proves the FE horizontal numbers
are derived; black or `ref != 0` proves they are authoritative and the full 226/233-field substitution
is required.

`.bss` question: `size_rows[16]` and `rcv_desc` are **initialised data** (present byte-identically in
the packaged `nflasha3` build at `0x63E12EA8` and `0x63E07206`), so both a file patch (that build's
offsets) and a RAM patch (this capture's `PA`) are possible. `rt_t14[16]` at `0x64201A0C`
(`PA 0x04201A0C`) **is** `.bss` (all zero in the dump) and is re-derived at boot by `0x63CF0018`; it
must not be patched.

---

## 3. Path A2 - native `3200x2160` / `3264x2160`

### 3.1 Why `1620` failed, and what the two aligned candidates change (Observed + arithmetic)

`0x63761C58(id, selector, flag)` returns, for `id` `0x13`/`0x14` only:

| selector | result |
| --- | --- |
| 1 | `W >> 1` (`1920` / `2048`) |
| 2 | `((W/2) / max(flag,1) - 0x20) << 3` |
| 3 | `(W >> 1) << 3` |
| other | `(W >> 1) - 0x80` |

`0x635D9BB0` is `__aeabi_uidivmod` (observed in Unicorn: quotient `r0`, remainder `r1`; 2,152 call
sites image-wide). **All three call sites pass `selector = 1`** (`movs r1,#1` at `0x63763342`,
`0x637633EC`, `0x63763582`), so selectors 2/3 are dead code and the divide never executes in stock.

The returned value is `W/2` — a byte offset for the **second pipe** — added to a record word and
stored: site `0x6376334A`/`0x637633F4` into `[0x641F1B50 + core*0x38 + 0x14]`, site `0x6376358A` into
`+0x30`. The per-word commit `0x63762E20` then stores each word into a memory-mapped register through
pure setters; e.g. `0x6375FD74` is

```
0x6375fd74  cmp r1,#1 ; beq 0x6375fd84 ; blo 0x6375fd80 ; cmp r1,#2 ; bne lr
0x6375fd86  str r0,[r3,#0x18]        ; r3 = pool value for the pipe
0x6375fd90  .word 0xF3851C00, 0xF3951C00, 0xF3A51C00   ; pipe 0 / 1 / 2 base
```

so word `+0x14` lands at **`0xF3851C18` / `0xF3951C18` / `0xF3A51C18`** (pool bytes
`00 1c 85 f3 00 1c 95 f3 00 1c a5 f3`; the `0xF3851xxx` page is already qualified live in `E0728`).
No mask, shift, compare or range check is applied to the value anywhere on this path — the code
cannot reject `1620`; only the hardware can.

Consequences:

- `1920` and `2048` are both multiples of 64; `1620` is `4 (mod 8)`; `1600` (3200) is `25 x 64`;
  `1632` (3264) is `51 x 32`.
- `.bss` at `PA 0x041F2AC8` staying `1984` is **expected and not evidence of an inert patch**: that
  copy is a `memcpy` of `desc_0xAC[id]` plus three field stores (`+0x30`, `+0x38`, `+0x58`) inside
  `0x6376309C`, and nothing there derives a width from `0x63761C58`.
- `A18` (per `E0752`) patched **both** width translators (`0x03761C64` and `0x03762F32`) to `3240`
  and `A19` patched only `0x03761C64`; **both were black** (`ref=18`/`20`). The only delta between
  the working left/right-split `A14` (`ref=10`) and the black `A19` is the `1620` offset at
  `0x63761C64`. The translator-inconsistency hypothesis is therefore **not** the explanation for
  `A18`; the `1620` offset itself is the variable that kills the pipeline.

`0x63762F32` still matters as a second width writer (it `strh`s the full width to `[r5]` and `[r5+2]`
for `id == 0`/`19`), and it must move together with `0x63761C64` in any width change.

**Constant sweep (Observed).** Over every halfword offset and every aligned literal word in
`0x63760000..0x63770000` there are only two width-relevant `3840`/`4096` writes
(`0x63761C64`/`0x63761C6A` and `0x63762F32`/`0x63762F44`); no `movw`/`mov.w`/`add.w` immediate and no
literal word equals `1984 (0x7C0)`, `3240 (0xCA8)`, `3264 (0xCC0)` or `3200 (0xC80)` anywhere in
that range, and `0x63950000..0x63960000` contains none of the watched constants at all. Two
unresolved `1920`/`2048` sites remain outside the DMA chain (`0x63765106` `mov.w sl,#0x780`,
`0x63764F3E` `add.w r2,r2,#0x800`) and must be excluded or included before a build is called
complete. Note also that `3240`/`3200`/`3264` are not encodable as `mov.w` immediates, which is why
`A18`/`A19` correctly used `movw`.

### 3.2 `vdf_tbl`: the `3240` row is stock and the selector is a class/threshold map (Observed)

`vdf_tbl` at `0x63DF199C` (24-byte records, three byte-identical blocks ending `0x63DF1B94`):

| # | VA | w x h | stride x buf_h | flag | vdf_id |
| --- | --- | --- | --- | --- | --- |
| 0 | `0x63DF199C` | 640x480 | 640x480 | 0 | `0x00240B2E` |
| 1 | `0x63DF19B4` | 1920x1080 | 1920x1080 | 0 | `0x00050B3F` |
| 2 | `0x63DF19CC` | 1440x1080 | 1472x1080 | 0 | `0x00050B3F` |
| 3 | `0x63DF19E4` | 1620x1080 | 1664x1080 | 0 | `0x00050B3F` |
| 4 | `0x63DF19FC` | **3840x2160** | 3840x2160 | 1 | `0x00000299` |
| 5 | `0x63DF1A14` | 2880x2160 | 2880x2160 | 1 | `0x00000299` |
| 6 | `0x63DF1A2C` | **3240x2160** | **3264x2160** | 1 | `0x00000299` |

**Correction to `E0753`:** the table *is* statically referenced. `0x6371B764` `add r3,pc` with
literal `0x6371B848 = 0x006D6234` gives `0x63DF199C`; `0x6371B766` `mla r3,#0x18,idx,r3` then reads
`+0x14` (the `vdf_id`) and calls `0x6371257C`. `E0753`'s sweep missed it because it queried
`0x63DF19B0` (an interior offset) and modelled only `ldr.w`/`movw`/absolute encodings, not the 16-bit
`ldr+add pc` idiom.

Index selection is `0x63CAEADA` (leaf; executed in Unicorn), not a `(w,h)` search:

```
r1 != 6                -> 0
r2 == 0: r3 <= 1920 ? 5 : (arg5 > 1920 ? 2 : 5)
r2 == 1: r3 <= 1920 ? r1(=6) : (arg5 > 1920 ? 3 : 6)
r2 == 2: r3 <= 1920 ? 4 : (arg5 > 1920 ? 1 : 4)
```

`3230/3240/3264/3840` all exceed the `0x780` threshold, so **the selected index does not depend on
the candidate width**. `3200`/`3264` therefore change only the record contents, not which record is
used. Step `A1` already exploited this: `tools/research/a6400_og_ram.c` lines 331-336 patch
`0x03CAEB1A` and `0x03CAEB2A` from `movs r0,#4` to `#6` (observed bytes `0420` -> `0620`) to force the
stock `3240/3264` entry, plus six `ZIMA_AVC` widths.

### 3.3 `ZIMA_AVC`: 14 rows, addressed by an explicit index (Observed)

`0x63E3B554`, 168 records x 40 B, packed `(u16 w, u16 h)` at `+0x18`; all widths are multiples of 16;
pairs present: `320x180, 480x270, 640x360, 800x480, 960x540, 1280x720, 1440x540, 1920x540, 1920x1080,
3840x2160`. Zero `(3200|3230|3240|3264|2880, 2160)` pairs. `0x639DCF24` receives the record index in
`r2`, does `mla r3,#0x28,idx,0x63E3B554`, and copies `(w,h)` out verbatim — it is not a search.

The 14 `(3840,2160)` pair VAs:

```
0x63E3B56C 0x63E3B594 0x63E3B5BC 0x63E3B5E4 0x63E3B60C 0x63E3B634
0x63E3BB34 0x63E3BB5C 0x63E3BB84 0x63E3BBAC 0x63E3BBD4 0x63E3BBFC
0x63E3C3F4 0x63E3C41C
```

There is also a **hard-coded fallback pair** in code at `0x639DCF9C`/`0x639DCFA2`
(`mov.w r3,#0xF00` / `mov.w r3,#0x870`, observed bytes `4f f4 70 63` / `4f f4 07 63`), taken when
`[r6+0x20]` is outside `6..9`; the `6..9` branch writes `1920`/`1080`. It is a 3840x2160 carrier that
`E0753`'s 32-pair inventory did not list (it is an immediate, not a contiguous pair).

### 3.4 `DmmConfig.bin`, the `sabin` blobs, and `dv_venc_initialize()` (Observed)

- `DmmConfig.bin` is the **kernel** `dmm.ko` rule table (`libIMDB.so` `0x3184
  DmmConfig=/usr/share/dmm/DmmConfig.bin`; `dmm.ko` strings `DmmConfig`, `dmm_config`,
  `/setting/DmmConfigExtra.bin`). `av-cam.bin` contains no `DmmConfig`/`dmm_config` string at all.
  The file's record shape is `{u32 physical base, u32 attribute, u32 extent id, u32 size bytes, u32
  rule bitmap}` (25 root records at `0x000050`, terminator at `0x000244`), with **no** `w`, `h`,
  `stride`, `vdf_id` or codec field: `u16 3240/3264/2160` occur 0 times and the 10 `u16 0x0C80`
  hits are low halves of `0x000C8000`-class byte sizes. It must **not** change for a 3200/3264 build.
- `sabin/sa_avce_{quality,performance,performance_plus}_zma.bin` contain zero `(3840|3240|3264|
  2880|4096, 2160)` u16 or u32 pairs (sectioned `ZM_AVCEQ*` containers). They must **not** change.
- `[ZIMA DVENC]dv_venc_initialize()` (`0x63F680D2` in the capture, `0x63F50BF4` in the packaged
  build) is **statically unreachable**: 0 hits under absolute `u32` LE/BE, adjacent `u16` halves,
  16-bit `ldr+add pc`, `ldr.w`+`add`, `movw/movt`, and `.data` slot scans, in both builds, while the
  same machinery resolves 119 sites for `0x63F5DE56` and one for `0x63F6434E`. The plan's
  "verify `dv_venc_initialize()` in Unicorn" question therefore **cannot be answered from this
  image**; it is replaced by the reachable gates below.

**DMM lock question (Observed symbols, Inferred lifetime).** `dmm.ko` is a reference-counted unit
manager (`dmm_ctrl_AllocUnitNewHdlr`, `AllocUnitReuseHdlr`, `AllocUnitShareHdlr`,
`AllocUnitRendezvousHdlr`, `OpenUnitHdlr`, `CloseUnitHdlr`, `dmm_unit_ctrl_SearchExistUnitFree`;
`/proc/udm/dmm_units` prints `handle type status alloc open rdv serial relation address size extent`
per unit). The return-code names are the **client** side: `DMM_RET_OK`, `DMM_RET_WRN_NOOPEN`,
`DMM_RET_WRN_DIRTYUNIT`, `DMM_RET_WRN_DIRTYUNITNOOPEN`, `DMM_RET_WRN_NOCLOSE`,
`DMM_RET_WRN_SUSPEND`, `DMM_RET_ERR_NOCLOSE`, ... are strings in **`av-cam.bin`** (and
`libmprctrl.so`), not in `dmm.ko` (0 `DMM_RET` strings in the module). `av-cam` allocates through a
synchronous UIPC round trip (`MemoryHandle::AllocNew` `0x639C9B94` -> `0x63D6C6C8`), passing **fixed
unit types** `0x370`-`0x373` with a 12-byte descriptor (`0x639CD552`-`0x639CD634`); the *sizes* come
from `DmmConfig.bin` by unit type, so a geometry change alters no DMM argument. A second exclusive
`AllocNew` of the same type can therefore hit an "already open" warning, and the decisive read is
`/proc/udm/dmm_units` before and after a Movie start. `vdf_tbl` itself is **not** shadowed:
`0x6371B766` recomputes the base from the literal pool on every invocation of the setting function,
so a data patch is re-read per call; the `dv_venc` layer is named only by log tags (`0x63F6914E`
`dv_venc_open::g_pdv_venc`, `0x63F69220` `dv_venc::m_pdv_sa_ctrl_venc`), so handle lifetime across
Movie starts is **unknown**.

### 3.5 What the width must satisfy (Observed gates, executed in Unicorn)

- `Camera::SaFuncDrv` image-parameter gate (`0x63B0E618`, caller `0x638E687A`), real rejection path:

```
0x63b0e6f4  tst.w r3,#0xf     ; width & 15      -> "FW: SA param width alignment error." (0x63F85379)
0x63b0e704  tst.w r3,#7       ; height & 7      -> "FW: SA param height alignment error." (0x63F8539D)
0x63b0e714  tst.w r3,#0xf     ; line_ofs & 15   -> "FW: SA param line_ofs alignment error." (0x63F853C2)
```

  Executed verdicts: `3840`, `3200`, `3264` accept; `3240` **rejects** (`3240 & 0xF = 8`), as does
  `1620`; `3840x2168` accepts (height is only `& 7`). Caveat: stock `vdf_tbl[3] = 1620` also fails
  this gate, so its presence on the *ZIMA* path is inferred; it is proven for the SA function whose
  caller is `0x638E687A`.
- `[IDT]` gates (`0x63B4D2A0` `tst.w r3,#3`, `0x63B4D882` `tst.w r2,#7`;
  strings `0x63F8DF2D`, `0x63F8DF77`) only **log** and continue; `3200`, `3240`, `3264`, `3840` all
  pass both.

### 3.6 `tbl_0x34` and `desc_0xAC` field laws (Observed, with a correction)

Per-id records: `tbl_0x34` at `0x63E11890 + id*0x34`, `desc_0xAC` at `0x63E11D28 + id*0xAC`.

```
id 0  0x63E11890: 7680 x4, 69120, 7680, 7680, 101888, 69120, 512, 64, 0, 163840
id 1  0x63E118C4: 8192 x4, 69120, 8192, 8192, 101888, 69120, 512, 64, 0, 163840
id 19 0x63E11C6C: 7680 x4, 69120, 7680, 7680, 101888, 69120, 512, 128, 0, 163840
id 20 0x63E11CA0: 8192 x4, 69120, 8192, 8192, 101888, 69120, 512, 128, 0, 163840
```

- The six stride words (`+0x00..+0x0C`, `+0x14`, `+0x18`) are `2 * align_up(W,64)`.
- **Correction:** `+0x10`/`+0x20` (`69120`) and `+0x1C` (`101888`) are **not** `9 * stride` — id 0
  (7680) and id 1 (8192) share both values, so they are width-independent (height/family dependent).
  They must stay stock.
- `+0x24`/`+0x28` (`512`, `64` vs `128`) are section/family dependent and width-independent.
- `desc_0xAC` id-0 words: `..., 1984, 2160, 1984, 540, 496, 135, 2, ...` with `+0x14`/`+0x1C`/`+0x98`
  = per-pipe pitch, `+0x18` = height, `+0x20` = `h/4`, `+0x24` = `pitch/4`, `+0x28` = `h/16`,
  `+0x2C` = `log2(npipes)` (proved: the call sites compute `1 << [desc+0x2C]`), `+0x70` = `pitch+64`.
  The live campaign's own numbers agree: `A14`'s `1684/421` and `1728/432` are `pitch` and
  `pitch/4`; `A15`'s `w[38]` is `+0x98` = `pitch`; `A17`'s `w[28]=1792 = 1728+64` is `+0x70`.

The per-pipe pitch law `active/2 + 64` vs `align_up(active/2,64) + 64` is still ambiguous for 3240
(1684 vs 1728) and now also for 3264 (1696 vs 1728), but **unambiguous for 3200**: `1600 + 64 = 1664`
under both readings.

### 3.7 Consolidated Path A2 candidate — `3200x2160` primary, `3264x2160` second

All addresses are capture VAs; `PA = VA - 0x60000000`, `file_offset = VA - 0x635C6000`.

| # | target | VA | PA | file off | stock bytes | patched | value |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `0x63761C58` id-19 width | `0x63761C64` | `0x03761C64` | `0x19BC64` | `4f f4 70 64` | `40 f6 80 44` | `movw r4,#0x0C80` (3200) |
| 2 | second width writer | `0x63762F32` | `0x03762F32` | `0x19CF32` | `4f f4 70 63` | `40 f6 80 43` | `movw r3,#0x0C80` |
| 3 | `desc_0xAC` pitch | `0x63E11D3C` | `0x03E11D3C` | `0x84BD3C` | `c0 07 00 00` | `80 06 00 00` | 1664 |
| 4 | `desc_0xAC` pitch copy | `0x63E11D44` | `0x03E11D44` | `0x84BD44` | `c0 07 00 00` | `80 06 00 00` | 1664 |
| 5 | `desc_0xAC` pitch/4 | `0x63E11D4C` | `0x03E11D4C` | `0x84BD4C` | `f0 01 00 00` | `a0 01 00 00` | 416 |
| 6 | `desc_0xAC` `+0x70` | `0x63E11D98` | `0x03E11D98` | `0x84BD98` | `00 08 00 00` | `c0 06 00 00` | 1728 |
| 7 | `desc_0xAC` `+0x98` | `0x63E11DC0` | `0x03E11DC0` | `0x84BDC0` | `c0 07 00 00` | `80 06 00 00` | 1664 |
| 8-13 | `tbl_0x34` six strides | `0x63E11890/94/98/9C`, `0x63E118A4`, `0x63E118A8` | `0x03E11890...` | `0x84B890...` | `00 1e 00 00` | `00 19 00 00` | 6400 |
| 14 | hard-coded fallback w | `0x639DCF9C` | `0x039DCF9C` | `0x416F9C` | `4f f4 70 63` | `40 f6 80 43` | `movw r3,#0x0C80` |
| 15 | `vdf_tbl[4]` w | `0x63DF19FC` (block bases `0x63DF199C`/`0x63DF1A44`/`0x63DF1AEC`, entry 4 = `+0x60` -> `0x63DF19FC`/`0x63DF1AA4`/`0x63DF1B4C`) | `0x03DF19FC` | `0x82B9FC` | `00 0f 00 00` | `80 0c 00 00` | 3200 |
| 16 | `vdf_tbl[4]` stride | `0x63DF1A04`/`0x63DF1AAC`/`0x63DF1B54` | `0x03DF1A04` | `0x82BA04` | `00 0f 00 00` | `80 0c 00 00` | 3200 |
| 17 | `ZIMA_AVC` 14 pairs | `0x63E3B56C ... 0x63E3C41C` | `0x03E3B56C ...` | `0x87556C ...` | `00 0f 70 08` | `80 0c 70 08` | 3200x2160 |

For `3264x2160` the same rows become `movw #0x0CC0` (`40 f6 c0 44` / `40 f6 c0 43`), pitch **1696 or
1728 (unresolved)**, `pitch/4 = 424 or 432`, `+0x70 = 1792`, six strides `80 19 00 00` (6528),
fallback `40 f6 c0 43`, `vdf_tbl` `00 0c 00 00` (3264), `ZIMA` `00 0c 70 08`.

Ordering and risk:

1. Rows 1-2 are **mandatory together** for any width change (they are the only code-level width
   sources in this module); `A18` patched both and still failed at `1620`, so the pair alone is not
   a success condition.
2. Rows 3-13 are pure data and follow the stock laws; they are independent of rows 1-2.
3. Rows 15-17 are the encoder/display side. `vdf_tbl[4]` is **conditional** (section 3.2: the index
   does not depend on width; the record is used only if the `[ctx+0x20]` words are a real
   full-resolution request). `ZIMA_AVC` rows are definitely read by the encoder; patch them.
4. Row 14 is a code fallback; patch it only if a run shows the fallback branch taken (no static
   caller chain proves it is live).
5. `3200` is the safer first test: `1600` is `25 x 64`, pitch `1664` is unambiguous, `6400` is a
   clean stride, `3200 % 16 == 0`, and all `vdf`/`tbl` values agree with the stock laws. `3264`
   differs only in that `1632` is `51 x 32` (not 64-aligned) and the pitch is ambiguous.

### 3.8 Container (`libmpr.so`) and LCD aspect for A2

- If the coded stream really becomes 3200/3264 wide, the container needs rows from section 2.2:
  `stsd` `(desc+0x08/+0x0A)` and `tkhd` `(r1/r2)`. A 3200-wide stream alone produces a **stale**
  container (`tkhd`/`stsd` = 3840x2160), because the two are independent inputs.
- Movie LCD aspect is independent of the stream width and applies to both paths (section 4).

---

## 4. Movie rear-LCD aspect: `640x360` (16:9) versus `640x428` (3:2) (Observed)

The box is not a constant. `viewUnified2.so` `.rodata` holds a **6 x 9 table of
`{f64 x, f64 y, f64 w, f64 h}`** at VA/file `0x77D090..0x77D750` (group stride `0x120`, cell stride
`0x20`), read by `0x2CC9B0` as `base + 0x120*group + 0x20*pattern` with `x` at `+0`, `y` at `+8`,
`w` at `+0x10`, `h` at `+0x18`.

```
0x77D090  (0.0, 26.0, 640.0, 428.0)   <- stock 3:2 cell
0x77D0B0  (106.0, 26.0, 428.0, 428.0) <- 1:1
0x77D0D0  (35.0, 26.0, 570.0, 428.0)  <- 4:3
0x77D110  (0.0, 60.0, 640.0, 360.0)   <- 16:9 cell (pattern 4)
0x77D150  (0.0, 60.0, 640.0, 360.0)   <- 16:9 cell (pattern 6)
```

Every cell is self-consistent with a 640x480 panel (`26+428+26 = 480`, `60+360+60 = 480`).
`(640,426)` does not exist in `viewUnified2.so`; the userspace 3:2 box is `640x428`, and
`640x426` is av-cam's 3:2 *content* cell (ladder row at `0x63E78536`:
`640x360, 640x480, 640x426, 480x480`). Both `image_cmn_169.uxc` and `image_EVF.uxc` already ship a
`640x428` resource next to the `640x360` one.

Selection is enum-driven, never a ratio:

- pattern: `CmnPanelCommonPattern::getPanelDisplayPattern()` `0x2BF8EC`, `tbb` at `0x2BF950`
  (`6c 74 74 2d 74 05 51 74 58`), returns `0..8`;
- group: `0x2CC958` -> `{1,0,2}` or `{4,3,5}` depending on whether the aspect getter `0x19D9E0`
  (`[global+0x86]`) returns 3; `isPanelAspect16_9` (`0x2FB768`) is true for enum 2 and 4;
- the movie REC layout family is `_169`-only (10 `_169` typeinfo names in `.rodata`; no `_32`/`_43`
  movie family exists), so the layout cannot be switched by picking another class.

Patch candidates (both datapath-only, no host rendering):

| # | kind | target | stock | patched | effect |
| --- | --- | --- | --- | --- | --- |
| P1 | `.rodata` data | eight 16:9 cells `0x77D110/150/230/270/470/4B0/590/5D0`, bytes `+0x0E` and `+0x1E` | `4e` (`60.0`) and `76` (`360.0`) | `3a` (`26.0`) and `7a` (`428.0`) | the 16:9 cell becomes the stock 3:2 cell |
| P1 minimal | `.rodata` data | the four pattern-4 cells `0x77D110/230/470/590` | same | same | leaves the "info bar" pattern-6 cells alone |
| P1b | `.rodata` data | integer copy `0x7C35E4/614/624` (`y 3c -> 1a`) plus stale `0x7C3604` | `(0,60,640,360)` | `(0,26,640,428)` | only if the int table is the one bound |
| P2 | `.text` byte | `0x2BFA3C` `movs r0,#4` | `04 20` | `00 20` | pattern 4 -> pattern 0 (3:2) |

P1 is preferred because it keeps the pattern (and therefore layout logic) intact; P2 is the
one-byte test if the pattern cannot be identified. Unknown: which of the f64 table, the int copy at
`0x7C35B4`, or the `.uxc` resource is actually bound at run time (the f64 table base is loaded as
`*(*GOT slot)`, and no static pointer to `0x77D090` exists).

---

## 5. Corrections and confirmations relative to `E0753` / Round 5

| earlier statement | status in this record |
| --- | --- |
| `vdf_tbl` has zero static references | **Refuted.** `0x6371B764` (`ldr+add pc`, literal `0x006D6234`) indexes it; `0x6371B766` `mla #0x18`. The `E0753` sweep looked at `0x63DF19B0` and missed the 16-bit idiom |
| the `0x9FF0` structure is a RAM ring buffer written by `0x6394E4E4` | **Refuted as stated.** `0x9FF0` is a byte budget at `0x64378A10`; records are pool objects handed to a consumer. The 16-byte slot size is proven, its field map is not |
| the plan's `+16` record header = level/sub-id/timestamp/length | **Correct for the 24-byte message, wrong for the slot** (no timestamp fits) |
| `tbl_0x34 +0x10` / `+0x20` are `9 * stride` and must change with width | **Refuted.** id 0 (7680) and id 1 (8192) share `69120` and `101888`, so those words are width-independent |
| `ZIMA_SCALER_A` / `ZIMA_SCALER_B` tables must stay consistent | **Ill-posed.** No `ZIMA_SCALER` string exists in the image or in any of the 1,079 extracted files; the Round 5 `A9` label does not name a table in this firmware |
| `DmmConfig.bin` may be a `3200`/`3264` geometry table | **Refuted.** It is the kernel `dmm.ko` extent/unit rule table with no `w`/`h`/`stride` field, and `av-cam.bin` never names it |
| `libmpr.so` has no hard-coded `3840`/`2160` on the box-writing path | **Confirmed and sharpened.** No `2160<<16` and no `3840<<16` exist anywhere in `libmpr.so`; `tkhd`/`stsd` are arguments/descriptor fields |
| the ZV-E1 `L4-mp4size` patch (3 container fields incl. Sony `PROF`) ports | **Partly refuted.** `pasp`, `VPRF`/`PROF` size box and HEVC (`hvc1`) do not exist in this body; only "lie in `tkhd`/`stsd`" ports, and it ports to call-site caves, not 4-bit immediate swaps |
| `dv_venc_initialize()` parameter checks can be verified in Unicorn | **Not possible from this image** (0 static references in both builds). Replaced by the `SaFuncDrv` gate (`0x63B0E6F4`) and the logging-only `[IDT]` gates |
| `A18`/`A19` failed because only one width translator was patched | **Refuted by `E0752`.** `A18` patched both (`0x03761C64` and `0x03762F32`) and was still black; `1620` at `0x03761C64` is the only delta from the working `A14` |
| `desc_0xAC`/`tbl_0x34` field meanings | **Confirmed** and matched against the campaign's own numbers (`+0x24 = pitch/4`, `+0x70 = pitch+64`, `+0x98 = pitch`, `+0x2C = log2(npipes)`) |
| `rec_fmt_tbl` id contract (`E0753` §2) | **Confirmed**, unchanged |
| the `DMM_RET_*` return-code strings are in `dmm.ko` | **Refuted.** `dmm.ko` contains 0 `DMM_RET` strings; the full set (`DMM_RET_OK`, `WRN_NOOPEN`, `WRN_DIRTYUNITNOOPEN`, `WRN_SUSPEND`, `ERR_*`) lives in `av-cam.bin` and `libmprctrl.so`, i.e. on the client side |

---

## 6. Explicit unknowns and next boundary

1. **`avlog` stream base.** Not statically present. Resolve with section 1.5 (exact-termination
   test) or a live read of the pointer at `sp+4` in `0x6394E4FE`. Part A (section 1.4) is safe
   regardless.
2. **Why the live budget at `PA 0x04378A10` read `0`** (`E0752`). Two reads (`0x04378A00`,
   `0x04378A10` before/after a known `S-Ele Refresh`) decide between an external reset, upstream
   gating, or a build mismatch.
3. **`[ctx+0x20]`/`[ctx+0x22]` meaning in the `vdf` selector.** Decides whether `vdf_tbl[4]` must be
   patched at all (section 3.2).
4. **Entry edges of `0x63957ED8`, `dv_venc_initialize()` and the `mp4FileLib_Rec` vtable.** All three
   are created at run time; only a live watchpoint or `.rel.dyn` `R_ARM_RELATIVE` resolution closes
   them.
5. **Per-pipe pitch law for 3264 (1696 vs 1728)** — needs the `desc_0xAC +0x14` reader.
6. **`tbl_0x34 +0x1C` (101888) and `+0x30` (163840)** readers remain unmapped; they are
   width-independent, so they can stay stock, but their unit is unknown.
7. **Which of the f64 table / int copy / `.uxc` resource is bound** for the LCD box, and whether the
   live-view sampler rescales or crops into a new 3:2 box.
8. **No physical test of any candidate here.** `3200` and `3264` have never run; `pasp` does not
   exist; `A18`'s black screen is still unattributed at the hardware level (the code applies no
   alignment check, so the constraint is the consumer of `0xF3851C18`).

---

## 7. What a next live step would look like (not executed)

Ordered by information per unit of risk, all inside the standing read-only envelope except where a
RAM write is stated:

1. **Read-only:** `avlog` Part A (7 pages) plus `PA 0x04378A10` twice. Confirms the logger state
   machine and the `ref` counter in one shot.
2. **Read-only:** one `S-Ele Refresh` window of `avlog` Part B; accept only on exact termination.
3. **RAM write (new step `B1`):** Path A2 rows 1-13 for `3200` **without** `vdf_tbl`/`ZIMA` changes,
   to separate the dual-pipe geometry from the encoder tables; expect either a 3200-wide live view
   or a black screen with a specific `ref` count and a specific `avlog` line.
4. **RAM write (step `B2`):** add `ZIMA_AVC` and `vdf_tbl[4]`; then check whether a recorded MP4
   reports 3200x2160 (`tkhd`/`stsd`) before adding the container hook.
5. **Data patch (host-side file):** the LCD P1 cells in `viewUnified2.so`, independently of the
   stream width.

---

## 8. Reproduction

```powershell
# avlog
.\venv\Scripts\python.exe -m artifacts.scratch.r5.dis_avcam --start 0x6394EC5C --len 0x68
.\venv\Scripts\python.exe -m artifacts.scratch.r5.dis_avcam --start 0x6394E4F4 --len 0x50
.\venv\Scripts\python.exe -m artifacts.scratch.r5.dis_avcam --start 0x6390784A --len 0x2C
.\venv\Scripts\python.exe -m artifacts.scratch.r6.s1_avlog.bw
.\venv\Scripts\python.exe -m artifacts.scratch.r6.s1_avlog.sites --filter apl_mov

# dual pipe
.\venv\Scripts\python.exe -m artifacts.scratch.r5.dis_avcam --start 0x63761C58 --len 0x50
.\venv\Scripts\python.exe -m artifacts.scratch.r5.dis_avcam --start 0x63762F1C --len 0x40
.\venv\Scripts\python.exe -m artifacts.scratch.r5.dis_avcam --start 0x6375FD74 --len 0x30
.\venv\Scripts\python.exe artifacts\scratch\r6\s2-dualpipe\uni_dualpipe.py

# vdf / zima / dmm
.\venv\Scripts\python.exe -m artifacts.scratch.r5.dis_avcam --start 0x6371B740 --len 0x40
.\venv\Scripts\python.exe -m artifacts.scratch.r5.dis_avcam --start 0x63CAEADA --len 0x56
.\venv\Scripts\python.exe artifacts\scratch\r6\s3-vdf-dmm\unicorn_vdf_idx.py
.\venv\Scripts\python.exe artifacts\scratch\r6\s3-vdf-dmm\unicorn_align.py
.\venv\Scripts\python.exe artifacts\scratch\r6\s3-vdf-dmm\dmmcfg.py

# libmpr container
.\venv\Scripts\python.exe artifacts\scratch\r6\s4-libmpr\02_fourcc.py
.\venv\Scripts\python.exe artifacts\scratch\r6\s4-libmpr\11_fn.py 0x87072C
.\venv\Scripts\python.exe artifacts\scratch\r6\s4-libmpr\11_fn.py 0x870A74

# LCD aspect / FE 5064
.\venv\Scripts\python.exe artifacts\scratch\r6\s5-lcd-aspect\10_rects.py lib/viewUnified2.so 0x77D090 0x77D750
.\venv\Scripts\python.exe artifacts\scratch\r6\s5-lcd-aspect\15_fe_5064.py
```

The five strand reports are kept verbatim at
`artifacts/scratch/r6/{s1_avlog,s2-dualpipe,s3-vdf-dmm,s4-libmpr,s5-lcd-aspect}/REPORT.md`
(gitignored scratch); every load-bearing claim above is either reproduced inline or re-verified by the
coordinator, and the corrections in section 5 record where a strand's conclusion was overridden.
