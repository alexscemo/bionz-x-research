# E0748 — QEMU BCM4339 `chanspecs` reply and multi-read Fn2 delivery

Date: 2026-11-27.

This record covers one emulator change in the modelled BCM4339 SDIO control channel: the
`chanspecs` iovar (CDC command 262, GET_VAR) now answers a `wl_uint32_list` of 5 GHz channels,
and the Fn2 read path keeps a queued frame until the host has read all of it. It is an offline
emulator result; it claims no physical camera result.

## Observation

- The guest `bcmdhd.ko` sends a 1024-byte `chanspecs` request during attach. Its
  `wl_construct_reginfo`/`wl_update_wiphybands` reads `{ uint32 count; uint32 element[count]; }`
  and accepts a 16-bit 11ac chanspec: band 5G `0xc000`, BW_20 `0x1000`, and channels above 33.
  The 11ac encoding is the one in Broadcom's public `bcmwifi_channels.h`; the driver's own
  validator is the authority for the accepted values.
- The old model answered the request with a zero list (count 0).
- The model also dropped a queued control reply after the first 32-byte Fn2 read, while the
  driver reads it in several chunks. Observed on a retained temporary trace build: a first read
  `req=32 copy=32`, then `req=32 copy=16 pos=48`. That is observed driver behaviour, not a
  documented contract.
- The trace build is not part of the change. It adds logging and a switch between two chanspec
  encodings (the final 11ac one and an older 16-bit one).

## Change

`qemu-a6400/qemu-overlay/hw/misc/cxd90045_bcm4339.c`:

- `WLC_GET_VAR` `chanspecs` replies `{ count = 4; 0xd024, 0xd028, 0xd02c, 0xd030 }`, the 11ac
  chanspecs for 5 GHz channels 36/40/44/48 at 20 MHz.
- The Fn2 read path tracks `f2_rx_pos`, returns the remaining bytes on the next read, and clears
  the queued frame only after `f2_rx_len` bytes have been delivered.

The four channels are the set the AP script accepts (36/40/44/48); they are an emulator
assumption, not a scan result or a hardware capability observation. No value is forced, the
model does not branch on a guest PC, and guest RAM is reached only through the modelled DMA.

## Result

Both panel arms used `--bcm4339` and `-nic none` (quoted from `/proc/<pid>/cmdline` of the
running QEMU process), the same mod and loader files and the same inputs, and started from the
same pre-ON start-frame hash `68c4060f…`. The only source difference is `cxd90045_bcm4339.c`.

| Measure | A: base build | B: this change |
| --- | --- | --- |
| QEMU `qemu-system-arm` SHA-256 | `ad33c1bf…` | `59f122d8…` |
| `chanspecs` list in the model reply | count 0 | count 4, `0xd024/0xd028/0xd02c/0xd030` |
| `Invalid channel Sepc.` lines in the serial log | 0 (no elements to check) | 0 |
| `wlan0` registered | yes | yes |
| `wlan0` in `/sys/class/net` at the 60 s probe | no | no |
| WiFiSync `ap.log` | `bands` empty, `5g_unavailable`, `down` | `bands` empty, `5g_unavailable`, `down` |
| `wl -i wlan0 bands` at the 60 s probe (after the script's `down`) | adapter not found | adapter not found |

- **Hypothesis verdict: falsified.** With the new reply the AP script still sees an empty
  `wl bands`, still reports `5g_unavailable`, and `wlan0` is still removed.
- **That the driver accepts the four values is inferred.** No log line reports an accepted list,
  and the zero-count base arm also logs no invalid channel. The supporting control is one trace
  arm on the retained trace build with the older encoding: the driver logged
  `Invalid channel Sepc. 0x824` (and `0x828`, `0x82c`), so it reads the element bytes and
  rejects bad ones by value. The final arm logs no rejection.
- **The next failing check is not established.** The script's `wl bands` result is empty; the
  likely request is `WLC_GET_BANDLIST` (command 140), which the model answers with two bands. In
  the saved trace arms command 140 appears at most once, during attach, before `chanspecs`
  (the retained arm included); no saved trace shows one from the script. Whether the script's command reaches the device is
  the next thing to measure. The `adapter not found` reply at 60 s comes after `down` and is not
  a bands result.
- **Tests:** `qemu-a6400/tests/test_qemu_l0191_bcm4339_chanspecs.py` drives the SDIO host at
  `0xF0301000` through qtest only, sends the `chanspecs` GET_VAR, and reads the reply in two
  32-byte chunks as the driver does. On build B all 3 checks pass (echo, count 4, the four
  chanspecs); on build A the echo passes and the count and chanspec checks fail (count 0, no
  elements), so the test is a runtime control, not a source grep.
- **Sessions:** twelve panel sessions (eight trace sessions, two guest probe sessions, the final
  A and B arms) and two qtest sessions. Without a full result: one trace session timed out with an
  empty run log, one probe session exited with an error, one probe session saved probe output only.
  Only two of the ten trace/probe sessions ran a trace build that still exists (one of those two
  is the timed-out one); the other eight are unverified and are not used as evidence above.

## Commands

- Panel arms: a copy of the project panel runner with `-nic none`, `--inputs`, `--loader`,
  `--mod` (WiFiSync) and `--bcm4339`, one session per arm; the exact argv is saved in each
  run's `commands.txt`, and the running QEMU's `/proc/<pid>/cmdline` is saved beside it.
- Trace: the same runner on a temporary trace build of the model; the logging is not part of
  the change.
- Test: `A6400_QEMU_DIR=<build> python3 qemu-a6400/tests/test_qemu_l0191_bcm4339_chanspecs.py`
- Negative control: the same command with `A6400_QEMU_DIR` pointing at the base build.

## Inputs and stimuli

- Camera-captured boot inputs (kernel, initrd, eMMC image, backup area, camera CPU image,
  sub-micro camera page) are not in the repository.
- Declared stimuli: `cond. PROBE bcm4339=on` (the model is opt-in), the repository-built
  WiFiSync mod and loader, and the recorded panel inputs.
