# E0737 — Carry the eMMC OCR across snapshot restore

Date: 2026-11-27.

This record covers one emulator change and the static test that guards it. It is **not** a claim
about physical hardware. The change is in the overlay only; the boot
stimuli are the declared ones (`cond. PROBE` gate-force and entry-force).

## Observation

- On the certified baseline build, restoring a snapshot at the BusyBox banner produced 20x
  "attempt to access beyond end of device" and 10x "Buffer I/O error" on nflasha15/nflasha7,
  then "im.elf: Input/output error"; restoring at `BSA_trace 1@` entered the exception monitor
  with an Illegal Instruction at `0x00092226` and the sequencer object never initialised
  (index 0). A cold boot logged none of these; the same VM continued after savevm ran clean to
  index 42, so the difference was the restore. The cause was assumed unsaved eMMC/DMA state.
- This eMMC is smaller than 2 GiB (`emmc.img` minus 8 MiB boot parts minus rpmb), so
  `sd_reset()` cannot let `sd_ocr_powerup()` re-derive `OCR.CARD_CAPACITY`; the guest sets it at
  run time in CMD1, and loadvm cleared it. Restored sector numbers were then decoded as byte
  addresses.

## Change

Since the 2026-11-27 upstream sync (the overlay copy of `sd.c` was removed upstream,
[E0732](0732-qemu-remove-stale-overlay-sd-c.md)) the same change is carried in
`qemu-a6400/patches/qemu-v11.1.3-sony-a6400.patch`; the v10.1.0 patch does not have it. The
path below is where it lived for the runs in this record. The static test now reads the patch.

- `qemu-a6400/qemu-overlay/hw/sd/sd.c`, `sd_ocr_vmstate_needed()` (around line 998) now returns
  true for an eMMC as well as for an unpowered card:
  `return !FIELD_EX32(sd->ocr, OCR, CARD_POWER_UP) || sd_is_emmc(sd);`. The existing
  `sd_ocr_vmstate` subsection therefore saves OCR and `ocr_power_timer` for a powered-up eMMC.
- A new static test (`qemu-a6400/tests/test_qemu_l0176_snapshot_state.py`) checks the source
  and registration; it does not boot QEMU.

## Method

Boot the captured inputs with the declared stimuli, save at the BusyBox banner and resume:

```sh
export GATE_FORCE_PROBE=1 ENTRY_FORCE_PROBE=1 CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1 WORK_DMA=1
python3 -m tools.research.qemu_snapshot create \
  --bundle-dir bundle --tag banner --wait-for "built-in shell (ash)" \
  --seconds 200 --evsel-late-us 500 --liveview-source bars \
  --inputs artifacts/qemu-a6400/inputs-a
python3 -m tools.research.qemu_snapshot resume \
  --bundle-dir bundle --report-dir resume --tag banner --seconds 150
```

Count the four failure strings in the resumed serial log and read the sequencer index from the
resume report. Save and resume were also repeated at `BSA_trace 1@` and at the final state
(index 42). A decisive cross-load ran the new binary against a snapshot bundle made before the
change.

## Result

| Run | Failure strings | Sequencer | Notes |
| --- | --- | --- | --- |
| banner restore | 0 of 4, 2/2 runs | 42 | qcow2 `2bc5b023…` / `f1e3112d…` |
| `BSA_trace 1@` restore | 0 of 4 | 42 | save 0.771 s |
| final-state restore | 0 of 4 | 42 | save 0.881 s; index 42 at 6.78 s vs 47.56 s cold boot |
| pre-fix bundle, new binary | exception monitor | 0 | reproduces the baseline fault at `0x00092226` |

Build `qemu-system-arm` sha256 `568bde7da6fd…` for every run. The final-state resume printed 43
serial lines / 4948 B and its last output was at guest 5.464 s, then it idled. The static test
passes 6/6.

## Red-team correction

