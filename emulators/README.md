# Emulators

Machines that boot captured Sony firmware on a PC. Firmware images and captures stay
local and are never committed.

| Folder | What it is |
| :--- | :--- |
| [`qemu-a6400/`](qemu-a6400/README.md) | QEMU `sony-a6400` machine family (`CXD90045`, 8 bodies): boot, browser rear panel, mod hot-reload ([status](qemu-a6400/STATUS.md)) |
| [`qemu-a6700/`](qemu-a6700/README.md) | First QEMU machine for the BIONZ XR `ILCE-6700` (research) |
| [`a6000/`](a6000/README.md) | `ILCE-6000` emulator run/provisioning scripts and web UI |

The fast Unicorn engine (kernel `L2` and user-space `L3` slices) is a Python package in
`src/a6400/execution/`, not here.
