# E0736 — CamCore rule windows log unlisted reads and writes as unmodelled accesses

Date: 2026-11-27.

This record covers one change to the emulator's diagnostic logging. It is **not** a claim about physical hardware, and
no camera was accessed.

## Observation

- The generic `cxd90045-camcore` rule device returns 0 for an offset that has no rule and stores an unlisted write
  silently. Mapping a window over unknown registers therefore removed them from the `cxd90045.unmodelled` log without
  any rule behind them.
- On `main` (`da24881`) three qtests that expect the unmodelled form fail: `test_qemu_camcore_f302.py`,
  `test_qemu_camcore_f302_1000.py` and `test_qemu_camcore_f302_rest.py`.

## Change

- `qemu-a6400/qemu-overlay/hw/misc/cxd90045_camcore.c`: unlisted reads and writes are logged with `LOG_UNIMP` as
  `<window>.unmodelled: unimplemented device read|write (size N, offset ...)`, with the same offset base
  (`0xF0000000`) as the board's catch-all region. Returned values and stored values are unchanged.

## Method

QEMU 11.1.3 was built twice on Linux from this tree's `qemu-a6400/setup-qemu.sh`: `main` (`qemu-system-arm` sha256
`d88dc023bf17…`) and `main` plus this change (`8d85c314f5d3…`). On each build:

1. every file in `qemu-a6400/tests/`, run the way the file says it must be run;
2. `python3 -m unittest discover -s tests -q`;
3. boots of captured ILCE-6400 inputs (not in this repository) with the declared stimuli `GATE_FORCE_PROBE=1`,
   `ENTRY_FORCE_PROBE=1`, `CAMPIG_ENABLE_READBACK=1`, `SGC1_EVENT_A=1`, `WORK_DMA=1`, `EVSEL_LATE_US=500`,
   `LIVEVIEW_SOURCE=bars` and a captured IDT profile, with `-d unimp,guest_errors`, reading the mode sequencer.

## Result

| Check | `main` | with this change |
| --- | --- | --- |
| `qemu-a6400/tests` files | 16 pass, 3 fail (2 failed checks each) | 19 pass, 0 fail |
| `unittest discover -s tests` | 1759 run, 10 failures, 145 errors, 146 skipped | same counts and same failing ids |
| Boots reaching sequencer index 42, step `0x897F`, 0 mismatches | 6 of 6 | 5 of 6 |
| Distinct unmodelled accesses in the scored boots | 2 reads (`0x1050a0`, `0x2900030`) | the same 2 reads |

The remaining `unittest` errors are mostly missing optional Python packages in that environment.

## Limitations

- One of the six boots with the change stopped at sequencer index 32 (step `0x8A3C`, two module mismatches); its
  partner run and the four later runs reached index 42. Six runs per side cannot show whether the two builds differ in
  how often a boot stops early. The unmodelled-access list of the stopped run was not kept.
- No scored boot listed a CamCore offset, so these boots show no new log line from the changed path. The path itself is
  exercised by the qtests.
- Any consumer that counts unmodelled accesses will count more of them for offsets that were silent before, in runs that
  reach such offsets.
- Wall-clock times are not compared: the boots were not run to measure speed.

## Reproduce

Build with `qemu-a6400/setup-qemu.sh`, then run each file in `qemu-a6400/tests/` with `A6400_QEMU_DIR` pointing at the
build, and `python3 -m unittest discover -s tests -q`. The boot numbers need local captured inputs; the script that
reads the sequencer and counts accesses is private tooling.
