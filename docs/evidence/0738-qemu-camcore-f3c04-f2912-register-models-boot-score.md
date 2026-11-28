# E0738 — Boot score for the register models at 0xF3C04000 and 0xF2912000-0xF2915FFF

Date: 2026-11-21. Recorded from merged pull request
[#64](https://github.com/ivica00/bionz-x-research/pull/64)
(merge commit `038806a73653d73e720690ba0e6f00cbdcc7d5e1`, 2026-11-21T19:29:11+01:00),
head commit `94124bb`. This workspace did not re-run the boot scorer and did not
access a physical camera. The scores below are the ones the pull request reports,
produced by this project's boot scorer (oracle version 3) run by the contributor.

## Result

The merged machine adds two block tables to the generic `cxd90045-camcore` rule file
at priority -600, so every existing model keeps winning: the P2S page and evsel block
at `0xF3C0xxxx`, and the 0x1200-byte GPIO model and DFS at `0xF291xxxx`. A new device
property `window` selects the mapped sub-window of a block table that is selected by
`base`. The change is on by default in the `sony-a6400` machine and adds no
environment option.

The rules cover:

- `0xF3C04000-0xF3C08FFF`: 23 read offsets, all read-modify-write (each reader is a
  load / bit-field-insert / store on the same word); each read returns the guest's own
  last write.
- `0xF2912000`, `0xF2914000` and `0xF2915000`: 16 read offsets that read 0 as the
  catch-all did (copy getters or one-bit tests; `+0x5010` has no CPU reader).

The contributor reports the same final mode-sequencer state and a drop in distinct
unmodelled device addresses from 421 to 330.

| Measure | Before | After |
| --- | --- | --- |
| Final state | step 26 of 43, value `0x8004`; module bit 2 request `0x0323` / report `0x0320` | same |
| Distinct unmodelled device addresses | 421 | **330** |
| of which reads | 193 | 154 |
| Accesses to unmodelled space | 60,053 | 20,320 |
| Wall time to final state (two runs) | 37.56 s / 37.01 s | 37.20 s / 37.19 s |
| Host busy during measurement | no | no |

The promotion re-measure score records 330 distinct unmodelled addresses, 154 of them
reads, and 20,320 accesses, which matches the pull request body. The earlier in-lead
score file records 19,227 accesses for the same after-build; the pull request body
differs from that earlier file and matches the promotion re-measure. The promotion
re-measure records the full scorer run at 82.7 s per run; the time to final state
above is the `t_reach` pair.

Access-stream comparison against the same boot without this commit: 0 structural
divergences; all 2,261 reads in the 0xF3C0 window equal the guest's own last write;
every changed write is the base value plus the read-back bits; no trace hits inside
other devices' ranges.

Build: QEMU v11.1.3 with this repository's `qemu-a6400` overlay. Scorer version 3; the
`qemu-system-arm` SHA-256 recorded in the scores is
`4e215ca0b76ccdc20af6c4719a63f91b018d98afda00e5450f5c7fd6390202c7`. Boot options on both
sides: `EVSEL_LATE_US=500 CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1`,
`-icount shift=0,sleep=on`.

The change touches `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` (42 lines added) and
`qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c` (125 lines changed); 2 files
changed, 158 insertions and 9 deletions.

## Verification

- `python3 -m unittest discover -s tests -q` (Linux/WSL, pull request branch): 1537
  tests run, `FAILED (errors=136, skipped=123)`. The pull request reports that the 136
  failing ids also fail on upstream `main` `6d75c9c` in that environment, mostly
  because of optional dependencies.
- `python3 -m tools.research.render_research_status --check`: passed.
- Firmware-gated tests skip without local captured inputs. Skipped checks are not
  claimed as validated.

## Not checked

- The boot scorer was not re-run in this workspace; the table is the contributor's
  measurement.
- No physical camera was used.
- The captured inputs needed to reproduce the counts are not in the repository, so a
  fresh clone cannot repeat them.
- The "all reads equal the guest's last write" statement is taken from the posted
  access-stream comparison and was not independently re-derived here.
- This change does not model `0xF2400000`, and this record does not cover it.

## Limitations

- Emulator only; not verified on a physical camera.
- The numbers need the captured inputs to reproduce; they are not in this pull
  request.
- The firmware still stops at the same sequencer step; this change does not claim
  boot progress.
- The first version of the change also mapped `0xF2400000` as an all-zero window (421
  to 258 distinct addresses). Review found that 23 of its 35 read offsets are
  guest-written and `+0x1038` is compared with 3 and never satisfied, so a 0-returning
  table would hide a real status register. That window was removed before promotion
  and is not part of the accepted change; it remains a work item that needs a real
  status model.
- The model is not opt-in. A self-clearing or counting status word among the read-back
  registers would be stored as plain memory; the posted logs are the evidence that
  those reads return the guest's own last write.
- The pull request's own safety checklist leaves "new hardware/emulator behaviour is
  opt-in" unchecked, consistent with the default-on behaviour recorded here.
