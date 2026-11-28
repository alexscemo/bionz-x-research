"""Sony ILCE-7C (a7C) reference target profile."""
from __future__ import annotations

from src.a6400.targets.bodies._common import (
    E0477,
    E0479,
    E0480,
    ref,
)
from src.a6400.targets.profile import (
    PID_TIER_OBSERVED,
    STATUS_REFERENCE,
    EvidenceRef,
    ModuleComponent,
    TargetProfile,
    UsbIdentity,
    UsbPid,
)

EXTERNAL_ILCE7C_USB_IDS = "docs/evidence/external-ilce7c-usb-ids/README.md"

ILCE_7C = TargetProfile(
    target_id="ilce_7c",
    model_name="Sony ILCE-7C",
    status=STATUS_REFERENCE,
    soc_verified=True,
    product_code="0032852231",
    firmware_version="2.03",
    kernel_version="3.0.27_nl-rt106+ SMP preempt mod_unload ARMv7",
    kallsyms={
        "usb_gadgetcore_ioctl": 0x602BCCF8,
        "usb_gadgetcore_register_driver": 0x602BCAAC,
        "usb_gcw_start": 0x602C1E4C,
        "usb_gcw_stop": 0x602C1E88,
        "setup_req": 0x602BD9D8,
        "make_config_desc": 0x602C0204,
    },
    uipc_queues={},
    usb=UsbIdentity(
        vid=0x054C,
        pids={
            "mass_storage": UsbPid(0x0D29, PID_TIER_OBSERVED),
            "mtp": UsbPid(0x0D2A, PID_TIER_OBSERVED),
            "pc_remote": UsbPid(0x0D2B, PID_TIER_OBSERVED),
            "usb_streaming": UsbPid(0x0DE6, PID_TIER_OBSERVED),
        },
        bcd_device=0x0200,
    ),
    modules={
        "usbg_video.ko": ModuleComponent(
            size=20668,
            sha256="d385a051bf01b0e5ea37412623ae85cfe3cda76ab0d11e61d111994ca9fbf4fb",
            role="UVC gadget video driver (byte-identical to ZV-E10)",
        ),
        "usbg_audio.ko": ModuleComponent(
            size=20456,
            sha256="8f73572784aab15da291c9f311a945d4ec56d2dd4162218164bc2aa18cc5fae0",
            role="UVC audio gadget driver (byte-identical to ZV-E10)",
        ),
        "usb_extcmd.ko": ModuleComponent(
            size=14808,
            sha256="db7383e9ad731cc23d5334dffdd4756e53dbb6aa9e32039dc503f2e2d8a246ef",
            role="Sony USB external-command kernel module (pulled from live ILCE-7C 2.00; metadata-equivalent to ILCE-6400)",
        ),
        "libusbcmd.so": ModuleComponent(
            size=15948,
            sha256="9fbf3339792e40caa73faa63d6b2dfa962ca4c47853320b49ff589595283ba71",
            role="Sony USB external-command user library (pulled from live ILCE-7C 2.00; byte-identical to ILCE-6400)",
        ),
    },
    sources={
        "product_code": ref(EXTERNAL_ILCE7C_USB_IDS, "external contributor pmca MSC output on FW 2.00 (Issue #60): model_code 0032852231"),
        "firmware_version": ref(E0477, "Update_ILCE7CV203.exe PackMan package (Sony CE Linux 8.2.7.6); live pull from FW 2.00 body"),
        "kernel_version": ref(E0477, "vermagic 3.0.27_nl-rt106+ SMP preempt mod_unload ARMv7"),
        "kallsyms.usb_gadgetcore_ioctl": ref(E0480, "decoded ILCE-7C 2.03 kallsyms: 0x602bccf8"),
        "kallsyms.usb_gadgetcore_register_driver": ref(E0480, "decoded ILCE-7C 2.03 kallsyms: 0x602bcaac"),
        "kallsyms.usb_gcw_start": ref(E0480, "decoded ILCE-7C 2.03 kallsyms: 0x602c1e4c"),
        "kallsyms.usb_gcw_stop": ref(E0480, "decoded ILCE-7C 2.03 kallsyms: 0x602c1e88"),
        "kallsyms.setup_req": ref(E0480, "decoded ILCE-7C 2.03 kallsyms: 0x602bd9d8"),
        "kallsyms.make_config_desc": ref(E0480, "decoded ILCE-7C 2.03 kallsyms: 0x602c0204"),
        "usb": (
            EvidenceRef(path=EXTERNAL_ILCE7C_USB_IDS, detail="host-observed Windows PnP (FW 2.00, Issue #60): Mass Storage 054C:0D29 (bcdDevice 0x0200)"),
            EvidenceRef(path=EXTERNAL_ILCE7C_USB_IDS, detail="host-observed Windows PnP (FW 2.00, Issue #60): MTP 054C:0D2A (bcdDevice 0x0200)"),
            EvidenceRef(path=EXTERNAL_ILCE7C_USB_IDS, detail="host-observed Windows PnP (FW 2.00, Issue #60): PC Remote 054C:0D2B (bcdDevice 0x0200)"),
            EvidenceRef(path=EXTERNAL_ILCE7C_USB_IDS, detail="host-observed Windows PnP (FW 2.00, Issue #60): USB Streaming 054C:0DE6 composite MI_00 UVC + MI_02 UAC"),
        ),
        "modules.usbg_video.ko": ref(E0479, "ILCE-7C 2.03 packaged UVC video module (byte-identical to ZV-E10)"),
        "modules.usbg_audio.ko": ref(E0479, "ILCE-7C 2.03 packaged UVC audio module (byte-identical to ZV-E10)"),
        "modules.usb_extcmd.ko": ref(E0477, "live Senser pull from ILCE-7C FW 2.00 (artifacts/hardware/ilce-7c-2.00-pull/usb_extcmd.ko)"),
        "modules.libusbcmd.so": ref(E0477, "live Senser pull from ILCE-7C FW 2.00 (artifacts/hardware/ilce-7c-2.00-pull/libusbcmd.so)"),
    },
    boundaries=(
        "Offline 2.03 package and live 2.00 Senser pull of usb_extcmd.ko/libusbcmd.so only; "
        "full /proc/kallsyms, /proc/osal/uipc, and live daemon execution on ILCE-7C are not yet exercised.",
        "usb_extcmd.ko (FW 2.00) is metadata-only equivalent to ILCE-6400; libusbcmd.so is byte-identical.",
        "product_code (0032852231) and observed USB PIDs (054C:0D29, 0D2A, 0D2B, 0DE6, bcdDevice 0x0200) "
        "were reported by an external contributor on a stock FW 2.00 body (Issue #60), not FW 2.03.",
    ),
    notes=(
        "Sony ILCE-7C (a7C) CXD90045 profile. Shares code object with ZV-E10 in 2.03 (E0477). "
        "Live FW 2.00 pull verified Senser serviceshell and usb_extcmd ABI equivalence."
    ),
)
