# E0745 — QEMU BCM4339 EROM encoding and the bcmdhd `si_attach` failure

Date: 2026-11-27.

This record covers one emulator change: the modelled BCM4339 Silicon Backplane enumeration ROM
(EROM) is re-encoded in the AI EROM format that the guest's own `bcmdhd.ko` scans. It is an
offline emulator result; it claims no physical camera result.

## Observation

- Panel arm A (unchanged model, `qemu-system-arm` SHA-256 `6ece40b3…`, `--bcm4339`, SDIO card
  enumerated): `dhdsdio_probe_attach: si_attach failed!`, then
  `dhdsdio_probe: dhdsdio_probe_attach failed` and `bcmsdh_sdmmc: probe of mmc0:0000:2 failed
  with error -12`. The module load times out. The baseline trace session below shows the same
  `si_attach failed` line: 2 of 2 sessions with the unchanged ROM.
- Trace of the backplane words read through the Fn1 window on the unchanged ROM (watched: the
  model's backplane word read function only, one session, `insmod` at guest 9.70 s). The driver
  reads ChipCommon `0x18000000` = `0x10044339`, the EROM pointer `0x180000FC` = `0x1810A000`,
  then exactly two EROM words: `0x1810A000` = `0x4BF80001` (CIA) and `0x1810A004` =
  `0x00000113` (CIB). No further EROM word is read and `si_attach` fails.
- The two trace sessions used temporary logging lines in scratch builds (SHA-256 `82677fc8…`
  with the unchanged ROM, `2b927486…` with the fixed ROM). Those lines are not part of this
  change and were removed afterwards, so the trace cannot be reproduced from the repository.
- The CIB `0x113` is not a valid component descriptor for this driver: its tag field
  (bits 1-3) is `0b001` (bit 1 set) instead of `ER_CI` (0), and its NSP field is zero. The
  driver aborts the EROM scan, so `si_attach` returns failure.

## Parse contract (verified from the guest driver)

The guest's own `bcmdhd.ko` (read from the captured storage image, not kept in the
repository) contains the Silicon Backplane `ai_scan` path. It parses each
component as:

1. CIA: designer (bits 31-20), part (bits 19-8), valid bit 0; tag bits 1-3 = `ER_CI`.
2. CIB: revision (31-24), NSW (23-19), NMW (18-14), NSP (13-9), NMP (8-4), valid bit 0;
   tag bits 1-3 = `ER_CI`. The scan skips a component whose NSP is zero, and one with
   `NMW+NSW == 0` unless its part is a known no-wrapper component.
3. NMP master-port descriptors (`ER_MP`).
4. Address descriptors (`ER_ADD`): first the slave register space (port 0, type slave), extra
   address spaces in that port, other slave ports, then NMW master wrappers (type `MWRAP`) and
   NSW slave wrappers (type `SWRAP`). The address is bits 31-12; size code bits 5-4.
5. `ER_END|ER_VALID` terminates the table.

The field constants are the public Broadcom `aidmp.h` definitions (`ER_TAG`, `ER_CI`, `ER_MP`,
`ER_ADD`, `CIA_*`, `CIB_*`, `AD_*`), which the guest driver uses unchanged.

## Change

