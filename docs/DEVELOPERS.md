# Developer guide

About 90 % of development runs offline, in Python and C, on two complementary emulators.
Camera dumps and private artifacts stay local and are excluded from Git.

## Quick start

Python 3.11+ (tested up to 3.15.2; toolchain details in [TOOLCHAIN.md](TOOLCHAIN.md)).

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -q
```

Run first-party tools as modules from the repository root: `python -m tools.research.<tool>`.
Before working on a subject, query the knowledge map:
`python -m tools.research.knowledge_map query <address|symbol|E-ID|term>` ([map](map/README.md)).

## The two emulators

- **QEMU ([`emulators/qemu-a6400/`](../emulators/qemu-a6400/README.md)): full virtual camera.**
  Boots all 4 CPU cores, eMMC partitions, 40 Linux kernel modules, `av-cam.bin` and the real camera
  menu (`im.elf`) across the 8 modeled `CXD90045` bodies (`sony-a6400`, `sony-a6400a`, `sony-zve10`,
  `sony-a6100`, `sony-a6600`, `sony-a7c`, `sony-a7m3`, `sony-a7rm4a`). It has a live browser rear
  screen (`rear_panel.py`), a clickable virtual touchscreen, opt-in `BCM4339` Wi-Fi/Bluetooth,
  HDMI/CEC output, `AudioIp` DMA I/O, E-mount lens motors and mod hot-reload
  ([E0700](evidence/0700-qemu-touchscreen-peripherals-and-multimodel.md)). Mod workflow:
  [MOD_DEVELOPMENT.md](../emulators/qemu-a6400/MOD_DEVELOPMENT.md).
- **Unicorn ([`src/a6400/`](../src/a6400/README.md)): fast ARM engine.** Runs isolated kernel
  (`L2`) and app (`L3`) slices in Python to check mods, memory safety and OS contracts in CI without
  booting a full machine.

Other machines: [`emulators/`](../emulators/README.md) (`ILCE-6700` research machine, `ILCE-6000`).

## What does not work in the emulator yet

- **Real sensor and SD photo/video capture.** QEMU renders a synthetic test pattern and models
  E-mount focus/iris/zoom/OSS actuator positions without optical defocus or saved RAW/AVCHD/JPEG
  files ([QEMU status](../emulators/qemu-a6400/STATUS.md)).
- **Live USB webcam (UVC) stream.** EP0 control enumeration works; the USB video streaming
  endpoints are unfinished ([CONTRIBUTING_UVC_PTP.md](CONTRIBUTING_UVC_PTP.md)).
- **Over-the-air RF and BLE.** `BCM4339` emulates SDIO enumeration, Backplane/TCM upload, `BCDC`
  control `ioctl`s and Bluetooth H4 HCI with `.hcd` loading, not live 802.11 WPA2/TCP traffic or
  BLE peers ([E0700](evidence/0700-qemu-touchscreen-peripherals-and-multimodel.md)).

## Rules that keep results honest

- **Honest claims only.** Host-written UI or camera logic is never presented as firmware compatibility.
- **No internal flash writes.** Never flash custom firmware or modify boot partitions.
- **Clean-room emulation.** Original firmware owns camera logic; unimplemented hardware stops
  cleanly instead of faking success.
- **Safe hardware inspection.** Read-only probes are allowed; bulk `/dev/mem` sweeps that can reboot
  the camera are banned. Every `/dev/mem` access goes through `tools/devmem_policy.py`
  ([AGENTS.MD](../AGENTS.MD)).

## Where to go next

| Topic | Document |
| :--- | :--- |
| Current technical state | [CURRENT_STATE.md](CURRENT_STATE.md) |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Adding a camera body | [ADDING_A_BIONZX_CAMERA.md](ADDING_A_BIONZX_CAMERA.md) |
| Porting a mod to another body | [porting/PORT_NOTES_TEMPLATE.md](porting/PORT_NOTES_TEMPLATE.md) |
| Status labels for mods and kits | [STATUS_LABELS.md](STATUS_LABELS.md) |
| QEMU/TCG tracing workflow | [TCG_TRACE_WORKFLOW.md](TCG_TRACE_WORKFLOW.md) |
| Ghidra/REA workflow | [REA_WORKFLOW.md](REA_WORKFLOW.md) |
| USB debugger | [NATIVE_USB_DEBUGGER.md](NATIVE_USB_DEBUGGER.md) |
| Repository layout | [REPOSITORY.md](REPOSITORY.md) |
| Evidence record format | [evidence/README.md](evidence/README.md) |
