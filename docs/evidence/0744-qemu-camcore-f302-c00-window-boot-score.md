# E0744 — Boot score for the CamCore `0xF3020C00` window at `0xF3020000`

Date: 2026-11-21 (the scored runs). Pull request #75 was merged on 2026-11-23 (+01:00).
This record was written afterwards from the saved score files, and the boots were not
re-run for it. It covers an emulator change that serves the remainder of the CamCore
`0xF3020000` page above the earlier 1 KiB window with a per-offset rule table. The scores
below were produced by this project's boot scorer (oracle version 3). This record does not
claim any physical camera result.

## Observation

- Before the change, the baseline boot (the promoted stack at the time) listed 848 distinct
  unmodelled address pairs. Of those, 37 pairs are in the CamCore `0xF3020000` page at
  block-relative `0xC00..0xFFF`, above the earlier `0x800..0xBFF` window. All 37 are
  writes, with no matching read in the boot list.
- The busiest words are `+0xC04`/`+0xC08` and `+0xD10..+0xD70` (91 writes each); `+0xD00`,
  `+0xE00` and `+0xF00` are written once, and `+0xF10..+0xF70` four times.
- The rest of the 64 KiB block is not part of this claim: it keeps 73 pairs at
  block-relative `0x1000..0x1FFF` (all writes, `0x1500..0x1E00`).

## Change

- `cxd90045-camcore` mounts a second 0x400-byte sub-window at `0xF3020C00`, priority
  `-600`, selected by the `window` property. It holds one block-relative `CAM_ZERO` rule
  per observed write-only offset.
- The earlier window at `0xF3020800..0xF3020BFF` and the lowest `0x800` bytes owned by the
  AIF model are unchanged.
- The window is mapped whole, but an offset without a rule still logs as unmodelled, so
  the gaps (for example `+0xD04`) are not silently claimed. `+0xD04` is the negative
  control in the new qtest.
- The change was merged as pull request #75 (merge commit `874dd8d`) on
  2026-11-23 (+01:00). It
  touches four files: `qemu-a6400/STATUS.md`,
  `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`,
  `qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c`, and the new
  `qemu-a6400/tests/test_qemu_camcore_f302_rest.py` (156 lines). Total is 250 insertions
  and 3 deletions.
- The experiment's notes report the new qtest at 10/10 and the earlier-window regression
  at 7/7. The qtest writes and reads two ruled words and gets 0 each time, confirms the
  earlier table is still selected, and shows the unruled `+0xD04` still logs both ways.

## Result

Each column is one saved score file. No values are averaged.

