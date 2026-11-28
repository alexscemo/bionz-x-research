# E0746 — QEMU BCM4339 SDPCM transmit credit and the bcmdhd `dhd_bus_start` stall

Date: 2026-11-27.

This record covers one emulator change: the modelled BCM4339 Fn2 SDPCM reply now carries a
moving transmit credit derived from the host sequence number, so the guest's `bcmdhd.ko` can
send more than nine control frames. It is an offline emulator result; it claims no physical
camera result.

## Observation

- Panel arm A (unchanged model, `qemu-system-arm` SHA-256 `1971eb24…`): after
  `dhd_bus_init: enable 0x06, ready 0x06` and `Firmware up`, the driver logs
  `dhd_bus_txctl: ctrl_frame_stat == TRUE txcnt_timeout=1` at guest 12.134 s and
  `txcnt_timeout=2` at 14.134 s, then `dhdsdio_probe: dhd_bus_start failed`.
- Trace on the unchanged source with temporary logging (watched: the model's Fn2 control-frame
  write handler and Fn2 read path, from the first Fn2 write to the first txctl timeout, one
  session): exactly **9** valid control frames were written, with host sequence bytes
  `0xFF, 0x00, 0x01, … 0x07`; every reply carried SDPCM header byte 9 (flow control) = `8`; no
  tenth control frame was written before the 2 s timeout. The trace build was a temporary build of
  the unchanged source and is not retained; the final A build above reproduces the same timeout.
- The guest's own `bcmdhd.ko` (read from the captured storage image, not kept in the
  repository) gives the contract. In `dhd_bus_txctl` the driver sets `ctrl_frame_stat`, queues
  the frame and waits; the worker sends it only while
  `0 < (uint8_t)(tx_max - tx_seq) <= 0x70`, where `tx_max` is header byte 9 of the last
  received SDPCM frame and `tx_seq` is the host sequence byte. The worker increments `tx_seq`
  after each successful write, and receive processing stores header byte 9 into `tx_max`.
  With the fixed advertised `8`, after the ninth write `tx_seq` is `0x08` and the difference is
  zero, so the tenth queued frame is never sent and `ctrl_frame_stat` stays true.

## Change

`qemu-a6400/qemu-overlay/hw/misc/cxd90045_bcm4339.c`: `bcm4339_handle_f2_write` now records
`sdpcm_tx_credit = (buf[4] + sdpcm_tx_max_seq) & 0xFF` for every valid SDPCM frame from the
host and puts that value in reply header byte 9 (and in asynchronous data frames). `buf[4]` is
the host transmit sequence the driver writes; the trace above shows it taking `0xFF, 0x00, …`.
The reset value is the same window. The window `8` is the model's pre-existing
`sdpcm_tx_max_seq` constant and remains an assumption; the sequence handshake is taken from the
driver contract. No value is forced and the change does not branch on a guest PC.

## Result

Both arms used the same `--bcm4339` and `-nic none` command line (quoted from
`/proc/<pid>/cmdline` of the running QEMU process), the same mod and loader files and the same
inputs, and started from the same start-frame hash `68c4060f…`. The only source difference
between the two builds is `cxd90045_bcm4339.c`.

| Measure | A: unchanged model | B: this change |
| --- | --- | --- |
| QEMU `qemu-system-arm` SHA-256 | `1971eb24…` | `f2cace7e…` |
| `dhd_bus_txctl: ctrl_frame_stat == TRUE`, sessions | **1 of 1** | **0 of 1** |
| `dhdsdio_probe: dhd_bus_start failed`, sessions | **1 of 1** | **0 of 1** |
| First post-init lines | none | `Firmware version = BCM4`, `Dongle Host Driver, version 1.141.67`, `Register interface [wlan0]` |
| `wlan0` in `/sys/class/net` at the 60 s probe | no | no |
| Next failure | the txctl timeout | `wl_cfg80211_up : Version mismatch, please upgrade. Got 12, expected 2 or 1`; `dhd_open: failed to bring up cfg80211` |

- **Hypothesis verdict: supported.** Naming and modelling the unmet condition (SDPCM transmit
  credit) makes `dhd_bus_start` succeed. The driver reads a firmware version, registers `wlan0`
  and moves to a later, different error.
- **Trace after the change:** the traced fixed build (`qemu-system-arm` SHA-256 `8c57f4ab…`,
  temporary logging) wrote **24** control frames and logged no txctl timeout, against 9 and a
  stall on the unchanged source.
- **Not solved:** `wlan0` is registered and then removed again. `dhd_open` fails on the cfg80211
  version check (`Got 12, expected 2 or 1`; the model returns 12 for `WLC_GET_VERSION`), and the
  mod powers the WLAN block off afterwards, so the 60 s `ifconfig -a` and `/sys/class/net`
  probes show no `wlan0`. This is recorded as the next gap, not as a Wi-Fi result.
- **Tests:** `qemu-a6400/tests/test_qemu_l0189_bcm4339_ctrl.py` drives the SDIO host at
  `0xF0301000` through qtest only, sends 12 `WLC_GET_VERSION` control frames with sequence bytes
  `0xFF, 0x00, … 0x0A` and checks that reply byte 9 is `(seq + 8) mod 256` and that the command
  is echoed. All 25 checks pass on build B. Negative control: the same qtest file against build
  A fails 11 of 25 checks (for example `P.fc_seq_ff: got 8 want 7`), so it is not a source
  grep and not a tautology.
- **Sessions counted:** four panel sessions in total (trace on unchanged source, trace on the
  fixed source, final A arm, final B arm), plus the two qtest sessions (B pass, A negative).

## Commands

- Panel arms: a copy of the project panel runner with `-nic none` added to the QEMU options,
  with `--inputs`, `--loader`, `--mod` (WiFiSync) and `--bcm4339`, one session per arm.
- Trace: the same runner on temporary builds of the unchanged and fixed model with temporary
  logging lines; those lines were removed afterwards and are not part of the change.
- Test: `A6400_QEMU_DIR=<build> python3 qemu-a6400/tests/test_qemu_l0189_bcm4339_ctrl.py`
- Negative control: the same command with `A6400_QEMU_DIR` pointing at the unchanged build.

## Inputs and stimuli

- Camera-captured boot inputs (kernel, initrd, eMMC image, backup area, camera CPU image,
  sub-micro camera page) are not in the repository.
- Declared stimuli: `cond. PROBE bcm4339=on` (the model is opt-in), the repository-built Mod
  Loader v2 core and the WiFiSync mod; temporary trace logging in the two trace sessions only.
- The guest had no network route: `-nic none` is in the saved command line of the running QEMU
  process, with no `-netdev`, `-net` or `-hostfwd`.

## Limits

- Emulator only; the captured inputs and the driver are not in the repository, so the runs
  cannot be reproduced without them. No physical camera result is claimed.
- One panel session per final arm and one trace session per source; the credit window `8` is an
  assumption, not a capture or datasheet value.
- Flow control is not modelled: every reply advertises the last host sequence plus 8, so the
  window never closes. A driver fault in credit handling would not show up in this model.
- The trace watched the Fn2 write/read paths only; other SDIO or backplane accesses in the same
  window are unobserved.
- `wlan0` does not survive the later cfg80211 version failure; this record does not claim a
  working Wi-Fi interface.
