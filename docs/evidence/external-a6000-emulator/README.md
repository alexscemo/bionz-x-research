# External ILCE-6000 emulator receipt

Date: 2026-11-27. Contributor: `voxivoid`. Scope: the `sony-a6000` QEMU machine for the
ILCE-6000, which uses the **CXD90014** SoC rather than the CXD90045. This record is
**not an upstream a6400 milestone**. It does not change the a6400 status snapshot or its
test count.

Source: [voxivoid/a6000-qemu](https://github.com/voxivoid/a6000-qemu), commit `62cf3bc`
(QEMU 10.1.0). That project is a fork of
[Oein/a5000-emulator](https://github.com/Oein/a5000-emulator), base commit `6ad36bd`.
This repository's port is `emulators/a6000/qemu-overlay/hw/arm/sony_a6000.c`, built on the
pinned QEMU 11.1.3. Scripts and instructions are in
[emulators/a6000/README.md](../../../emulators/a6000/README.md).

## Inputs

All inputs stayed on the contributor's computer, and none are in this repository:

- Sony's public updater `Update_ILCE6000V321.exe`, firmware **3.21**, unpacked with fwtool.
- The contributor's own camera NAND dump, provisioned into an emulated NAND with Sony's
  own NAND stack (`provision.sh`).

| Input | SHA-256 |
|---|---|
| `Update_ILCE6000V321.exe` | `0c3e61b03daafa469f4dfcb04decf0983088e609d7c32a29f356dab57ce45d57` |
| `FirmwareData_ILCE6000V321.dat` (cut from the `.exe`) | `29aef21ab9b5c2e98388974ac9749c2cbfb1815273b444e62f2e41398aa8892f` |
| `vmlinux.bin` (updater, `nflasha3`) | `1ad249a2f231785e8d036fb8be2bbb90ca7bcbb00fd24f4a87647a92acd55053` |
| Emulated NAND (owner's dump) | not published: it holds the owner's device data |

Builds: QEMU 11.1.3 (`005c61c0ef6f6ae615283440b4bb7be51b2c6ddd`) staged by
`emulators/a6000/setup-qemu.sh` from this branch and configured with
`--target-list=arm-softmmu --enable-sdl --disable-werror`; the source build is
voxivoid/a6000-qemu `62cf3bc` on QEMU 10.1.0.

## Commands

From `emulators/a6000/`, each run on a fresh copy of the same emulated NAND
(`A6K_NAND=<copy>`):

```sh
./run.sh -display none -monitor unix:<sock>,server,nowait            # boot, then HMP:
#   sendkey ret 300 / sendkey m 300 / sendkey down 300 ... ; screendump <file>.ppm
./run.sh -display none -monitor tcp:127.0.0.1:<p>,server,nowait \
  -chardev socket,id=nfc,host=127.0.0.1,port=<n>,server=on,wait=off \
  -M usb-socket=127.0.0.1:<u>,nfc=nfc,hdmi=on,lens=ekit \
  -netdev user,id=w0 -global ar6003-sdio.netdev=w0 \
  -global 'ar6003-sdio.aps=ssid=TestAP,psk=testpass123'
#   SD: A6K_SD=<blank SDHC from tools/mksd.py>; guest shell (A6K_SHELL=<port>,
#       tools/a6kshell.py): grep mmc /proc/partitions; dmesg | grep mmc
#   USB: Sony-PMCA-RE with tools/pmca-socket/: pmca-console info
#   NFC: write "tap 1000" to the nfc chardev; then ifconfig -a, lsmod, dmesg | grep ath
#   HDMI: screendump <file>.ppm vph 1   (console 1)
```

Screendumps were compared by MD5 between the two builds at each step.

## Identity

| Claim | Value | Basis |
|---|---|---|
| SoC | `CXD90014` | The updater's `config.yaml` states `crypterName: CXD90014`. Its 3.21 `vmlinux.bin` contains `arch/arm/mach-cxd90014/` paths and `cxd90014_*` symbols, such as `cxd90014_pm_init`. |
| Firmware version | `3.21` | The updater that was booted (`Update_ILCE6000V321.exe`). |
| Kernel version | `3.0.27_nl-rt106` | `uname -r` in the guest root shell (`tools/a6kshell.py`) during an emulated boot of that updater's kernel. |

## What was observed

Every item below was run twice: on the QEMU 10.1.0 build from the source repository, and
on this repository's QEMU 11.1.3 build. Both runs used a fresh copy of the same emulated
NAND.

| Item | Result |
|---|---|
| Two-stage boot, first-boot setup dialog, shooting screen, MENU | Screendumps are byte-identical between the two builds at each step, and over three runs each. |
| HDMI console 1 at 1080p | Byte-identical between the two builds. |
| SD card | `mmca10: p1` detected in 6 of 6 runs. |
| USB | Sony-PMCA-RE `info` over the emulator's USB socket reports firmware 3.21, in 6 of 6 runs. |
| NFC | A virtual phone `tap` reads the PlayMemories NDEF record and starts Smart Remote Embedded. A second `tap` in the same session times out on both builds. With three emulators running in parallel on one host, one first `tap` also timed out; two in parallel did not. |
| Wi-Fi | After the NFC tap, the stock `ath6kl_sdio` loads on the AR6003 SDIO model (`ar6003 hw 2.1.1 sdio fw 3.4.0.189`) and `ath0`/`ath1` come up. 3 of 3 runs on each build. |
| Guest time | Without `-icount`, 1 of 6 parallel runs reached the UI. With `-icount shift=0`, 16 of 16 did (8 with `sleep=off`, 8 with `sleep=on`). `run.sh` now passes `-icount`. |

The tag answer contains the camera's Wi-Fi Direct SSID and password, so no NFC log is
included here.

## Changes to shared models

These properties are added by the ILCE-6000 patch
(`emulators/a6000/patches/`), so `qemu-a6400/` itself is unchanged. They are
opt-in, and their defaults keep the a6400 behaviour. The native qtests show no new
failures against a baseline run on the unchanged tree.

- `cxd90014-vph` `scan-without-arm`: composes every frame, as the a5000 model did. With
  the per-frame arm rule (E0639), the ILCE-6000 panel could turn black after the UI had
  been shown. In one run, a screendump showed the setup dialog at 200 s and a black panel
  at 260 s. The firmware does not re-arm the OSD channel for a static screen.
- `cxd90014-gpio` `pulse-us`: pin IRQ pulse width. `sony-a6000` uses 1 ms.
- `cxd90014-adc`: a per-channel idle level for the ILCE-6000 mode-dial ladders.

## Boundaries

- At the date of this record the A6000 booted only with the user's own NAND dump.
  This boundary no longer holds since 2026-11-28; see the update below.
- Not shown: still capture (no ISP or live-view model), lens behaviour, EVF and eye
  sensor, control dial, power-cycle resume screen, PMCA app install.
- The emulated Wi-Fi module is an AR6003 model. It is not established that the ILCE-6000
  carries an AR6003.
- These are emulator observations. Nothing was run on the camera for this record.

## Update 2026-11-28: NAND without a camera dump

`emulators/a6000/tools/mknand_synth.py` builds the NAND image from the updater alone: its
partition table and images, the factory-default `Backup.bin` template from `0110_backup/`,
and `DmmConfig.bin` and `ulogio.bin` rebuilt from tables in the factory warm-boot image.
`nflasha4` is written erased; the firmware writes its own records there.

Run in a copy of `emulators/a6000/` without `dumps/`, `state/`, `inputs.local.json` or
`data/`: `tools/mkdata.py` → `mknand_synth.py` → `provision.sh` → `run.sh` (and
`tools/camui.py --launch`). The firmware reached the shooting screen and the menus, and
the kernel log had no oops. The NAND file's SHA-256 did not change.

Still missing compared with a real camera: the per-unit calibration (protected
`Backup.bin` props, `/setting/psd.bin`), the updater core (`nflasha1`), and owner data.
[emulators/a6000/README.md](../../../emulators/a6000/README.md#what-a-synthetic-nand-lacks)
lists each item and what it is for. On the synthetic NAND the shooting screen has shown
a SteadyShot warning icon whose cause is not known.