The red team upheld the fix with a correction: the earlier "serial output continues without fault
strings for 60 s" is not literal. Output stops at guest 5.46 s and then idles, though no fault
appears; the index stays 42 in every sample over the 60 s. It also confirmed the cross-load
reproduces the baseline fault and that no other SDHCI field diverged in these runs.

## Re-check on the `a9d986a` port (2026-11-28)

The fix was re-ported to upstream `a9d986a` in the v11 patch, with the same static test. The
record's banner method was repeated on the port and on the same overlay tree at upstream `a9d986a`
without the guard, one build at a time, with no other QEMU: `loop/tools/snap/snap.py create --marker "built-in shell (ash)"` then `resume`, the
declared baseline environment, captured inputs-a and `-icount shift=0,sleep=on`.

| Run | Build sha256 | Banner | savevm | Resume | Faults | Index |
| --- | --- | --- | --- | --- | --- | --- |
| send-L0176 | `cb9deefc0d21…` | 11.83 s | 0.614 s | ok | 0 (0/0/0/0/0) | 0 -> 42 at 45.4 s, stayed 42 to 59.4 s |
| up-a9d986a | `db5ff344539c…` | 11.12 s | 0.588 s | not ok | 2 (`im_elf_io` 1, `exception_monitor` 1) | 0 for all 120 s |

Fault keys: `access_beyond_end` / `buffer_io` / `im_elf_io` / `illegal_instruction` /
`exception_monitor`. The port's resume printed 1410 serial lines / 158995 B with its last output at
guest ~2.9 s. The upstream control ends at `Exception>`; serial line 3 is
`sh: im.elf: Input/output error` and line 320 is `Entering exception monitor.`.

Source check: the port's `hw/sd/sd.c` `sd_ocr_vmstate_needed()` returns
`!FIELD_EX32(sd->ocr, OCR, CARD_POWER_UP) || sd_is_emmc(sd)`; upstream has only the first term. The
device test set passes 20/20 on the port, including `test_qemu_l0176_snapshot_state.py`.

Caveat: `up-a9d986a` is not stock QEMU; it is the same synced-overlay build as `send-L0176`. The two
build trees are byte-identical except `hw/sd/sd.c`, which carries the guard only in `send-L0176`, so
the split isolates the one-line change. It is not the certified-baseline build used above, so the
upstream symptom here is the exception monitor (as at `BSA_trace 1@`), not the 20x block errors seen
at the banner. The root cause is the same: `OCR.CARD_CAPACITY` is lost on loadvm. The split shows
the fix is present or absent, not a scored delta.

Two more port-review corrections. First, the port's resume starts at index 0 and only reaches 42 at
45.4 s; it does not resume directly into 42. Second, the upstream control's scored runs disagree:
one ended at 42 / `0x897F` with 0 mismatches and one at 32 / `0x8A3C` with 2 mismatches, so "0
mismatched" describes the scored furthest run, not every run. The static test passes 6/6 only with
`A6400_QEMU_DIR` set; without it 4/6 pass and 2 skip, and a mutant with the `|| sd_is_emmc(sd)`
clause removed still passes 4/4, so the test reads the patch text and does not by itself prove the
built tree behaves correctly.

## Limits

- The new test is static only; it would miss a dead or inverted branch, `sd_is_emmc()` returning
  false on the real device, a broken registration or pre_load, and any restore still wrong at
  loadvm.
- Other SDHCI fields are unproven and not exercised: hostctl2, vendor_spec, UHS-only
  switching_1v8/uhs_activated, ext_csd_ro, scr and size.
- `REAR_CONTROLS` restore was not tested.
- Tested on one eMMC input set only.
- A boot with no save/load never calls the changed function: `sd_ocr_vmstate_needed()` is reached
  only through `vmstate_section_needed()` from a savevm, and load uses stream markers.

## Reproduce

Build `qemu-system-arm` with `qemu-a6400/setup-qemu.sh` from this tree, point `A6400_QEMU_DIR` at it, then run the
`create`/`resume` commands above. The scored runs also preloaded the captured IDT profile (`IDT_CAM`). The scripts
that counted the failure strings are private tooling, not part of this repository.
