# External ILCE-7C USB identity receipt (Issue #60)

Date: 2026-11-21. Contributor: `mavanha` ([Issue #60](https://github.com/ivica00/bionz-x-research/issues/60)).
Scope: host-observed USB identities and `pmca` native MSC model metadata from a stock **Sony ILCE-7C** running firmware **2.00**. This record is **not an upstream a6400 milestone**. It does not change the a6400 status snapshot or its test count.

## Observations

All observations were reported from a stock physical ILCE-7C (FW 2.00); no proprietary firmware images, dumps, or binaries were attached or added to the repository.

### `pmca` native MSC output

- `model_name`: `ILCE-7C`
- `model_code`: `0032852231`
- `firmware_version`: `2.00`

### Windows PnP IDs by USB mode

Common USB VID: `0x054C`, `bcdDevice`: `0x0200`.

| Mode | VID:PID | `bcdDevice` | Host-reported description |
|---|---|---|---|
| Mass Storage (`mass_storage`) | `054C:0D29` | `0x0200` | 2 `Sony DSC` rev `1.00` LUNs (SD card + `PMHOME`) |
| MTP (`mtp`) | `054C:0D2A` | `0x0200` | WPD (`ILCE-7C`) |
| PC Remote (`pc_remote`) | `054C:0D2B` | `0x0200` | Sony Remote Control Camera (`ILCE-7C`) |
| USB Streaming (`usb_streaming`) | `054C:0DE6` | `0x0200` | Composite: `MI_00` (UVC) + `MI_02` (UAC) |

## Boundaries

- These USB PIDs (`0x0D29`, `0x0D2A`, `0x0D2B`, `0x0DE6`) and `model_code` (`0032852231`) were observed on firmware **2.00**; the offline `kallsyms` and `usbg_video.ko`/`usbg_audio.ko` entries in `ilce_7c` come from firmware **2.03** (`E0477`, `E0479`, `E0480`).
- Host-bus enumeration does not establish live `/proc/kallsyms`, `/proc/osal/uipc`, or daemon execution state.
