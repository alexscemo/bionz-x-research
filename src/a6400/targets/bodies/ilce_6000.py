"""Sony ILCE-6000 (a6000) reference profile: CXD90014, not a BIONZ X CXD90045 body.

Registered in ``CXD90014_REGISTRY`` only, so CXD90045 runners that walk
``DEFAULT_REGISTRY`` never see it.
"""
from __future__ import annotations

from src.a6400.targets.bodies._common import ref
from src.a6400.targets.profile import SOC_CXD90014, STATUS_REFERENCE, TargetProfile

A6000_EMULATOR = "docs/evidence/external-a6000-emulator/README.md"

ILCE_6000 = TargetProfile(
    target_id="ilce_6000",
    model_name="Sony ILCE-6000 (a6000)",
    status=STATUS_REFERENCE,
    soc=SOC_CXD90014,
    soc_verified=True,
    firmware_version="3.21",
    kernel_version="3.0.27_nl-rt106",
    sources={
        "firmware_version": ref(A6000_EMULATOR, "public updater Update_ILCE6000V321.exe, booted in sony-a6000"),
        "kernel_version": ref(A6000_EMULATOR, "uname -r in the guest shell of an emulated 3.21 boot"),
    },
    boundaries=(
        "Different SoC: CXD90014 (quad Cortex-A5). No CXD90045 address, module, USB or "
        "boot-layout claim applies to this body.",
        "soc_verified rests on the updater's crypterName: CXD90014 and the mach-cxd90014 "
        "paths and cxd90014_* symbols in its 3.21 vmlinux.bin, not on /proc/cpuinfo of a body.",
        "Emulator observations only (emulators/a6000/); the emulator boots only with the "
        "user's own NAND dump, which is never part of this repository.",
        "No kallsyms, uipc_queues or USB identity are claimed yet.",
    ),
    notes=(
        "Runs in the sony-a6000 QEMU machine, ported from voxivoid/a6000-qemu "
        "(a fork of Oein/a5000-emulator)."
    ),
)
