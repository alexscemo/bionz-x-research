# Custom OS DWC3/USB plan status

Tracks `usb_custom_os_qemu_plan.md` (external plan document) against repository evidence. One
line per phase gate, with what is actually verified and what remains. The evidence record is
authoritative: [E0716](../evidence/0716-custom-os-usb-dwc3-phase1-diagnosis-and-composite-model.md).

## Phase 1 - diagnosis and DWC3 audit (Gate 1)

State: **done for the offline half**, with an explicit root-cause boundary.

- Host side of the silence: `a6400_usb_debugd` only opens `/dev/usb/usb_extcmd` and registers
  feature `0x23` / opcode `0x7a` (`tools/native_camera/a6400_usb_debugd.c`). It needs an
  enumerated Sony gadget, not a DWC3 register.
- Kernel side is present in the Custom OS under QEMU: `dwc3.0` binds, `/sys/class/udc/dwc3.0`
  exists, `GSNPSID = 0x5533290a`, `0xF0200024 = 1`, FUSB `0xF2920010` VBUS/CID bits, all
  `/dev/usb/*` nodes, and `usb_extcmd`, `usbg_sen`, `usbg_buspower`, `usb_buspwr`,
  `usb_darwin`, `usb_portMonitor`, `devno` loaded (`usbg_storage`/`usbg_stillimage` are not).
- `DCTL`, `DEVTEN`, `GEVNTSIZ`, `DALEPENA` stay 0 and the UDC stays `not attached`; IRQ 229
  never fires. No enumeration exists.- Keeping `libUsbControl.so` + `libHotplug.so` (`custom_os.usbsvc=1`) runs their constructors
  (`UsbControl_Init()=0`, `Hotplug_Init()=387`) and still starts nothing.
- A decoded standalone `otgcore` sequence initialises the UDC but stops at
  `usb_otgcore: mode error` and `save_desc_tbl()` (`EFAULT`), i.e. no registered function driver.
- Endpoint budget: 16 physical endpoints (EP0 OUT/IN + seven pairs), from the captured
  `GHWPARAMS3 = 0x06A4D085`.

Open: host the Sony USB service FSM, or implement the userspace gadget registration the stock
class libraries use (`CUsbGadgetStill::on_create` -> `ioctl 0x403800e0` with event callbacks,
E0279; video probe-info registration behind `0x4038e000`, E0319). Loading `usbg_storage.ko`
in the guest does not register a function.

## Phase 2 - one functional interface in the Custom OS (Gate 2)

State: **done (2026-11-23)**. One vendor interface enumerates and answers a real EP0 handshake.

- `custom_os/usb/gadget_desc_abi.h` mirrors the gadgetcore descriptor ABI; `abi_check.c` proves
  every size/offset against the custom kernel header with `BUILD_BUG_ON`.
- `custom_os/launcher/usb_gift_start.c` calls `USB_IOC_GADGETCORE_START` (0x400ce203) with a
  054C:0CA8, one-configuration, one-interface vendor table.
- `custom_os/usb/a6400_vendor_fn.c` registers a function driver for interface 0 and answers
  vendor PING (0xC1/0x01, 4 bytes) with `PONG` via `usb_ep_queue` on EP0.
- Result: GET_DESCRIPTOR(device/config), SET_ADDRESS, SET_CONFIGURATION(1),
  GET_CONFIGURATION=1 all complete; UDC `configured`/high-speed; `DCTL=0x8cf00a00`; PING returns
  `50 4f 4e 47`. Automated by `tests/test_qemu_custom_os_usb_gate2.py` (passes live in WSL).

Not claimed: the stock `a6400_usb_debugd` daemon (needs the Sony `usb_extcmd` transport), bulk or
ISOC data paths.

## Phase 3 - composite traffic and parallel operation (Gate 3)

Guest side: **done for debugger + data + UVC (bulk)**. Four interfaces / six endpoints
enumerate; UVC VideoControl carries class-specific descriptors and VideoStreaming has alt 0
(formats) and alt 1 (bulk IN 0x86); `GET_CUR(VS_PROBE)` returns the 26-byte probe;
`SET_CUR(PROBE)` and `SET_CUR(COMMIT)` are accepted, and COMMIT queues the retained 720p JPEG
(29,452 B) which the host reassembles byte-exactly (SOI/EOI and fixture SHA-256) with debugger
traffic interleaved around it; SET_CONFIGURATION(0) + soft-disconnect stops every driver and
leaves the host NAKed. Automated by `tests/test_qemu_custom_os_usb_composite.py` (passes live in
WSL).
Remaining for full Gate 3 parity: ISO VideoStreaming and a camera-derived multi-frame payload.
The UDC sysfs string cannot reach "not attached" on a pull-up drop in this kernel (only at UDC
registration), recorded as a limitation.

QEMU side: **done for the model contract.** The opt-in `strict-composite` policy models
14 parallel endpoints, multi-TRB sequencing, bounded LINK following, Interrupt and ISO
transfers, per-endpoint stall, and `DCTL` soft-connect. `qemu_dwc3_composite_check.py` passes
116/116 checks, including a single interleaved pass over all 14 non-control endpoints, two
LINK hops, ISO InProgress/Complete, STALL then recovery, and disconnect/reconnect with a
working transfer afterwards. Default, `strict-ep0` and `strict-bulk` behaviour is unchanged.

Not modelled yet: real UVC payload producers, multi-packet bursts beyond one TRB per token,
and a guest-side composite function set.

## Phase 4 — final offline gate and physical-test package (Gate 4)

State: **blocked on user action**.

- Everything under this workstream is green: Phase A 8/8 (run-19; also run-15), Phase B OK
  (run-16), Gate 2 and the debugger+data+UVC tests pass live, qtest 116/116, MMIO identity
  44/44, knowledge map `build`/`check` OK, ASCII-only files.
- Known flake: the Phase A UI item fails on some boots because the guest warm-reboots through
  its exception-monitor path and the OSD buffer is empty at screenshot time (runs 16-18 failed,
  runs 15 and 19 passed, same initrd). Receipts for both outcomes are retained.
- The full suite reports **2 failures from untracked local state**, not from this work:
  - `emulator.test_run_b26_uvc_prearm` expects `apps/camera-daemon/build/a6400-lived` to be the
    E0476 pin (632,812 B, `1eda8418...`); the local untracked build is 856,320 B,
    `c92403f6...`.
  - `test_mod_manager` finds an untracked `Sony-PMCA-RE` checkout in the workspace.
  Closing Gate 4 needs a user decision: restore the pinned daemon binary (or re-pin it with a
  new evidence record) and decide whether the PMCA checkout stays (update the test's
  environment assumption) or is removed. This workstream will not weaken either assertion.
- The physical checklist (`custom-os-usb-physical-test-checklist.md`) is written but not
  executed; executing it needs an explicit GO.
- No physical-camera action has been taken.
