# E0749 — QEMU BCM4339 `WLC_GET_MAGIC` reply lets the WiFiSync AP script pass `wl bands`

Date: 2026-11-27.

This record covers one emulator change in the modelled BCM4339 SDIO control channel: CDC
command 0 (`WLC_GET_MAGIC`) now answers `WLC_IOCTL_MAGIC` instead of zeros. It is an offline
emulator result; it claims no physical camera result.

## Observation

- Broadcom's public `wlioctl.h` defines `WLC_IOCTL_MAGIC` as `0x14e46c77`, the value its `wl`
  tool expects from `WLC_GET_MAGIC` (CDC command 0, 4-byte buffer) before it uses an adapter.
- The on-flash `wl` tool (in the camera flash image, not in the repository) carries the names
  `driver_magic`, `get_magic`, `wl_validatedev`, `wl_check` and `wl_get` and the string
  `%s: wl driver adapter not found`. The little-endian byte sequence of the magic value appears
  twice in the guest's Wi-Fi driver module. The compare instruction was not recovered (the `wl`
  file is block-compressed), so the check is inferred; the A/B below is the evidence.
- A temporary trace build logged every Fn2 control request and the first 32 reply bytes. It
  recorded 11 command-0 requests after attach, each `clen=4 payin=4`, each answered
  `776ce414`. The old model has no command-0 branch, so its reply is zeros (from the source).
- The same trace shows command 140 (`WLC_GET_BANDLIST`) answered `020000000100000002000000`
  and the `chanspecs` GET_VAR answered by the existing model. The trace does not show which
  guest process sent a request, and its first command-0 requests are a few seconds before the
  panel driver noted the toggle.

## Change

`qemu-a6400/qemu-overlay/hw/misc/cxd90045_bcm4339.c`:

- CDC command 0 (`WLC_GET_MAGIC`) replies the 4-byte `0x14e46c77` (`WLC_IOCTL_MAGIC`, public
  `wlioctl.h`) and reports a 4-byte payload.

No value is forced, the model does not branch on a guest PC, and guest RAM is reached only
through the modelled DMA. The trace logging was temporary, lived only in a scratch build, and is
not part of the change.

## Result

Both panel arms used `--bcm4339` and `-nic none` (quoted from `/proc/<pid>/cmdline` of the
running QEMU process), the same mod and loader files and the same inputs, and started from the
same pre-ON start-frame hash `f60f46a3…`. The only source difference is `cxd90045_bcm4339.c`.

| Measure | A: base build `59f122d8…` | B: this change `828c8e28…` |
| --- | --- | --- |
| command-0 branch in the model | none (zeros) | replies `0x14e46c77` |
| WiFiSync `ap.log` | `bands` empty, `5g_unavailable`, `down` | `bands a b`, `chanspec 36/80`, `up ok 36/80` |
| DHCP server on `wlan0` (from `ap.log`) | not started | `Listening on` … `192.168.122.0/24` |
| `wl -i wlan0 bands` at the 60 s probe | `wl: wl driver adapter not found` | `a b` |
| `wl -i wlan0 chanspec` at the 60 s probe | not relevant (no adapter) | `(0x0)` |
| `wlan0` in `/sys/class/net` at the 60 s probe | no | yes |
| qtest `P.magic` | 0 of 1 | 1 of 1 |

- **Hypothesis verdict: upheld.** With the magic reply `wl bands` is non-empty, the AP script
  passes its 5 GHz check, runs to its end and `wlan0` stays present at the 60 s probe. The base
  build stops at `5g_unavailable` and `wlan0` is removed. Panel lines other than the 60 s
  probes carry no wall clock.
- **What `up ok` does not show.** The script prints it unconditionally at its end: its wait for
  `wpa_state=COMPLETED` can time out and the `dhcpd` start is not checked. `chanspec 36/80` is
  the script's intent: the model stores no chanspec, and the guest's own query answers `(0x0)`.
  `wl status` fails with a `bss_info_version 0` message. The model answers the other commands
  with success and zeros and holds no radio, beacon or client state, so this is not evidence of
  a working access point, a client connection or traffic, and a real failure at those steps
  would be hidden.
- **Next checks (not done):** the supplicant log and state in access-point mode, and the
  replies behind `wl chanspec` and `wl status`.
- **Sessions:** four panel sessions (one base arm, one final arm, one successful trace arm, and
  one trace launcher that failed before QEMU started because its build path was relative) and two
  qtest sessions. The failed launcher produced no result and is not counted as a panel result.
- **Trace build:** retained `810fb336…`; the arm is counted only because that build still exists
  and the check reports it retained.

## Commands

- Panel arms: a copy of the project panel runner with `-nic none`, `--inputs`, `--loader`,
  `--mod` (WiFiSync) and `--bcm4339`, one session per arm; the exact argv is saved in each run's
  `commands.txt`, and the running QEMU's `/proc/<pid>/cmdline` is saved beside it.
- Trace: the same runner on the temporary trace build with `A6400_BCM4339_TRACE=1`.
- Test: `A6400_QEMU_DIR=<build> python3 qemu-a6400/tests/test_qemu_l0192_bcm4339_magic.py`
- Negative control: the same command with `A6400_QEMU_DIR` pointing at the base build.

## Inputs and stimuli

- Camera-captured boot inputs (kernel, initrd, eMMC image, backup area, camera CPU image,
  sub-micro camera page) are not in the repository.
- Declared stimuli: `cond. PROBE bcm4339=on` (the model is opt-in), the repository-built
  WiFiSync mod and loader, and the recorded panel inputs.
