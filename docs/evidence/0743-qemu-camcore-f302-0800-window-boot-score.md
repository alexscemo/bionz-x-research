# E0743 — Boot score for the CamCore `0xF3020800` window at `0xF3020000`

Date: 2026-11-21 (the scored runs). Pull request #71 was merged on 2026-11-22.
This record was written afterwards from the saved score files, and the boots were not
re-run for it. It covers an emulator change that serves the second 1 KiB of the CamCore
`0xF3020000` block with a per-offset rule table. The scores were produced by this
project's boot scorer (oracle version 3). This record does not claim any physical camera
result.

## Observation

- Before the change, the baseline boot listed 938 distinct unmodelled address pairs. Of
  those, 90 pairs are in the `0xF3020800..0xF3020BFF` window (block-relative
  `0x800..0xBFF`), above the lowest `0x800` bytes that the AIF model already owns.
- The 90 pairs cover 89 distinct offsets: 88 write-only offsets and one
  read-modify-write word `+0xB80`. The word `+0xB80` has 74 reads, from the two reading
  load sites `0x63784AB4` and `0x63784A74`, and every read follows a write, so its rule
  returns the guest's own last write.
- The baseline's unmodelled reads are 95 and its accesses to unmodelled space are
  8,006,648.

## Change

- `cxd90045-camcore` mounts a second 0x400-byte sub-window at `0xF3020800` inside the
  `0xF3020000` block, at priority `-600`, selected by the `window` property. It holds
  one block-relative rule per observed offset: 88 `CAM_ZERO` (read 0, as the catch-all
  did) and one `CAM_RF` for `+0xB80` (read the last write).
- The lowest `0x800` bytes of the block, owned by the AIF model, are unchanged. The
  window is mapped whole, but an offset without a rule still logs as unmodelled, so the
  gaps are not silently claimed; the new qtest uses an unruled `+0xBF0` as its negative
  control.
- The change was merged as pull request #71, merge commit `0e7a077`, on 2026-11-22.
  It touches four files (`qemu-a6400/STATUS.md`,
  `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`,
  `qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c`, and the new
  `qemu-a6400/tests/test_qemu_camcore_f302.py`, 142 lines): 278 insertions, no
  deletions.
- The experiment's notes report the new qtest at 7/7: a ZERO word reads 0, the RF word reads the last
  write, and the unruled `+0xBF0` still logs both directions.

## Result

Each column is one saved score file. No values are averaged.

| Measure | Before (baseline) | After (rerun) | After (promotion) |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, value `0x897F` | same | same |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled pairs | 938 | **848** | **848** |
| Unmodelled reads | 95 | 94 | 94 |
| Accesses to unmodelled space | 8,006,648 | 7,489,831 | 7,223,408 |
| `t_reach` (two runs) | 42.36 s / 42.10 s | 42.72 s / 42.18 s | 42.13 s / 42.30 s |
| Contended host | no | no | no |

The distinct count fell by 90, the exact number of catch-all pairs in
`0xF3020800..0xF3020BFF`, and none of the remaining 848 pairs are in that window. The
final sequencer step and its value did not change, so boot progress did not change. The
scorer records the comparison as `moved: true`, kind `coverage`, `regressed: false`.

The two after-score files report different access counts for the same build and inputs
(7,489,831 in the rerun and 7,223,408 in the promotion). The saved score files do not say
why. This record states the numbers and does not resolve the difference; it is not a
speed claim.

## Identity

The instruction digest, the boot manifest digest and the submicro camera page digest are
the same in the baseline and in both after-scores. The scorer's visual check (`vph`) is
also unchanged: 640 x 480, 4,368 non-black pixels, centre non-black fraction 0.0314,
sha256 `1cb47093…`. The QEMU binary digest changed, as expected for a code change:
`3152fafb…` before and `2c542fc9…` after. The promotion was recorded at
`20261121T090753Z`.

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

The accepted-patch list is the same on both sides; the promotion adds this change and no
other option differs.

## What is not shown

- **Most of the window is write-only in this boot.** The 88 `CAM_ZERO` offsets have no
  read in the scored boot, so the rules only retire their writes; whether the firmware
  reads them outside this boot window is not shown.
- **Register meaning is unknown.** No datasheet or bus capture names these registers. The
  rules add coverage for observed accesses; they do not model a device.
- **The rest of the block is outside this claim.** The window is only the second 1 KiB.
  Pairs elsewhere in the `0xF3020000` block are not counted or changed here.
- **No hardware verification.** This is emulator only, and the camera-captured inputs are
  not in the repository, so the counts cannot be reproduced without them.
- **No new boot progress.** The firmware still stops at the same sequencer step (42 of
  43) with 0 mismatches.
- **The access-count difference is unresolved**, as stated in Result.

## Red team

An independent red-team recount upheld the claim: the window goes from 90
pairs to 0, the non-window pairs are identical at 848, the window cannot shadow the
`0x800`-byte AIF model, the diff is add-only, there is no `LOG_UNIMP` change, no
guest-RAM write and no guest-PC branch.

The red team corrected the original prose, which said "89 write-only + 1 read-modify-write",
but the count is **88 write-only + 1 read-modify-write**, that is 89 offsets and 90
pairs.

## Sources and number provenance

All numbers come from the project's saved files: the saved rerun score, the saved
promotion score, the saved baselines before and after this change, the experiment's
finding notes (window layout, offsets, reader addresses, qtest count, red-team result)
and the merged change itself (file list, line counts). The score files are not in the
repository.
