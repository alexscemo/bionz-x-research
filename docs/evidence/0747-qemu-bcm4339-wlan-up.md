# E0747 — QEMU BCM4339 control replies: `WLC_GET_VERSION` and `WLC_GET_BANDLIST`

Date: 2026-11-27.

This record covers one emulator change in the modelled BCM4339 SDIO control channel: the
`WLC_GET_VERSION` reply now carries the ioctl interface version (`2`) instead of the driver
version (`12`), and `WLC_GET_BANDLIST` returns both bands instead of an empty list. It is an
offline emulator result; it claims no physical camera result and no working Wi-Fi interface.

## Observation

- Panel arm A (base build, `qemu-system-arm` SHA-256 `f2cace7e…`): `bcmdhd.ko` reaches
  `Firmware up: op_mode=0x0002` and `Register interface [wlan0]`, then logs exactly
  `CFG80211-ERROR) wl_cfg80211_up : Version mismatch, please upgrade. Got 12, expected 2 or 1`
  and `dhd_open: failed to bring up cfg80211`; `wlan0` is gone from `/sys/class/net` at the
  60 s probe and the WiFiSync mod logs `ap_rc 4`, `power_apply_failed 2`.
- Two trace sessions with temporary logging (watched: the model's Fn2 control-frame handler;
  the lines carry command number, lengths and variable name, not reply values). One ran the
  base source; that temporary build was not retained, so the session is unverified. The other
  ran the changed source with logging (SHA-256 `5ab98e57…`) and ends two seconds before the mod
  was switched ON, so it covers driver bring-up only, not the access-point requests. The driver
  sends `WLC_GET_VERSION` (CDC command 1), ioctl 140 and a 1024-byte read of the `chanspecs`
  variable. The base build's reply values (`12`, an empty band list, zero bytes) come from its
  source and the qtest below, not from a trace. The logging is not part of the change.
- The guest's own `bcmdhd.ko` (read from the captured storage image, not kept in the
  repository) gives both contracts.
  - `wl_cfg80211_up` -> `wl_cfg80211_get_ioctl_version` accepts the dongle value only while
    `(value - 1) <= 1` (unsigned), and the mismatch message prints the expected constant `2`
    with the literal old value `1`. `2` is `WLC_IOCTL_VERSION` from Broadcom's `wlioctl.h`;
    `12` is the driver/firmware version and is not this field.
  - `wl_update_wiphybands` calls the ioctl 140 path with a 12-byte buffer and then walks it as
    `{ uint32 count; uint32 band[count] }`, comparing each band to `WLC_BAND_2G` (1) and
    `WLC_BAND_5G` (2) from the same header.

## Change

`qemu-a6400/qemu-overlay/hw/misc/cxd90045_bcm4339.c`, in `bcm4339_handle_f2_write`:

- `WLC_GET_VERSION` (command 1) replies `2` (`WLC_IOCTL_VERSION`) instead of `12`.
- `WLC_GET_BANDLIST` (command 140, at least 12 reply bytes) replies
  `{ count = 2, band[0] = 1, band[1] = 2 }` (`WLC_BAND_2G`, `WLC_BAND_5G`).

The BCM4339 is a dual-band part, so advertising both bands is a capability reply, not a scan
result. No value is forced, the model does not branch on a guest PC, and guest RAM is only
reached through the modelled DMA. The `chanspecs` channel list is deliberately not modelled.

## Result

Both arms used the same `--bcm4339` and `-nic none` command line (quoted from
`/proc/<pid>/cmdline` of the running QEMU process), the same mod and loader files and the same
inputs, and started from the same start-frame hash `68c4060f…` (the pre-ON `row1`/submenu frame;
the `on0` frame is `189be544…` in both arms). The only source difference between the two builds
is `cxd90045_bcm4339.c`.

| Measure | A: base build | B: this change |
| --- | --- | --- |
| QEMU `qemu-system-arm` SHA-256 | `f2cace7e…` | `ad33c1bf…` |
| `wl_cfg80211_up : Version mismatch … Got 12`, sessions | **1 of 1** | **0 of 1** |
| `dhd_open: failed to bring up cfg80211`, sessions | **1 of 1** | **0 of 1** |
| `Register interface [wlan0]` | yes | yes |
| `wlan0` in `/sys/class/net` at the 60 s probe | no | no |
| WiFiSync `ap.log` | `bands` empty, `5g_unavailable`, `down` | `bands` empty, `5g_unavailable`, `down` |
| WiFiSync mod result | `ap_rc 4`, `power_apply_failed 2` | `ap_rc 4`, `power_apply_failed 2` |

- **Hypothesis verdict: supported for the version error, not for keeping `wlan0` up.** The
  version mismatch and the failed `dhd_open` are gone; the driver registers `wlan0` and reaches
  the AP bring-up. It is still removed: the AP script's `wl bands` is empty, so the
  script reports `5g_unavailable`, returns 4, and the mod powers the block off. That the empty
  output is caused by `wl_update_wiphybands` having no channels is an inference, not observed.
- **Next failure, named:** the `chanspecs` iovar (CDC command 262, GET_VAR, 1024-byte request)
  is answered with zeros; the inference (from the driver contract and the reply sizes, not
  observed) is that the driver therefore builds no channels. A correct reply is a structured channel list (per-band chanspec/count data), which
  is a scan/channel result rather than a single reply value; the lead stops here as instructed.
- **Tests:** `qemu-a6400/tests/test_qemu_l0190_bcm4339_wlan_up.py` drives the SDIO host at
  `0xF0301000` through qtest only, sends `WLC_GET_VERSION` and `WLC_GET_BANDLIST`, and checks the
  echoed command, the version value and the band list. All 4 checks pass on build B; only the
  version and band-list checks can fail meaningfully (the two echo checks compare with the
  command the test itself wrote). Negative
  control: the same qtest file against build A fails 2 of 4 (`P.version: got 12 want 2`,
  `P.bandlist: got [0, 0, 0] want [2, 1, 2]`), so it is a runtime control, not a source grep.
- **Sessions counted:** five QEMU panel sessions (one version-only intermediate arm whose build and
  command line were not recorded, two trace sessions with temporary logging, the final A arm,
  the final B arm) and two qtest sessions.
  A sixth launcher attempt stopped before boot because its run directory already held a log
  file; it produced no panel session and is not counted as an arm.

## Commands

- Panel arms: a copy of the project panel runner with `-nic none`, `--inputs`, `--loader`,
  `--mod` (WiFiSync) and `--bcm4339`, one session per arm; the exact argv is saved in each
  run's `commands.txt`, and the running QEMU's `/proc/<pid>/cmdline` is saved beside it.
- Trace: the same runner on temporary builds of the model with temporary logging lines, which
  are not part of the change.
- Test: `A6400_QEMU_DIR=<build> python3 qemu-a6400/tests/test_qemu_l0190_bcm4339_wlan_up.py`
- Negative control: the same command with `A6400_QEMU_DIR` pointing at the base build.

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
- One panel session per final arm; the version-only intermediate arm and the two trace sessions
  are counted separately.
- The band list reply is a capability value; the chip's channel set is not modelled. The AP
  path stops at the empty `chanspecs` list, so this record does not claim a working AP, a scan,
  an association or an open `wlan0`.
- The trace watched the Fn2 write/read paths only; other SDIO or backplane accesses in the same
  window are unobserved.
