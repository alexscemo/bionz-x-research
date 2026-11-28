# Sony ILCE-6000 (α6000) emulator: `sony-a6000`

The `sony-a6000` machine runs the official ILCE-6000 firmware **3.21** without changes. It boots from an emulated NAND in two stages, as the real camera does, and reaches the camera UI.

The ILCE-6000 uses the **CXD90014** SoC (quad Cortex-A5); the `emulators/qemu-a6400/` machines use the CXD90045. Everything specific to the ILCE-6000 is in this directory:

- `qemu-overlay/`: the ILCE-6000-only device models and the `sony-a6000` board.
- `patches/qemu-v11.1.3-sony-a6000.patch`: build wiring, CPU, L2C-310 and SDIO changes, and the opt-in properties added to the shared `cxd90014_*` models.
- `setup-qemu.sh`: runs `emulators/qemu-a6400/setup-qemu.sh` unchanged, then applies the two above to the same QEMU 11.1.3 tree.

The machine reuses the shared `cxd90014_*`, `cxd4115_timer` and `dmp_*` device models from `emulators/qemu-a6400/qemu-overlay/`. Nothing under `emulators/qemu-a6400/` is modified.

It comes from [voxivoid/a6000-qemu](https://github.com/voxivoid/a6000-qemu) (commit `62cf3bc`, QEMU 10.1.0). That project is a fork of [Oein/a5000-emulator](https://github.com/Oein/a5000-emulator) (base commit `6ad36bd`). Paths under `docs/reference/` in source comments are files in voxivoid/a6000-qemu.

## What works

Each item was checked against the same run on the original QEMU 10.1.0 build. The two builds give identical screens.

| Area | Status |
|---|---|
| Two-stage boot: stage-1 loader with Sony's NAND stack, then the stock kernel and initrd from NAND | works |
| Stock init, Android, camera UI, first-boot setup, shooting screen, menus | works |
| Buttons and mode dial (key table from the camera's own `Backup.bin`) | works |
| Camera-body web UI (`tools/camui.py`) | works |
| USB device (Sony-PMCA-RE `info` over `tools/pmca-socket/`) | works |
| NFC tag read by a virtual phone; starts Smart Remote Embedded (Wi-Fi Direct) | works; a second `tap` in one session times out |
| Wi-Fi: stock `ath6kl_sdio` on the AR6003 SDIO model, `ath0`/`ath1` up | works; whether the A6000 has an AR6003 is not verified |
| HDMI 1080p output (console 1), CEC | works |
| SD card (`mmca10`) | works |
| Shutter half press (metering) | works; no capture, because there is no ISP or live-view model |
| Power off, then wake | wake works; the resumed screen is not checked |
| Lens (`lens=ekit`), EVF and eye sensor, control dial | not checked or not modelled |

### Guest time

`run.sh` runs QEMU with `-icount shift=0,sleep=on`, so guest time comes from the instruction count. Without it, guest time follows the host clock, and a busy host makes the firmware's timeouts fire. In testing with six emulators in parallel, 1 of 6 runs booted without `-icount` and 16 of 16 booted with it. Set `A6K_ICOUNT=off` to turn it off.

## Inputs you provide

Nothing in this directory contains Sony firmware or data from a camera. Every input below stays on your computer, and `.gitignore` keeps it out of Git.

**You need an ILCE-6000 that you own.** Sony's updater does not contain the data written to each camera at the factory: the settings store (`Backup.bin`), the calibration and the updater core. These exist only on the camera itself. A NAND dump also holds personal data: serial number, device name, Wi-Fi settings and face registrations. Never share it, and never use a dump of someone else's camera.

| Path (under `emulators/a6000/`) | Contents | From your camera? |
|---|---|---|
| `firmware/unpacked/…` | Official updater `Update_ILCE6000V321.exe`, unpacked with fwtool | no |
| `data/` | Hand-off page and sub-micro frames from the factory warm-boot image in the updater (`tools/mkdata.py`) | no |
| `dumps/` | Full NAND dump of **your own** camera | **yes** |
| `state/a6000.nand` | Emulated NAND, made by `provision.sh` | **yes** |
| `provision/a6kcopy`, `provision/*.cpio` | Built by the scripts from the updater | no |

### 1. Official updater

1. Download system software **3.21** for Windows (`Update_ILCE6000V321.exe`) from Sony's support site.
2. Get [fwtool.py](https://github.com/ma1co/fwtool.py) and its dependencies.
3. Current Sony downloads wrap the firmware in an archive that fwtool rejects with `Unknown exe file`. Cut out the `.dat` first. It starts at the magic `\x89UFU\r\n\x1a\n`, and its size is the u32 just before its UTF-16 name:
   ```sh
   python3 - <<'EOF'
   import struct
   d = open('firmware/Update_ILCE6000V321.exe', 'rb').read()
   name = 'FirmwareData_ILCE6000V321.dat'.encode('utf-16-le')
   size = struct.unpack('<I', d[d.index(name) - 4:d.index(name)])[0]
   off = d.index(b'\x89UFU\r\n\x1a\n')
   open('firmware/FirmwareData_ILCE6000V321.dat', 'wb').write(d[off:off + size])
   EOF
   python3 <fwtool.py>/fwtool.py unpack -f firmware/FirmwareData_ILCE6000V321.dat -o firmware/unpacked
   ```
   If the unpacked tree is somewhere else, set `A6K_FW=<dir>`.

### 2. Warm-boot image data

```sh
python3 tools/mkdata.py --fwtool <fwtool.py checkout>
```

### 3. NAND dump of your own camera (read only)

Get a root shell on the camera with [OpenMemories-Tweak](https://github.com/ma1co/OpenMemories-Tweak), installed through [Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE). Then copy the NAND to the SD card:

```sh
dd if=/dev/nflasha of=/android/mnt/sdcard/NAND.BIN bs=131072
busybox md5sum /dev/nflasha /android/mnt/sdcard/NAND.BIN   # both must match
```

The Sony-PMCA-RE updater shell can also do it over USB: `pull /dev/nflasha <dir>/nflasha.bin`.

**Never run a command with `of=/dev/nflasha*`.** Writing to the NAND can brick the camera.

### 4. Emulated NAND

```sh
./provision.sh dumps/<your dump>           # writes state/a6000.nand
cp --reflink=auto state/a6000.nand state/a6000.nand.pristine
```

A failed boot can erase the emulated NAND, so keep the untouched copy.

## Build and run

Stage the QEMU tree with `./setup-qemu.sh` (it uses the same `A6400_QEMU_DIR` and pinned QEMU as `emulators/qemu-a6400/setup-qemu.sh`, see `emulators/qemu-a6400/README.md`), then build it with the command it prints. The staged tree also builds every `emulators/qemu-a6400` machine. Re-stage it with this script, not with `emulators/qemu-a6400/setup-qemu.sh` alone, which refuses a tree that carries the ILCE-6000 patch. The Linux build also needs `gcc-arm-none-eabi`, which builds `provision/a6kcopy`, and `libslirp-dev` for Wi-Fi. Commands run from `emulators/a6000/`:

```sh
./run.sh                              # QEMU window with the camera panel
python3 tools/camui.py --launch       # camera-body web UI at http://localhost:8642
```

`run.sh` uses `$A6400_QEMU_DIR/build/qemu-system-arm`. The default directory is the one `setup-qemu.sh` uses, and `A6K_QEMU=<binary>` overrides it. The header of `run.sh` lists the other variables: `A6K_NAND`, `A6K_SD`, `A6K_SHELL`, `A6K_FW`, `A6K_ICOUNT` and others.

Machine options go after `-M`, for example `./run.sh -M rtc-lost=on`:

| Option | Meaning |
|---|---|
| `mode-dial=N` | mode-dial position (default 3 = P) |
| `rtc-lost=on` | clock backup lost (first-boot setup) |
| `lens=none\|ekit` | empty mount (default) or a kit-lens model |
| `hdmi=on`, `hdmi-edid=FILE` | virtual 1080p TV, also hot-pluggable with `qom-set /machine hdmi on\|off` |
| `usb-socket=HOST:PORT` | USB cable over TCP (see `tools/pmca-socket/`) |
| `nfc=CHARDEV` | NFC virtual phone (`tap`, `read`, `cmd <hex>`, `field on\|off`, `status`) |

For Wi-Fi, add `-netdev user,id=w0 -global ar6003-sdio.netdev=w0 -global 'ar6003-sdio.aps=ssid=TestAP,psk=testpass123'`.

The NFC tag answers carry the camera's Wi-Fi Direct SSID and password from `Backup.bin`. Keep NFC logs local.

## Camera controls

| Host key | Camera control |
|---|---|
| ↑ ← ↓ →, Enter | control wheel DISP / Drive / ± / ISO, centre |
| `]` / `[` | control wheel turn (Shift = x4) |
| Delete, `A`, `F`, `C` | C2/Delete, AEL, Fn, C1 |
| PgUp / PgDn | mode dial |
| `M` / `P` | MENU / Playback |
| `H` | flash pop-up |
| `R` | MOVIE |
| `O` | ON/OFF switch |
| `S` / Shift+S or Space | shutter half / full press |

HMP `sendkey ret 300` also works. Hold keys for about 300 ms. Send one key at a time, and wait a few seconds before taking a screendump.

## Tools

| Path | Purpose |
|---|---|
| `run.sh`, `provision.sh`, `provision/` | Boot, factory-style NAND programming, stage-1 loader |
| `tools/camui.py`, `tools/camui.html` | Camera-body web UI with live LCD (A6000 key map; `emulators/qemu-a6400/tools/rear_panel.py` is the A6400 panel) |
| `tools/mkdata.py`, `tools/wbi.py` | Warm-boot-image data from the updater |
| `tools/a6kshell.py` | Root shell on UART2 next to the stock UI (`A6K_SHELL=<port>`), with file push and pull |
| `tools/mon.py`, `tools/klog.py` | HMP commands; kernel log from the Sony klog ring |
| `tools/mksd.py` | Blank SDHC image |
| `tools/pmca-socket/` | Sony-PMCA-RE USB backend for the emulator's cable (MIT) |

## Credits

- [Oein/a5000-emulator](https://github.com/Oein/a5000-emulator) (GPL-2.0, Park SungHyun): the ILCE-5000 emulator that the machine, its device models and these scripts come from.
- [voxivoid/a6000-qemu](https://github.com/voxivoid/a6000-qemu): the ILCE-6000 port that this directory is taken from.
- [QEMU](https://gitlab.com/qemu-project/qemu): the emulator the machine is built on.
- [ma1co/fwtool.py](https://github.com/ma1co/fwtool.py): unpacks Sony's updater.
- [ma1co/Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE): PlayMemories Camera Apps tooling. `tools/pmca-socket/` is a backend for it, under MIT like Sony-PMCA-RE (Copyright (c) 2015 ma1co, `tools/pmca-socket/LICENSE`).
- [ma1co/OpenMemories-Tweak](https://github.com/ma1co/OpenMemories-Tweak): root shell on a real camera, used to make the NAND dump.

## Licence and notice

The code here is GPL-2.0-or-later, like `emulators/qemu-a6400/` and QEMU. The exception is `tools/pmca-socket/`, which is MIT.

This project is not affiliated with Sony. Do not add Sony firmware, data derived from it or camera dumps to this repository or its issues. The emulator never writes to a real camera.
