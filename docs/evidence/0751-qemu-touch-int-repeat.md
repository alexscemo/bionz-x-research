# E0751 — Touch INT repeats while a touch report is unread

Date: 2026-11-27.

This record covers one change to the emulator's touch device model. It is **not** a claim about physical
hardware, and no camera was accessed.

## Observation

- `cxd90045_touch.c` raises P0.12 twice per tap: press at about +2 ms and release at about +39 ms. The GPIO model
  turns each edge into a 50 us pulse on GIC IRQ 133; the real latch/acknowledge behaviour is not modelled (E0527), so
  a pulse the CPU does not take is lost.
- On the unchanged build (`int-repeat-ms=0`) 30 scripted ON taps gave 5 zero-read taps, 8 read-but-unchanged taps
  and 17 changed taps from one start frame. A lost release pulse leaves the guest without the Lift Up report.

## Change

- `qemu-a6400/qemu-overlay/hw/misc/cxd90045_touch.c`: while `pending_down || active_contact || pending_up` is true, a
  `QEMU_CLOCK_VIRTUAL` timer re-drives the INT line with a fresh high-to-low edge every `int-repeat-ms` (uint32
  qdev property, default 10; 0 keeps the old single-edge behaviour). The timer is armed when `touch_input_sync`
  asserts the line and stopped in `touch_prepare_burst` and reset when nothing is pending. The device has no vmstate,
  so none was added.
- The period is a declared assumption: FocalTech trigger-mode INT pulses once per report while a touch is active.
  The 10 ms period was not captured, and register 0x88 (active scan period 6) is not decoded.
- `tests/test_qemu_peripherals_and_models.py`: a host-independent static source check that the property default is 10
  and that the timer is only armed while the line is asserted.

## Method

QEMU 11.1.3 was built on Linux from this tree's `qemu-a6400/setup-qemu.sh` (`qemu-system-arm` sha256
`5719fe6fc542…`). One build was used for both arms; the arm is selected by the property:

- B (old behaviour): `-global cxd90045-touch.int-repeat-ms=0`
- A (new behaviour): property left at its default 10 ms

Each arm ran one panel session on the same captured ILCE-6400 inputs and the same relocatable `MenuTouchFull` mod,
switched ON through the Mods menu. `tap_test.py` sent one Playback-tab tap per trial, 30 trials per arm, and recorded
the QOM `touch-bursts`/`touch-events` deltas and the tab-bar hash. A tap with `touch-bursts` delta 0 is a zero-read
tap; a changed tap is one whose end tab-bar strip is a Playback tab. Both arms reported the same start frame
`c4f899acab3f…`.

The mod, key navigation and the zero default arm are declared stimuli (`cond. PROBE`). No GIC trace was recorded;
the counters are QOM reads, not guest accesses.

## Result

| Check | B: `int-repeat-ms=0` | A: default 10 |
| --- | --- | --- |
| Zero-read taps | 5 of 30 (trials 5, 9, 14, 24, 29) | 0 of 30 |
| Changed taps | 17 of 30 | 30 of 30 |
| Read taps that did not change (delivered misses) | 8 of 30 (trials 13, 17, 18, 20, 26, 27, 28, 30) | 0 of 30 |
| Start frame hash | `c4f899acab3f…` | `c4f899acab3f…` |

## Limitations

- One build, one input set, one 10 ms period and two sequential panel sessions (B then A), not interleaved arms.
- The period is an assumption, not a capture. Other periods and other tap lengths are untested.
- All 5 zero-read B trials were unchanged; no trial changed with a zero read count. The change flag is the tab-bar
  hash 5 s after the tap.
- While a report stays unread (for example a finger held down and never read), the 10 ms edge repeats with no cap,
  as a scanning controller would keep reporting.
- No GIC trace was recorded, so the number of pulses taken by the CPU is not measured directly.
- The static test checks the source text, not the running timer.

## Reproduce

Build with `qemu-a6400/setup-qemu.sh` and run the panel and tap tooling as the lead command list records. The
per-trial evidence is `loop/scratch/L0183/on-A.csv`, `on-B.csv` and the named frame pairs there.