| Measure | Before (baseline) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, value `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled pairs | 848 | **811** | **811** |
| Unmodelled reads | 94 | 94 | 94 |
| Accesses to unmodelled space | 7,223,408 | 7,572,267 | 7,511,779 |
| `t_reach` (two runs) | 42.13 s / 42.30 s | 42.12 s / 42.11 s | 42.13 s / 42.10 s |
| Contended host | no | no | no |

The distinct count fell by 37, the exact number of write-only pairs in
`0xC00..0xFFF`. The enclosing 64 KiB block keeps the 73 pairs at `0x1000..0x1FFF`.
The final sequencer step and its value did not change, so boot progress did not change.

The scorer's comparison records `moved: true`, kind `coverage`, `regressed: false`.
The promotion was measured twice with the same final state and the same distinct count,
and the promoted stack is also what the saved baseline before the next change records.

The two after-score files report different access counts for the same build and inputs
(7,572,267 in the rerun and 7,511,779 in the promotion). The score files do not say why,
and both are above the before figure of 7,223,408 even though 37 log lines leave the
unmodelled list. This record states the numbers and does not resolve the difference; it
is not a speed or behaviour claim.

## Identity

The instruction digest, the boot manifest digest and the submicro camera page digest are
the same in the baseline and in both after-scores. The scorer's visual check (`vph`) is
also unchanged: 640 x 480, 4,368 non-black pixels, centre non-black fraction 0.0314,
sha256 `1cb47093…`. The QEMU binary digest changed, as expected for a code change:
`2c542fc9…` before and `867bd316…` after. The promotion was recorded at
`20261121T095155Z`.

## Inputs and declared stimuli

The request environment is identical on both sides, so the difference is this change
alone. It contains:

- `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`, `SGC1_EVENT_A=1`, `WORK_DMA=1`, speed
  profile `default`, and `-icount shift=0,sleep=on`.
- `GATE_FORCE_PROBE=1` and `ENTRY_FORCE_PROBE=1`: declared probes that the scorer allows.
- `LIVEVIEW_SOURCE=bars`: the declared bar-pattern source.
- The camera-captured `IDT_CAM` preload, a declared input. It is not in the repository
  and its contents are not described here.
- The camera-captured boot inputs (boot manifest and submicro camera page). Neither is in
  the repository.

The accepted-patch list is the same on both sides except that the promotion adds this
change; no other option differs.

## What is not shown

- **Reads were not exercised.** The 37 offsets are write-only in the scored boot: the
  baseline list has no read for any of them. A read of a ruled offset returns 0, as the
  qtest shows, but the guest boot itself did not read them. Whether the firmware reads
  these offsets outside this boot window is not shown.
- **Register meaning is unknown.** No datasheet or bus capture names these registers. The
  rules add coverage for observed writes; they do not model a device.
- **No hardware verification.** This is emulator only, and the camera-captured inputs are
  not in the repository, so the counts cannot be reproduced without them.
- **No new boot progress.** The firmware still stops at the same sequencer step (42 of
  43) with 0 mismatches.
- **The access-count difference is unresolved**, as stated in Result.

## Red team

The finding notes state that the first red-team pass **refuted the claim's scope**: "0 page
pairs" was true only for the 4 KiB page `0x03020000..0x03020FFF`; the 64 KiB block keeps
73. The wording was corrected, including in `STATUS.md`, and the re-check **upheld** it on
the same code, with counts 37 and 73 and the total going from 848 to 811.

## Sources and number provenance

All numbers come from the project's saved files: the saved rerun score, the saved
promotion score, the saved baselines before and after this change, the experiment's
finding notes (window layout, offsets, write counts, qtest count, red-team result) and the
merged change itself (file list, line counts). The score files are not in the repository.

| Number or claim | Source |
| --- | --- |
| Run date `2026-11-21`; oracle version 3 | the three saved score files |
| Window `0x400` bytes at `0xF3020C00`, priority `-600`, per-offset `CAM_ZERO` table | finding notes, merged change |
| Earlier window `0x800..0xBFF`; AIF model covers the first `0x800` bytes | merged change |
| 37 write-only pairs in block-relative `0xC00..0xFFF` | finding notes, merged change, saved baseline before this change |
| 73 pairs at `0x1000..0x1FFF`, writes `0x1500..0x1E00` | finding notes, merged change |
| Page `0x03020000..0x03020FFF`; block `0xF3020000..0xF302FFFF` | merged change |
| Per-offset write counts: 91, 1, 4 | merged change |
| qtest 10/10 (two ruled ZERO words read 0); earlier-window regression 7/7; unruled `+0xD04` control | finding notes, merged change |
| Change four files, 250 insertions, 3 deletions, new test 156 lines | merged change |
| Before: 848 distinct, 7,223,408 accesses, 94 reads | saved baseline before this change |
| After rerun: 811 distinct, 7,572,267 accesses, 94 reads | saved rerun score file |
| After promotion: 811 distinct, 7,511,779 accesses, 94 reads | saved promotion score file |
| Step 42 of 43, `0x897F`, 0 mismatches | all three saved score files |
| `t_reach` 42.13 / 42.30 s; 42.12 / 42.11 s; 42.13 / 42.10 s | respective saved score files |
| QEMU `2c542fc9…` before, `867bd316…` after | saved baseline before this change, both after scores |
| Instruction, boot-manifest and submicro-page digests unchanged | saved baseline before this change, both after scores |
| `vph` 640 x 480, 4,368, 0.0314, `1cb47093…` | all three saved score files |
| Promoted stack confirmed by the saved baseline after this change | saved baseline after this change |
| Promotion timestamp `20261121T095155Z` | saved promotion score file |
| Red-team first pass, correction and re-check | finding notes |