`qemu-a6400/qemu-overlay/hw/misc/cxd90045_bcm4339.c`: the `bcm4339_erom[]` table is rebuilt with
that encoding (macros `BCM_EROM_CIA/CIB/MP/ASD`). Each of the four components (ChipCommon
`0x800`, SDIO `0x829`, ARM CR4 `0x83e`, D11 `0x812`) is CIA, CIB with NMP=1, NSP=1, NMW=1,
NSW=0 (ChipCommon revision 4, from `BCM_CHIPCOMMON_ID`'s revision field), one master-port
descriptor, a 4 KiB slave address descriptor at the core base, and a 4 KiB master-wrapper
address descriptor at `0x1810N000`. The old four-word-per-core layout and its CIB `0x113` are
gone. No behavior is forced: the encoding comes from the driver's parse contract and the core map
already in the model. The revision value 0 for the SDIO, CR4 and D11 cores has no datasheet or
capture source; it is an assumption of the model, and the driver accepted it in the one run
below.

## Result

Both arms saved the same frame of the focused WiFiSync row (raw-row hash `68c4060f…`), used the
same `--bcm4339` and `-nic none` QEMU command line, the same mod and loader files and the same
inputs, and switched the mod ON (the loader log records it in both). The only source difference
between the two builds is `cxd90045_bcm4339.c`.

| Measure | A: unchanged ROM | B: this change |
| --- | --- | --- |
| QEMU `qemu-system-arm` SHA-256 | `6ece40b3…` | `1971eb24…` |
| `si_attach failed`, all sessions including traces | **2 of 2** | **0 of 2** |
| First post-attach line | none | `DHD: dongle ram size is set to 65536(orig 65536) at 0x180000` |
| Bus init | none | `dhd_bus_init: enable 0x06, ready 0x06 (waited 0us)` |
| Next failure | none (stops at attach) | `dhd_bus_txctl: ctrl_frame_stat == TRUE txcnt_timeout=2`, `dhd_bus_start failed` |
| `insmod bcmdhd.ko` | `Timer expired` | `Timer expired` (a later step) |
| `ifconfig -a` / `/sys/class/net` | no `wlan0` | no `wlan0` |

- **Hypothesis verdict: supported.** With the corrected ROM the driver scans the four
  components, attaches, reads a dongle RAM size and completes the bus enable/ready handshake.
- **Not solved:** `wlan0` still does not appear. The driver now fails at the Fn2 SDPCM/BCDC
  control channel: the first control frame is not acknowledged (`ctrl_frame_stat == TRUE`, two
  2 s timeouts, `dhdcdc_msg failed w/status -110`), so `dhd_bus_start` fails.
- **Inference, not tested:** the RAM address the driver reports (`0x180000`) lies outside the
  model's TCM window (`0..0xBFFFF`), where reads return 0 and writes are dropped. The driver
  still logs `Firmware up` before the control timeout, so this is recorded as a possible gap in
  the model, not as the cause of the timeout.
- **Trace after the change:** the driver reads the 21 EROM words from `0x1810A000` to
  `0x1810A050` (20 descriptors and the end marker `0x0000000F`), then the core registers, and
  logs no `si_attach` error.
- **Tests:** `qemu-a6400/tests/test_qemu_l0188_bcm4339_attach.py` re-implements the scan walk in
  Python and reads the C table. Five checks pass on the new table, including ChipCommon rev 4, a
  master wrapper for every core, and rejection of the old CIB `0x113`. Negative control: the
  same test run against the old table fails (exit 1, `CIA not followed by CIB (tag bits)`).
- **Normal boot:** the project's boot scorer (two runs, Wi-Fi model off) reports the same final
  state as the baseline (sequencer index 42, step `0x897F`, 0 mismatches, 0 unmodelled pairs),
  47.12 s and 52.30 s against 52.28 s; neither better nor worse.

## Commands

- Panel arms: a copy of `qemu-a6400/tools/rear_panel.py` with `-nic none` added to the QEMU
  options, with `--inputs`, `--loader`, `--mod` (WiFiSync) and `--bcm4339`.
- Test: `python3 -m unittest qemu-a6400/tests/test_qemu_l0188_bcm4339_attach.py -q`
- Negative control: the same command with `QEMU_BCM4339_SRC` pointing at a copy of the old
  source file.

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
- One trace session per ROM and one A/B panel session per arm.
- The trace watched only the backplane word read function; other accesses are unobserved.
- The ROM describes the four cores the model already implemented. It does not model the CR4 TCM
  as a separate address space and does not make the Fn2 control channel answer the driver.
- `wlan0` is absent in both arms; this record does not claim a working Wi-Fi interface.
