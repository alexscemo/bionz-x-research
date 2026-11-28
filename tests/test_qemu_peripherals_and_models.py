"""QEMU CXD90045 touchscreen, peripherals, and multi-model compatibility tests.

Verifies:
  1. Host-independent contract parity: all 8 modeled TargetProfile bodies in
     DEFAULT_REGISTRY map to dedicated QEMU machine types, rear_panel.py touch
     QMP event translation, and run.sh peripheral flags.
  2. Native QEMU multi-body hardware instantiation across all 8 modeled bodies
     (sony-a6400, sony-a6400a, sony-zve10, sony-a6100, sony-a6600, sony-a7c,
     sony-a7m3, sony-a7rm4a): EVF-less P0.2 on ZV-E10, 6-axis IMU on SIO ch4,
     Dual-SD mmc2 on a7M3/a7RM4A, and FF vs APS-C kit lens defaults.
  3. Virtual Touchscreen (FocalTech 0x20 on i2c0 + P5.9 GPIO + VPH ABS/BTN):
     i2c.ko start/config/TX/RX, unregistered slave NACK, ESD heartbeat 0x91,
     QMP pointer click, sub-20ms fast tap, and drag/swipe coordinate tracking.
  4. Broadcom BCM4339 Wi-Fi (SDIO mmc1 @ 0xF0301000) & Bluetooth (H4 HCI uart3):
     CMD5/CMD3/CMD7/CMD52/CMD53, CIS 0x02D0:0x4339, ChipCommon 0x10044339,
     AI EROM, TCM firmware upload/readback, Fn2 SDPCM/BCDC MAC query, and
     Bluetooth H4 HCI + Broadcom .hcd minidriver/RAM/Launch_RAM handshake.
  5. HDMI TX (0xF2A40000) + CEC (0xF3C10000) + VPH Console 1 scanout:
     HPD status/W1C, 256-byte CEA-861/VSDB EDID DDC read & checksums, TMDS ->
     VPH head 1 active composition, and CEC virtual TV responder.
  6. AudioIp (0xF3420000..0xF3428FFF): capture/playback DMA ring-buffer pointer
     advancement, 440 Hz stereo sine PCM capture into DRAM, and mic-jack sense.
  7. Physical E-Mount Lens Motors (UART1 @ 0xF2001000 + PL080 @ 0xF0102000 +
     P1.4 GPIO): BL 0xF0..0x55 wire framing, STARTUP_INFO, focus/iris/zoom/OSS
     motor slewing and settling, PrimeCell ID suppression on UART1, and
     work-dma ch6 coexistence.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

from project_paths import PROJECT_ROOT
from src.a6400.targets.catalog import DEFAULT_REGISTRY
from tests.test_qemu_emmc import NATIVE_AVAILABLE, QEMU


EXPECTED_BODY_MAP = {
    "ilce_6400":  ("sony-a6400",  True,  False, False, False),
    "ilce_6400a": ("sony-a6400a", True,  False, False, False),
    "zv_e10":     ("sony-zve10",  False, True,  False, False),
    "ilce_6100":  ("sony-a6100",  True,  False, False, False),
    "ilce_6600":  ("sony-a6600",  True,   True, False, False),
    "ilce_7c":    ("sony-a7c",    True,   True, False,  True),
    "ilce_7m3":   ("sony-a7m3",   True,   True,  True,  True),
    "ilce_7rm4a": ("sony-a7rm4a", True,   True,  True,  True),
}


def _load_rear_panel_module():
    tools_dir = PROJECT_ROOT / "qemu-a6400/tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    spec = importlib.util.spec_from_file_location(
        "qemu_rear_panel", tools_dir / "rear_panel.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class HostContractParityTests(unittest.TestCase):
    """Host-independent checks covering all 8 modeled bodies and rear_panel touch."""

    def test_all_registered_targets_have_qemu_machine_definitions(self):
        registered_ids = set(DEFAULT_REGISTRY.ids())
        self.assertEqual(registered_ids, set(EXPECTED_BODY_MAP.keys()))

        c_src = (PROJECT_ROOT / "qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c").read_text(
            encoding="utf-8"
        )
        for tid, (mname, _, _, _, _) in EXPECTED_BODY_MAP.items():
            self.assertIn(f'"{tid}"', c_src)
            self.assertIn(f'"{mname}"', c_src)

    def test_rear_panel_send_touch_event_formats_vph_qmp_commands(self):
        rp = _load_rear_panel_module()
        calls = []

        class FakeQmp:
            def command(self, name, **kwargs):
                calls.append((name, kwargs))
                return {}

        qmp = FakeQmp()
        rp.send_touch_event(qmp, "down", 0.25, 0.75)
        rp.send_touch_event(qmp, "move", 0.50, 0.50)
        rp.send_touch_event(qmp, "up", 1.20, -0.10)  # clamped to [0, 32767]

        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][0], "input-send-event")
        self.assertEqual(calls[0][1]["device"], "vph")
        self.assertEqual(calls[0][1]["head"], 0)
        self.assertEqual(
            calls[0][1]["events"],
            [
                {"type": "abs", "data": {"axis": "x", "value": round(0.25 * 32767)}},
                {"type": "abs", "data": {"axis": "y", "value": round(0.75 * 32767)}},
                {"type": "btn", "data": {"button": "left", "down": True}},
            ],
        )
        self.assertEqual(len(calls[1][1]["events"]), 2)
        self.assertEqual(
            calls[2][1]["events"],
            [
                {"type": "abs", "data": {"axis": "x", "value": 32767}},
                {"type": "abs", "data": {"axis": "y", "value": 0}},
                {"type": "btn", "data": {"button": "left", "down": False}},
            ],
        )
        with self.assertRaises(ValueError):
            rp.send_touch_event(qmp, "pinch", 0.5, 0.5)

    def test_touch_int_repeat_property_and_asserted_only_timer(self):
        src = (PROJECT_ROOT / "qemu-a6400/qemu-overlay/hw/misc/cxd90045_touch.c").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'DEFINE_PROP_UINT32("int-repeat-ms", Cxd90045TouchState, int_repeat_ms, 10)',
            src,
        )
        self.assertIn("device_class_set_props(dc, touch_props);", src)

        def body(header):
            self.assertIn(header, src)
            return src.split(header, 1)[1].split("\n}", 1)[0]

        arm = body("static void touch_int_repeat_arm")
        self.assertIn("!touch_line_asserted(s)", arm)
        self.assertIn("timer_mod", arm)
        sync = body("static void touch_input_sync")
        self.assertIn("touch_update_int_line(s, true)", sync)
        self.assertIn("touch_int_repeat_arm(s)", sync)
        prepare = body("static void touch_prepare_burst")
        self.assertIn("touch_line_asserted(s)", prepare)
        self.assertIn("touch_int_repeat_stop(s)", prepare)
        reset = body("static void touch_reset")
        self.assertIn("touch_int_repeat_stop(s)", reset)


class _QTestQmpHarness(unittest.TestCase):
    """Helper starting QEMU with both qtest stdio and a unix QMP socket."""

    def start_harness(self, machine_spec: str, extra_args: list[str] | None = None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        qmp_sock = root / "qmp.sock"
        cmd = [
            str(QEMU),
            "-M", machine_spec,
            "-accel", "qtest",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-net", "none",
            "-qmp", f"unix:{qmp_sock},server=on,wait=off",
            "-qtest", "stdio",
        ]
        if extra_args:
            cmd.extend(extra_args)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.addCleanup(self._cleanup_proc, proc)
        pending = [b""]
        rp = _load_rear_panel_module()
        qmp = rp.Qmp(qmp_sock, proc)
        qmp.request = lambda name, args=None: qmp.command(name, **(args or {}))
        self.addCleanup(qmp.close)
        return proc, pending, qmp, root

    @staticmethod
    def _cleanup_proc(proc: subprocess.Popen):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe:
                pipe.close()

    def qt(self, proc: subprocess.Popen, pending: list[bytes], cmd: str) -> str:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write((cmd + "\n").encode("ascii"))
        deadline = time.monotonic() + 5.0
        while b"\n" not in pending[0]:
            rem = deadline - time.monotonic()
            if rem <= 0 or not select.select([proc.stdout], [], [], rem)[0]:
                raise RuntimeError(f"qtest timeout on: {cmd}")
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError(f"qtest EOF on: {cmd}")
            pending[0] += chunk
        line, pending[0] = pending[0].split(b"\n", 1)
        text = line.decode("ascii", errors="replace")
        if not text.startswith("OK"):
            raise RuntimeError(f"qtest failed ({cmd}): {text}")
        return text

    def rd32(self, proc, pending, addr: int) -> int:
        return int(self.qt(proc, pending, f"readl {addr:#x}").split()[1], 16)

    def rd16(self, proc, pending, addr: int) -> int:
        return int(self.qt(proc, pending, f"readw {addr:#x}").split()[1], 16)

    def rd8(self, proc, pending, addr: int) -> int:
        return int(self.qt(proc, pending, f"readb {addr:#x}").split()[1], 16)

    def wr32(self, proc, pending, addr: int, val: int) -> None:
        self.qt(proc, pending, f"writel {addr:#x} {val:#x}")

    def wr16(self, proc, pending, addr: int, val: int) -> None:
        self.qt(proc, pending, f"writew {addr:#x} {val:#x}")

    def wr8(self, proc, pending, addr: int, val: int) -> None:
        self.qt(proc, pending, f"writeb {addr:#x} {val:#x}")

    def clock_step_ns(self, proc, pending, ns: int) -> None:
        self.qt(proc, pending, f"clock_step {ns}")


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class QemuMultiModelCompatibilityTests(_QTestQmpHarness):
    """Verify all 8 modeled camera models boot and configure body-specific hardware."""

    def test_all_eight_camera_models_and_body_traits(self):
        for profile in DEFAULT_REGISTRY.list():
            tid = profile.target_id
            mname, has_evf, has_imu, has_dual_sd, is_ff = EXPECTED_BODY_MAP[tid]
            with self.subTest(model=tid, machine=mname):
                proc, pending, qmp, _ = self.start_harness(f"{mname},peripherals=on")
                got_model = qmp.command(
                    "qom-get", path="/machine", property="camera-model"
                )
                self.assertEqual(got_model, tid)

                got_lens = qmp.command(
                    "qom-get", path="/machine/cxd90045-emount-lens", property="lens-model"
                )
                self.assertEqual(got_lens, "fe50f18" if is_ff else "selp1650")

                # GPIO Port 0 input data (+0x10): P0.2 is high on EVF-less ZV-E10
                p0_in = self.rd32(proc, pending, 0xF2910010)
                if not has_evf:
                    self.assertNotEqual(p0_in & (1 << 2), 0)

                # SIO ch4 (0xF2405800): 6-axis IMU answers WHO_AM_I (0x0F|0x80 -> 0x69)
                sio4 = 0xF2405800
                self.wr32(proc, pending, sio4 + 0x10, 0x01000000)
                self.wr32(proc, pending, sio4 + 0x0C, (3 << 24))  # 4 bytes
                self.wr32(proc, pending, sio4 + 0x100, 0x0000008F)
                self.wr32(proc, pending, sio4 + 0x00, 0x20000000)
                rx_word = self.rd32(proc, pending, sio4 + 0x100)
                self.assertEqual((rx_word >> 8) & 0xFF, 0x69)

                # Dual-SD bodies (a7M3, a7RM4A) expose mmc2 @ 0xF0302000
                mmc2_cap = self.rd32(proc, pending, 0xF0302040)
                if has_dual_sd:
                    self.assertEqual(mmc2_cap, 0x016830B0)
                else:
                    self.assertEqual(mmc2_cap, 0)


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class QemuTouchscreenTests(_QTestQmpHarness):
    """Verify I2C0 completion + FocalTech 0x20 touch IC + P5.9 + QMP pointer."""

    I2C0 = 0xF2401000
    GPIO_P5_IN = 0xF2910510

    def _i2c_init(self, proc, pending):
        self.wr16(proc, pending, self.I2C0 + 0x1C, 1)
        self.wr16(proc, pending, self.I2C0 + 0x2C, 1)
        self.wr16(proc, pending, self.I2C0 + 0x10, 0x20)
        self.wr16(proc, pending, self.I2C0 + 0x14, 0x20)
        self.assertEqual(self.rd16(proc, pending, self.I2C0 + 0x38), 3)

    def _i2c_write_bytes(self, proc, pending, addr7: int, data: bytes) -> int:
        self.wr16(proc, pending, self.I2C0 + 0x24, 1)  # I2C_MSG
        payload = bytes([(addr7 << 1) | 0]) + data
        for b in payload:
            self.wr16(proc, pending, self.I2C0 + 0x0C, b)
        self.wr16(proc, pending, self.I2C0 + 0x08, len(payload) - 1)
        self.wr16(proc, pending, self.I2C0 + 0x04, 0x87)  # START|TX|STOP
        st = self.rd16(proc, pending, self.I2C0 + 0x00)
        self.assertEqual(self.rd16(proc, pending, self.I2C0 + 0x28) & 1, 1)
        self.wr16(proc, pending, self.I2C0 + 0x28, 1)  # W1C IRQ
        self.assertEqual(self.rd16(proc, pending, self.I2C0 + 0x28) & 1, 0)
        return st

    def _i2c_read_regs(self, proc, pending, addr7: int, reg: int, length: int) -> bytes:
        st = self._i2c_write_bytes(proc, pending, addr7, bytes([reg]))
        self.assertEqual(st & 0x20, 0)
        out = bytearray()
        while len(out) < length:
            chunk = min(16, length - len(out))
            # Phase 1: address + RD bit
            self.wr16(proc, pending, self.I2C0 + 0x24, 1)
            self.wr16(proc, pending, self.I2C0 + 0x0C, (addr7 << 1) | 1)
            self.wr16(proc, pending, self.I2C0 + 0x08, 0)
            self.wr16(proc, pending, self.I2C0 + 0x04, 0x85)
            self.wr16(proc, pending, self.I2C0 + 0x28, 1)
            # Phase 2: RX data bytes
            self.wr16(proc, pending, self.I2C0 + 0x24, 1)
            self.wr16(proc, pending, self.I2C0 + 0x08, chunk - 1)
            self.wr16(proc, pending, self.I2C0 + 0x04, 0x8A)
            len_reg = self.rd16(proc, pending, self.I2C0 + 0x08)
            self.assertEqual(((len_reg >> 4) & 0xF) + 1, chunk)
            for _ in range(chunk):
                out.append(self.rd16(proc, pending, self.I2C0 + 0x0C) & 0xFF)
            self.wr16(proc, pending, self.I2C0 + 0x28, 1)
        return bytes(out)

    @staticmethod
    def _unpack_avcam_point(pt: bytes) -> tuple[int, int, int, int]:
        ev = (pt[0] >> 6) & 0x3
        tid = (pt[2] >> 4) & 0xF
        x_byte = ((((pt[0] & 0xF) << 8) | pt[1]) >> 2) & 0xFF
        y_byte = ((((pt[2] & 0xF) << 8) | pt[3]) >> 2) & 0xFF
        return ev, tid, x_byte, y_byte

    def test_touch_click_drag_fast_tap_heartbeat_and_nack(self):
        rp = _load_rear_panel_module()
        proc, pending, qmp, _ = self.start_harness("sony-a6400,touchscreen=on")
        self._i2c_init(proc, pending)

        # Unregistered address 0x55 NACKs cleanly with bit 5 (0x20)
        st_nack = self._i2c_write_bytes(proc, pending, 0x55, b"\x00")
        self.assertNotEqual(st_nack & 0x20, 0)
        self.wr16(proc, pending, self.I2C0 + 0x00, 0x20)
        self.assertEqual(self.rd16(proc, pending, self.I2C0 + 0x00) & 0x20, 0)

        # Idle P5.9 is high (bit 9 = 1)
        self.assertNotEqual(self.rd32(proc, pending, self.GPIO_P5_IN) & (1 << 9), 0)

        # ESD heartbeat 0x91 increments on every read
        hb = [self._i2c_read_regs(proc, pending, 0x20, 0x91, 1)[0] for _ in range(3)]
        self.assertEqual(len(set(hb)), 3)

        # Touch down -> drag move -> touch up via rear_panel.send_touch_event
        rp.send_touch_event(qmp, "down", 100 / 255.0, 50 / 255.0)
        self.assertEqual(self.rd32(proc, pending, self.GPIO_P5_IN) & (1 << 9), 0)

        b1 = self._i2c_read_regs(proc, pending, 0x20, 0x02, 13)
        self.assertEqual(b1[0], 1)  # TD_STATUS = 1 finger
        self.assertEqual(self._unpack_avcam_point(b1[1:7]), (0, 0, 100, 50))  # Put Down

        rp.send_touch_event(qmp, "move", 200 / 255.0, 150 / 255.0)
        b2 = self._i2c_read_regs(proc, pending, 0x20, 0x02, 13)
        self.assertEqual(b2[0], 1)
        self.assertEqual(self._unpack_avcam_point(b2[1:7]), (2, 0, 200, 150))  # Contact

        rp.send_touch_event(qmp, "up", 200 / 255.0, 150 / 255.0)
        b3 = self._i2c_read_regs(proc, pending, 0x20, 0x02, 13)
        self.assertEqual(b3[0], 0)
        self.assertEqual(self._unpack_avcam_point(b3[1:7]), (1, 0, 200, 150))  # Lift Up
        # P5.9 returns idle high once Lift Up has been consumed
        self.assertNotEqual(self.rd32(proc, pending, self.GPIO_P5_IN) & (1 << 9), 0)

        # Fast sub-20ms tap (down + up before any I2C poll) still delivers Put Down then Lift Up
        rp.send_touch_event(qmp, "down", 64 / 255.0, 128 / 255.0)
        rp.send_touch_event(qmp, "up", 64 / 255.0, 128 / 255.0)
        fast1 = self._i2c_read_regs(proc, pending, 0x20, 0x02, 13)
        fast2 = self._i2c_read_regs(proc, pending, 0x20, 0x02, 13)
        self.assertEqual(self._unpack_avcam_point(fast1[1:7]), (0, 0, 64, 128))
        self.assertEqual(self._unpack_avcam_point(fast2[1:7]), (1, 0, 64, 128))

    def test_i2c_prewrite_busypoll_and_status_reads_without_complete_transfers(self):
        proc, pending, _, _ = self.start_harness("sony-a6400")
        # i2c.ko wait_reg_not_busy polls bit 15 == 0 before writing +0x10/+0x14/+0x20.
        for off, val in ((0x10, 0x8020), (0x14, 0x8020), (0x20, 0x8001)):
            self.wr16(proc, pending, self.I2C0 + off, val)
            self.assertEqual(self.rd16(proc, pending, self.I2C0 + off), val & ~0x8000)
        # i2c.ko reads I2C_STATUS (+0x00) and tests bit 5 even when complete_transfers=off.
        self.wr16(proc, pending, self.I2C0 + 0x00, 0x0023)
        self.assertEqual(self.rd16(proc, pending, self.I2C0 + 0x00), 0x0003)


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class QemuBcm4339WifiBluetoothTests(_QTestQmpHarness):
    """Verify BCM4339 SDIO Wi-Fi at mmc1 and Bluetooth H4 HCI + HCD on uart3."""

    MMC1 = 0xF0301000
    UART3 = 0xF2003000

    def _sdio_cmd(self, proc, pending, idx: int, arg: int, xfer: int = 0) -> int:
        self.wr16(proc, pending, self.MMC1 + 0x30, 0xFFFF)
        self.wr32(proc, pending, self.MMC1 + 0x08, arg)
        self.wr16(proc, pending, self.MMC1 + 0x0C, xfer)
        self.wr16(proc, pending, self.MMC1 + 0x0E, (idx << 8) | 0x02)
        return self.rd32(proc, pending, self.MMC1 + 0x10)

    def _cmd52(self, proc, pending, wr: bool, fn: int, addr: int, val: int = 0) -> int:
        arg = ((1 if wr else 0) << 31) | (fn << 28) | ((addr & 0x1FFFF) << 9) | (val & 0xFF)
        return self._sdio_cmd(proc, pending, 52, arg) & 0xFF

    def test_bcm4339_sdio_wifi_and_bt_hci_contracts(self):
        proc, pending, qmp, _ = self.start_harness("sony-a6400,bcm4339=on")

        # 1. SDIO enumeration (CMD5 -> R4, CMD3 -> RCA, CMD7 -> SELECT)
        self.wr16(proc, pending, self.MMC1 + 0x34, 0x01FF)
        r4 = self._sdio_cmd(proc, pending, 5, 0x00FF8000)
        self.assertEqual(r4, 0xA1FFFF00)
        r6 = self._sdio_cmd(proc, pending, 3, 0)
        self.assertEqual(r6 >> 16, 1)
        self._sdio_cmd(proc, pending, 7, 1 << 16)

        # 2. Read Common CIS MANFID (0x02D0:0x4339) via CMD52
        cis = bytes([self._cmd52(proc, pending, False, 0, 0x1002 + i) for i in range(4)])
        vid, did = struct.unpack("<HH", cis)
        self.assertEqual((vid, did), (0x02D0, 0x4339))

        # Enable F1 & F2 and verify IORx ready + ALP/HT clock CSR
        self._cmd52(proc, pending, True, 0, 0x02, 0x06)
        self.assertEqual(self._cmd52(proc, pending, False, 0, 0x03), 0x06)
        self._cmd52(proc, pending, True, 0, 0x04, 0x07)  # IENx master+F1+F2
        self._cmd52(proc, pending, True, 1, 0x1000E, 0x08)
        self.assertEqual(self._cmd52(proc, pending, False, 1, 0x1000E) & 0xC0, 0xC0)

        # 3. CMD53 PIO read of ChipCommon @ 0x18000000 -> 0x10044339
        arg53_cc = (1 << 28) | (1 << 26) | (0x0000 << 9) | 4
        self._sdio_cmd(proc, pending, 53, arg53_cc, xfer=0x0010)
        chipid = self.rd32(proc, pending, self.MMC1 + 0x20)
        self.assertEqual(chipid, 0x10044339)

        # 4. CMD53 SDMA firmware upload to CR4 TCM @ 0x00001000 and readback
        self._cmd52(proc, pending, True, 1, 0x1000A, 0x00)
        self._cmd52(proc, pending, True, 1, 0x1000B, 0x00)
        self._cmd52(proc, pending, True, 1, 0x1000C, 0x00)
        fw_src = 0x00500000
        fw_dst = 0x00501000
        self.wr32(proc, pending, fw_src, 0xCAFE4339)
        self.wr32(proc, pending, fw_src + 4, 0x12345678)
        self.wr32(proc, pending, self.MMC1 + 0x00, fw_src)
        self._sdio_cmd(proc, pending, 53, (1 << 31) | (1 << 28) | (1 << 26) | (0x1000 << 9) | 8, xfer=0x0001)
        self.wr32(proc, pending, self.MMC1 + 0x00, fw_dst)
        self._sdio_cmd(proc, pending, 53, (0 << 31) | (1 << 28) | (1 << 26) | (0x1000 << 9) | 8, xfer=0x0011)
        self.assertEqual(self.rd32(proc, pending, fw_dst), 0xCAFE4339)
        self.assertEqual(self.rd32(proc, pending, fw_dst + 4), 0x12345678)

        # 5. Function 2 SDPCM + BCDC WLC_GET_VAR("cur_etheraddr")
        var_name = b"cur_etheraddr\x00"
        total_len = 12 + 16 + len(var_name)
        sdpcm = struct.pack("<HHBBBBBBBB", total_len, (~total_len) & 0xFFFF, 0, 0, 0, 12, 0, 0, 0, 0)
        cdc = struct.pack("<IIII", 262, 6, 0x00010000, 0) + var_name
        pkt = sdpcm + cdc
        while len(pkt) % 4:
            pkt += b"\x00"
        for i in range(0, len(pkt), 4):
            self.wr32(proc, pending, fw_src + i, struct.unpack_from("<I", pkt, i)[0])
        self.wr32(proc, pending, self.MMC1 + 0x00, fw_src)
        self._sdio_cmd(proc, pending, 53, (1 << 31) | (2 << 28) | (1 << 26) | len(pkt), xfer=0x0001)
        self.assertNotEqual(self.rd16(proc, pending, self.MMC1 + 0x30) & 0x0100, 0)  # CARD_INT

        self.wr32(proc, pending, self.MMC1 + 0x00, fw_dst)
        self._sdio_cmd(proc, pending, 53, (0 << 31) | (2 << 28) | (1 << 26) | 64, xfer=0x0011)
        rx_bytes = b"".join(struct.pack("<I", self.rd32(proc, pending, fw_dst + i)) for i in range(0, 40, 4))
        self.assertEqual(rx_bytes[28:34], bytes([0x02, 0x43, 0x39, 0x64, 0x00, 0x01]))

        # 5b. SDPCM Data Channel (chan=2) TX Ethernet frame -> netdev
        eth_frame = (
            b"\xff\xff\xff\xff\xff\xff"
            + bytes([0x02, 0x43, 0x39, 0x64, 0x00, 0x01])
            + b"\x08\x00"
            + b"QEMU-WIFI-TX-TEST"
        )
        data_total = 12 + 4 + len(eth_frame)
        sdpcm_tx = struct.pack("<HHBBBBBBBB", data_total, (~data_total) & 0xFFFF, 1, 2, 0, 12, 0, 0, 0, 0)
        bcdc_hdr = b"\x20\x00\x00\x00"
        tx_pkt = sdpcm_tx + bcdc_hdr + eth_frame
        while len(tx_pkt) % 4:
            tx_pkt += b"\x00"
        for i in range(0, len(tx_pkt), 4):
            self.wr32(proc, pending, fw_src + i, struct.unpack_from("<I", tx_pkt, i)[0])
        self.wr32(proc, pending, self.MMC1 + 0x00, fw_src)
        self._sdio_cmd(proc, pending, 53, (1 << 31) | (2 << 28) | (1 << 26) | len(tx_pkt), xfer=0x0001)
        tx_pkts = qmp.command("qom-get", path="/machine/cxd90045-bcm4339-sdio", property="tx-packets")
        self.assertEqual(tx_pkts, 1)

        # 6. Bluetooth H4 HCI + Broadcom .hcd download over PL011 UART3 (0xF2003000)
        self.wr32(proc, pending, self.UART3 + 0x2C, 0x70)   # LCR_H: 8N1 + FIFOs
        self.wr32(proc, pending, self.UART3 + 0x30, 0x0301) # CR: UARTEN|TXE|RXE

        def hci_xfer(cmd_bytes: bytes) -> bytes:
            for b in cmd_bytes:
                self.wr32(proc, pending, self.UART3 + 0x00, b)
            self.clock_step_ns(proc, pending, 50_000)
            evt = bytearray()
            while (self.rd32(proc, pending, self.UART3 + 0x18) & 0x10) == 0:
                evt.append(self.rd32(proc, pending, self.UART3 + 0x00) & 0xFF)
            return bytes(evt)

        ev_rst = hci_xfer(b"\x01\x03\x0C\x00")
        self.assertEqual(ev_rst, b"\x04\x0E\x04\x01\x03\x0C\x00")
        ev_ver = hci_xfer(b"\x01\x01\x10\x00")
        self.assertEqual(ev_ver[:7], b"\x04\x0E\x0C\x01\x01\x10\x00")
        # Manufacturer = Broadcom (0x000F), LMP subversion = 0x4103
        self.assertEqual(struct.unpack_from("<HH", ev_ver, 11), (0x000F, 0x4103))

        # HCD minidriver -> Write_RAM -> Launch_RAM
        self.assertEqual(hci_xfer(b"\x01\x2E\xFC\x00")[6], 0)
        self.assertEqual(hci_xfer(b"\x01\x4C\xFC\x08\x00\x80\x08\x00\xDE\xAD\xBE\xEF")[6], 0)
        self.assertEqual(hci_xfer(b"\x01\x4E\xFC\x04\x00\x80\x08\x00")[6], 0)
        self.assertTrue(qmp.command("qom-get", path="/chardevs/bcm4339-bt", property="fw-launched"))

    def test_bcm4339_netdev_rx_to_sdio_cmd53(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        py_port = sock.getsockname()[1]
        qemu_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        qemu_sock.bind(("127.0.0.1", 0))
        qemu_port = qemu_sock.getsockname()[1]
        qemu_sock.close()
        try:
            proc, pending, qmp, _ = self.start_harness(
                "sony-a6400,bcm4339=on",
                extra_args=[
                    "-netdev", f"dgram,id=w0,local.type=inet,local.host=127.0.0.1,local.port={qemu_port},"
                               f"remote.type=inet,remote.host=127.0.0.1,remote.port={py_port}",
                    "-global", "cxd90045-bcm4339-sdio.netdev=w0",
                ],
            )
            self.wr16(proc, pending, self.MMC1 + 0x34, 0x01FF)
            self._sdio_cmd(proc, pending, 5, 0x00FF8000)
            self._sdio_cmd(proc, pending, 3, 0)
            self._sdio_cmd(proc, pending, 7, 1 << 16)
            self._cmd52(proc, pending, True, 0, 0x02, 0x06)
            self._cmd52(proc, pending, True, 0, 0x04, 0x07)

            eth_rx = (
                bytes([0x02, 0x43, 0x39, 0x64, 0x00, 0x01])
                + bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x02])
                + b"\x08\x00"
                + b"QEMU-WIFI-RX-TEST"
            )
            sock.sendto(eth_rx, ("127.0.0.1", qemu_port))
            deadline = time.monotonic() + 2.0
            rx_pkts = 0
            while time.monotonic() < deadline:
                rx_pkts = qmp.command("qom-get", path="/machine/cxd90045-bcm4339-sdio", property="rx-packets")
                if rx_pkts >= 1:
                    break
                self.clock_step_ns(proc, pending, 50_000)
            self.assertEqual(rx_pkts, 1)
            self.assertNotEqual(self.rd16(proc, pending, self.MMC1 + 0x30) & 0x0100, 0)

            fw_dst = 0x02003000
            self.wr32(proc, pending, self.MMC1 + 0x00, fw_dst)
            self._sdio_cmd(proc, pending, 53, (0 << 31) | (2 << 28) | (1 << 26) | 64, xfer=0x0011)
            rx_buf = b"".join(struct.pack("<I", self.rd32(proc, pending, fw_dst + i)) for i in range(0, 16 + len(eth_rx) + 4, 4))
            self.assertEqual(rx_buf[5] & 0x0F, 2)  # SDPCM_DATA_CHANNEL
            self.assertEqual(rx_buf[16:16 + len(eth_rx)], eth_rx)
        finally:
            sock.close()


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class QemuHdmiCecAndAudioTests(_QTestQmpHarness):
    """Verify HDMI TX EDID/HPD/TMDS -> VPH head 1, CEC TV, and AudioIp DMA."""

    HDMI = 0xF2A40000
    CEC = 0xF3C10000
    VPH = 0xF3440000
    AUDIO = 0xF3420000

    def test_hdmi_edid_vph_console1_and_cec_virtual_tv(self):
        proc, pending, qmp, root = self.start_harness("sony-a6400,hdmi-cec=on")

        # HPD connected (reg 0x3004 bit 1 = 0x02), PHY PLL locked (reg 0x4004 bit 0)
        self.assertEqual(self.rd32(proc, pending, self.HDMI + 0x3004 * 4) & 2, 2)
        self.assertEqual(self.rd32(proc, pending, self.HDMI + 0x4004 * 4) & 1, 1)

        # Read full 256-byte EDID in 32-byte DDC bursts and verify checksums + VSDB
        edid = bytearray()
        for off in range(0, 256, 32):
            self.wr32(proc, pending, self.HDMI + 0x7E00 * 4, 0x50)
            self.wr32(proc, pending, self.HDMI + 0x7E01 * 4, off)
            self.wr32(proc, pending, self.HDMI + 0x7E02 * 4, 32)
            self.wr32(proc, pending, self.HDMI + 0x7E04 * 4, 0x08)
            self.assertEqual(self.rd32(proc, pending, self.HDMI + 0x0105 * 4) & 2, 2)
            self.wr32(proc, pending, self.HDMI + 0x0105 * 4, 2)  # W1C DDC ready
            for i in range(32):
                edid.append(self.rd32(proc, pending, self.HDMI + (0x7E20 + i) * 4) & 0xFF)
        self.assertEqual(edid[:8], b"\x00\xff\xff\xff\xff\xff\xff\x00")
        self.assertIn(b"SONY QEMU TV", edid[:128])
        self.assertIn(b"\x03\x0c\x00", edid[128:])  # HDMI Licensing OUI
        self.assertEqual(sum(edid[:128]) & 0xFF, 0)
        self.assertEqual(sum(edid[128:]) & 0xFF, 0)

        # Enable VPH HDMI line OSD (640x480 green RGBA4444 = 0x0F0F) + TMDS reg 0x4001
        osd_pa = 0x00600000
        for i in range(0, 640 * 4, 4):
            self.wr32(proc, pending, osd_pa + i, 0x0F0F0F0F)
        self.wr32(proc, pending, self.VPH + 0x454, (480 << 16) | 640)
        self.wr32(proc, pending, self.VPH + 0x410, 1 << 16)  # progressive
        self.wr32(proc, pending, self.VPH + 0x414, 0)        # RGBA4444
        self.wr32(proc, pending, self.VPH + 0x5B0, (480 << 16) | 640)
        self.wr32(proc, pending, self.VPH + 0x5B8, 640 * 2)
        self.wr32(proc, pending, self.VPH + 0xB80, osd_pa)
        self.wr32(proc, pending, self.VPH + 0xBC0, 1)        # arm ch55 line OSD

        ppm_idle = root / "hdmi_idle.ppm"
        qmp.command("screendump", filename=str(ppm_idle), device="vph", head=1)
        # Enable HDMI TMDS output -> drives VPH hdmi-tmds high
        self.wr32(proc, pending, self.HDMI + 0x4001 * 4, 0x0F)
        self.assertTrue(
            qmp.command("qom-get", path="/machine/cxd90045-hdmi-cec", property="tmds-active")
        )
        ppm_on = root / "hdmi_on.ppm"
        qmp.command("screendump", filename=str(ppm_on), device="vph", head=1)
        self.assertNotEqual(ppm_idle.read_bytes(), ppm_on.read_bytes())

        # CEC: self-poll Playback 1 (0x44) -> NACK (0x04); Give OSD Name (0x40, 0x46) -> "QEMU TV"
        self.wr32(proc, pending, self.CEC + 0x00 * 4, 0x44)
        self.wr32(proc, pending, self.CEC + 0x10 * 4, 1)
        self.wr32(proc, pending, self.CEC + 0x11 * 4, 1)
        self.assertEqual(self.rd32(proc, pending, self.CEC + 0x60 * 4) & 0x04, 0x04)
        self.wr32(proc, pending, self.CEC + 0x61 * 4, 0x07)

        self.wr32(proc, pending, self.CEC + 0x00 * 4, 0x40)
        self.wr32(proc, pending, self.CEC + 0x01 * 4, 0x46)
        self.wr32(proc, pending, self.CEC + 0x10 * 4, 2)
        self.wr32(proc, pending, self.CEC + 0x11 * 4, 1)
        st_cec = self.rd32(proc, pending, self.CEC + 0x60 * 4)
        self.assertEqual(st_cec & 0x03, 0x03)  # TX ACK + RX frame ready
        rx_len = self.rd32(proc, pending, self.CEC + 0x25 * 4)
        rx_frame = bytes(self.rd32(proc, pending, self.CEC + (0x15 + i) * 4) & 0xFF for i in range(rx_len))
        self.assertEqual(rx_frame, b"\x04\x47QEMU TV")

    def test_audioip_dma_ring_and_sine440_pcm_capture(self):
        proc, pending, qmp, _ = self.start_harness(
            "sony-a6400,audio-io=on,audio-input=sine440,audio-mic-plugged=on"
        )
        # Extended 0xF3428004 codec ready + external mic-jack sense bit 1
        self.assertEqual(self.rd32(proc, pending, self.AUDIO + 0x8004) & 0x03, 0x03)

        cap_ring = 0x00700000
        pb_ring = 0x00710000
        self.wr32(proc, pending, pb_ring, 0x4000C000)
        self.wr32(proc, pending, self.AUDIO + 0x3004, cap_ring)
        self.wr32(proc, pending, self.AUDIO + 0x3008, 3840)
        self.wr32(proc, pending, self.AUDIO + 0x3000, 1)
        self.wr32(proc, pending, self.AUDIO + 0x3104, pb_ring)
        self.wr32(proc, pending, self.AUDIO + 0x3108, 3840)
        self.wr32(proc, pending, self.AUDIO + 0x3100, 1)

        self.clock_step_ns(proc, pending, 15_000_000)  # 15 ms -> 1 DMA tick (1920 B)
        self.assertEqual(self.rd32(proc, pending, self.AUDIO + 0x300C), 1920)
        self.assertEqual(self.rd32(proc, pending, self.AUDIO + 0x310C), 1920)
        self.assertEqual(self.rd32(proc, pending, self.AUDIO + 0x3C00), 8192)
        samples = [
            struct.unpack("<hh", struct.pack("<I", self.rd32(proc, pending, cap_ring + i * 4)))
            for i in range(16)
        ]
        self.assertTrue(any(l != 0 and r != 0 for l, r in samples))


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class QemuEmountLensMotorTests(_QTestQmpHarness):
    """Verify Sony E-Mount BL protocol framing, PL080 M2P DMA, and motor physics."""

    UART1 = 0xF2001000
    PL080 = 0xF0102000
    GPIO_P1_IN = 0xF2910110

    @staticmethod
    def _build_bl_packet(opcode: int, payload: bytes = b"", flags: int = 1) -> bytes:
        total = len(payload) + 8
        body = struct.pack("<HBB", total, flags & 7, opcode) + payload
        csum = sum(body) & 0xFFFF
        return b"\xF0" + body + struct.pack("<HB", csum, 0x55)

    def _send_bl_via_pl080(self, proc, pending, opcode: int, payload: bytes = b"") -> tuple[int, bytes]:
        pkt = self._build_bl_packet(opcode, payload)
        ram_tx = 0x00580000
        padded = pkt + b"\x00" * ((4 - len(pkt) % 4) % 4)
        for i in range(0, len(padded), 4):
            self.wr32(proc, pending, ram_tx + i, struct.unpack_from("<I", padded, i)[0])

        self.wr32(proc, pending, self.PL080 + 0x030, 1)              # DMAC Enable
        self.wr32(proc, pending, self.PL080 + 0x100, ram_tx)         # Ch0 SrcAddr
        self.wr32(proc, pending, self.PL080 + 0x104, self.UART1)     # Ch0 DestAddr (UART1 DR)
        self.wr32(proc, pending, self.PL080 + 0x10C, (1 << 31) | len(pkt))
        self.wr32(proc, pending, self.PL080 + 0x110, (1 << 15) | (1 << 11) | 1)  # Flow=1 M2P + Enable
        self.assertEqual(self.rd32(proc, pending, self.PL080 + 0x004) & 1, 1)
        self.wr32(proc, pending, self.PL080 + 0x008, 1)              # Clear TC

        rx = bytearray()
        while (self.rd32(proc, pending, self.UART1 + 0x18) & 0x10) == 0:
            rx.append(self.rd32(proc, pending, self.UART1 + 0x00) & 0xFF)
        self.assertGreaterEqual(len(rx), 8)
        self.assertEqual(rx[0], 0xF0)
        self.assertEqual(rx[-1], 0x55)
        total = struct.unpack_from("<H", rx, 1)[0]
        self.assertEqual(total, len(rx))
        want_csum = sum(rx[1:-3]) & 0xFFFF
        got_csum = struct.unpack_from("<H", rx, len(rx) - 3)[0]
        self.assertEqual(got_csum, want_csum)
        return rx[4], bytes(rx[5:-3])

    def test_emount_lens_startup_motors_uart1_ids_and_work_dma_coexistence(self):
        proc, pending, qmp, _ = self.start_harness(
            "sony-a6400,emount-lens=selp1650,work-dma=on"
        )
        # 1. UART1 PrimeCell IDs at +0xFE0..+0xFFC MUST read 0 (keeps ttyAM1 = uart3)
        for off in range(0xFE0, 0x1000, 4):
            self.assertEqual(self.rd32(proc, pending, self.UART1 + off), 0)

        # 2. P1.4 lens detect is active-low (0 = attached)
        self.assertEqual(self.rd32(proc, pending, self.GPIO_P1_IN) & (1 << 4), 0)

        # 3. STARTUP_INFO (0x09) returns SELP1650 capabilities
        op, info = self._send_bl_via_pl080(proc, pending, 0x09)
        self.assertEqual(op, 0x09)
        self.assertIn(b"E PZ 16-50mm F3.5-5.6 OSS", info)
        self.assertEqual(struct.unpack_from("<HHH", info, 2), (160, 500, 350))

        # 4. Command focus (4000 steps @ 100 steps/ms), iris (F5.6 = 560), zoom (35.0 mm = 350)
        self._send_bl_via_pl080(proc, pending, 0x43, struct.pack("<ih", 4000, 100))
        self._send_bl_via_pl080(proc, pending, 0x20, struct.pack("<H", 560))
        self._send_bl_via_pl080(proc, pending, 0x28, struct.pack("<Hh", 350, 10))

        # Mid-travel @ 15 ms: focus motor is slewing (~1500 steps) and busy bit 1 is set
        self.clock_step_ns(proc, pending, 15_000_000)
        _, cyc_mid = self._send_bl_via_pl080(proc, pending, 0x41)
        mid_focus = struct.unpack_from("<i", cyc_mid, 4)[0]
        self.assertGreater(mid_focus, 500)
        self.assertLess(mid_focus, 3500)
        self.assertNotEqual(cyc_mid[0] & 0x02, 0)

        # After 40 ms more: focus, iris, and zoom all settle at exact targets
        self.clock_step_ns(proc, pending, 40_000_000)
        _, cyc_done = self._send_bl_via_pl080(proc, pending, 0x41)
        self.assertEqual(struct.unpack_from("<i", cyc_done, 4)[0], 4000)
        self.assertEqual(struct.unpack_from("<HH", cyc_done, 12), (350, 560))
        self.assertEqual(cyc_done[0] & 0x0E, 0)  # all motors settled

        # 5. Coexistence with E0641 work-dma channel 6 M2M copy
        self.wr32(proc, pending, 0x00620000, 0xA6400DEA)
        self.wr32(proc, pending, self.PL080 + 0x1C0, 0x00620000)
        self.wr32(proc, pending, self.PL080 + 0x1C4, 0x00621000)
        self.wr32(proc, pending, self.PL080 + 0x1CC, 0x1C491001)
        self.wr32(proc, pending, self.PL080 + 0x1D0, 1)
        self.assertEqual(self.rd32(proc, pending, 0x00621000), 0xA6400DEA)


@unittest.skipUnless(os.name == "posix", "POSIX shell required for run.sh preflight tests")
class SubmicroSyntheticPreflightTests(unittest.TestCase):
    """Verify run.sh preflight rules for SUBMICRO_SYNTHETIC=1."""

    def test_submicro_synthetic_rejects_conflicting_fixture_vars(self):
        run_sh = PROJECT_ROOT / "qemu-a6400/run.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "not-staged"
            dummy = root / "dummy.bin"
            dummy.write_bytes(bytes(384))
            for extra_env in (
                {"SUBMICRO_PAGE": str(dummy)},
                {"SUBMICRO_FRAMES": str(dummy)},
            ):
                with self.subTest(extra_env=extra_env):
                    res = subprocess.run(
                        ["bash", str(run_sh)],
                        env={
                            **os.environ,
                            "RUN_DIR": str(run_dir),
                            "A6400_QEMU_DATA_DIR": "",
                            "SUBMICRO_SYNTHETIC": "1",
                            **extra_env,
                        },
                        capture_output=True,
                        timeout=5,
                    )
                    self.assertNotEqual(res.returncode, 0)
                    self.assertIn(b"SUBMICRO_SYNTHETIC=1 cannot be combined", res.stderr)
                    self.assertFalse(run_dir.exists())


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class QemuSubmicroSyntheticAndHdmiHotplugTests(_QTestQmpHarness):
    """Verify opt-in seed-free sub-micro synthesis and machine hdmi-plugged hotplug."""

    SIO3 = 0xF2405600
    HDMI = 0xF2A40000

    def _sio3_xfer(self, proc, pending, tx: bytes) -> bytes:
        assert len(tx) in (128, 256)
        self.wr32(proc, pending, self.SIO3 + 0x00, 0xC0000000)
        self.wr32(proc, pending, self.SIO3 + 0x0C, ((len(tx) - 1) & 0xFF) << 24)
        for idx in range(0, len(tx), 4):
            word = struct.unpack_from("<I", tx, idx)[0]
            self.wr32(proc, pending, self.SIO3 + 0x100 + idx, word)
        self.wr32(proc, pending, self.SIO3 + 0x00, 0x20000000)
        return b"".join(
            struct.pack("<I", self.rd32(proc, pending, self.SIO3 + 0x100 + idx))
            for idx in range(0, len(tx), 4)
        )

    def test_baseline_sio3_stays_silent_without_submicro_opt_in(self):
        proc, pending, _, _ = self.start_harness("sony-a6400")
        tx = b"\x31" + b"\xA5" * 127
        rx = self._sio3_xfer(proc, pending, tx)
        self.assertEqual(rx, tx)

    def test_submicro_synthetic_slots_lens_rail_sd_detect_and_front_keys(self):
        from src.a6400.execution.submicro_link import (
            decode_slot_b_frame,
            encode_slot_b_frame,
            verify_frame_crc,
        )
        from tools.research.qemu_media import sd_blockdev, sd_event
        from tools.research.qemu_boot_probe import digest

        rp = _load_rear_panel_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            card = root / "sd.img"
            card.write_bytes(bytes(16 * 1024 * 1024))
            import json
            node = sd_blockdev(card, digest(card), root / "inputs", root / "run")
            proc, pending, qmp, _ = self.start_harness(
                "sony-a6400,submicro-synthetic=on,submicro-lens-power=on,rear-controls=on",
                ["-drive", "if=none,id=a6400-sd", "-blockdev", json.dumps(node)],
            )

            # 1. Slot A (0x21 -> 0x20): valid CRC, POWER ON (byte 28 = 0x01), SD0 empty
            rx_a = self._sio3_xfer(proc, pending, b"\x21" + bytes(127))
            self.assertEqual(rx_a[0], 0x20)
            self.assertTrue(verify_frame_crc(rx_a))
            self.assertEqual(rx_a[2] & 0x10, 0x00)
            self.assertEqual(rx_a[28], 0x01)
            self.assertEqual(rx_a[29], 0xFF)

            # Insert SD -> Slot A byte 2 bit 4 reflects card insertion
            sd_event(qmp, "insert")
            rx_a_ins = self._sio3_xfer(proc, pending, b"\x21" + bytes(127))
            self.assertTrue(verify_frame_crc(rx_a_ins))
            self.assertEqual(rx_a_ins[2] & 0x10, 0x10)

            # Toggle power lever -> byte 28 bit 0 clears, then restores
            rp.press_control(qmp, "power-lever")
            rx_a_off = self._sio3_xfer(proc, pending, b"\x21" + bytes(127))
            self.assertTrue(verify_frame_crc(rx_a_off))
            self.assertEqual(rx_a_off[28] & 0x01, 0x00)
            rp.press_control(qmp, "power-lever")
            self.assertEqual(self._sio3_xfer(proc, pending, b"\x21" + bytes(127))[28] & 0x01, 0x01)

            # 2. Slot C (0x61 -> 0x60): valid CRC
            rx_c = self._sio3_xfer(proc, pending, b"\x61" + bytes(127))
            self.assertEqual(rx_c[0], 0x60)
            self.assertTrue(verify_frame_crc(rx_c))

            # 3. Slot B (0x31 -> 0x30): valid 8-TLV powered status + lens rail tracking
            tx_b_off = encode_slot_b_frame(0x31, [(0x0A, b"\x00\x00" + bytes(6))])
            rx_b_off = self._sio3_xfer(proc, pending, tx_b_off)
            dec_off = decode_slot_b_frame(rx_b_off)
            self.assertTrue(dec_off.crc_valid)
            self.assertEqual(dec_off.frame_type, 0x30)
            tlvs_off = dec_off.by_id()
            self.assertEqual(set(tlvs_off.keys()), {0x00, 0x01, 0x02, 0x03, 0x0A, 0x0B, 0x10, 0x14})
            self.assertEqual(tlvs_off[0x00], bytes.fromhex("01020138"))
            self.assertEqual(tlvs_off[0x03], bytes.fromhex("00000002"))  # rail off bit 0 = 0

            tx_b_on = encode_slot_b_frame(0x31, [(0x0A, b"\x00\x01" + bytes(6))])
            rx_b_on = self._sio3_xfer(proc, pending, tx_b_on)
            dec_on = decode_slot_b_frame(rx_b_on)
            self.assertTrue(dec_on.crc_valid)
            self.assertEqual(dec_on.by_id()[0x03], bytes.fromhex("00000003"))  # rail on bit 0 = 1

            # 4. Combined 256-byte Slot A + Slot B exchange
            rx_ab = self._sio3_xfer(proc, pending, (b"\x21" + bytes(127)) + tx_b_off)
            self.assertEqual(rx_ab[0], 0x20)
            self.assertTrue(verify_frame_crc(rx_ab[:128]))
            self.assertEqual(rx_ab[128], 0x30)
            self.assertTrue(verify_frame_crc(rx_ab[128:]))
            self.assertEqual(decode_slot_b_frame(rx_ab[128:]).by_id()[0x03], bytes.fromhex("00000002"))

    def test_machine_hdmi_plugged_startup_and_runtime_hotplug(self):
        proc, pending, qmp, _ = self.start_harness(
            "sony-a6400,hdmi-cec=on,hdmi-plugged=off"
        )
        self.assertFalse(qmp.command("qom-get", path="/machine", property="hdmi-plugged"))
        self.assertEqual(self.rd32(proc, pending, self.HDMI + 0x3004 * 4) & 0xC2, 0)

        # Arm TMDS while unplugged -> tmds-active stays false
        self.wr32(proc, pending, self.HDMI + 0x4001 * 4, 0x0F)
        self.assertFalse(
            qmp.command("qom-get", path="/machine/cxd90045-hdmi-cec", property="tmds-active")
        )

        # Hotplug HDMI sink via machine property -> HPD + RSEN + transition event + TMDS active
        qmp.command("qom-set", path="/machine", property="hdmi-plugged", value=True)
        self.assertTrue(qmp.command("qom-get", path="/machine", property="hdmi-plugged"))
        self.assertEqual(self.rd32(proc, pending, self.HDMI + 0x3004 * 4) & 0xC2, 0xC2)
        self.assertEqual(self.rd32(proc, pending, self.HDMI + 0x3005 * 4) & 0xC2, 0xC2)
        self.assertTrue(
            qmp.command("qom-get", path="/machine/cxd90045-hdmi-cec", property="tmds-active")
        )

    def test_synthetic_submicro_slot_a_tracks_hdmi_hotplug_without_fixture(self):
        from src.a6400.execution.submicro_link import verify_frame_crc

        proc, pending, qmp, _ = self.start_harness(
            "sony-a6400,hdmi-cec=on,hdmi-plugged=off,submicro-synthetic=on"
        )
        tx = b"\x21" + bytes(127)

        unplugged = self._sio3_xfer(proc, pending, tx)
        self.assertEqual(unplugged[0], 0x20)
        self.assertTrue(verify_frame_crc(unplugged))
        self.assertEqual(unplugged[29] & 0x40, 0)

        qmp.command("qom-set", path="/machine", property="hdmi-plugged", value=True)
        plugged = self._sio3_xfer(proc, pending, tx)
        self.assertEqual(plugged[0], 0x20)
        self.assertTrue(verify_frame_crc(plugged))
        self.assertEqual(plugged[29] & 0x40, 0x40)

        qmp.command("qom-set", path="/machine", property="hdmi-plugged", value=False)
        unplugged_again = self._sio3_xfer(proc, pending, tx)
        self.assertTrue(verify_frame_crc(unplugged_again))
        self.assertEqual(unplugged_again[29] & 0x40, 0)


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class QemuRemovableSdUhsU3Tests(_QTestQmpHarness):
    """Verify opt-in UHS-I U3 / Class 10 / V30 ACMD13 SD_STATUS and SCR vs baseline."""

    MMC0 = 0xF0300000

    def _sdhci_enable(self, proc, pending):
        self.wr8(proc, pending, self.MMC0 + 0x29, 0x0F)
        self.wr16(proc, pending, self.MMC0 + 0x2C, 0x0007)
        self.wr32(proc, pending, self.MMC0 + 0x34, 0xFFFFFFFF)

    def _sd_cmd(self, proc, pending, idx: int, arg: int, flags: int,
                blk_size: int = 0) -> int:
        self.wr32(proc, pending, self.MMC0 + 0x30, 0xFFFFFFFF)
        self.wr32(proc, pending, self.MMC0 + 0x04, ((1 << 16) | blk_size) if blk_size else 0)
        self.wr32(proc, pending, self.MMC0 + 0x08, arg)
        xfer = 0x0012 if blk_size else 0x0000
        self.wr32(proc, pending, self.MMC0 + 0x0C, (((idx << 8) | flags) << 16) | xfer)
        for _ in range(30):
            st = self.rd32(proc, pending, self.MMC0 + 0x30)
            self.assertEqual(st & 0x8000, 0, f"MMC0 CMD{idx} failed: {st:#x}")
            if st & 1:
                return self.rd32(proc, pending, self.MMC0 + 0x10)
            self.clock_step_ns(proc, pending, 100_000)
        raise TimeoutError(f"MMC0 CMD{idx} timed out")

    def _init_and_select_sd(self, proc, pending) -> int:
        self._sd_cmd(proc, pending, 0, 0, 0)
        self.assertEqual(self._sd_cmd(proc, pending, 8, 0x1AA, 0x1A), 0x1AA)
        for _ in range(10):
            self._sd_cmd(proc, pending, 55, 0, 0x1A)
            ocr = self._sd_cmd(proc, pending, 41, 0x40FF8000, 0x02)
            if ocr & 0x80000000:
                break
            self.clock_step_ns(proc, pending, 1_000_000)
        else:
            raise TimeoutError("ACMD41 powerup timeout")
        self._sd_cmd(proc, pending, 2, 0, 0x09)
        rca = self._sd_cmd(proc, pending, 3, 0, 0x1A) & 0xFFFF0000
        self._sd_cmd(proc, pending, 7, rca, 0x1B)
        return rca

    def _read_acmd_data(self, proc, pending, rca: int, acmd: int, size: int) -> bytes:
        self._sd_cmd(proc, pending, 55, rca, 0x1A)
        self._sd_cmd(proc, pending, acmd, 0, 0x3A, blk_size=size)
        return b"".join(
            struct.pack("<I", self.rd32(proc, pending, self.MMC0 + 0x20))
            for _ in range(size // 4)
        )

    def test_sd_status_baseline_class0_vs_opt_in_uhs_u3(self):
        import json
        from tools.research.qemu_media import SD_NODE, sd_event

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Sparse 4 GiB SDHC image (0 actual data blocks written, instant truncate)
            card = root / "sdhc.img"
            with card.open("wb") as f:
                f.truncate(4 * 1024 * 1024 * 1024)
            node = {
                "driver": "raw",
                "node-name": SD_NODE,
                "file": {"driver": "file", "filename": str(card)},
            }

            for uhs_on in (False, True):
                with self.subTest(sd_uhs_u3=uhs_on):
                    mspec = "sony-a6400,sd-uhs-u3=on" if uhs_on else "sony-a6400"
                    proc, pending, qmp, _ = self.start_harness(
                        mspec,
                        ["-drive", "if=none,id=a6400-sd", "-blockdev", json.dumps(node)],
                    )
                    self._sdhci_enable(proc, pending)
                    sd_event(qmp, "insert")
                    self.clock_step_ns(proc, pending, 1_000_000)
                    rca = self._init_and_select_sd(proc, pending)

                    scr = self._read_acmd_data(proc, pending, rca, 51, 8)
                    ssr_1bit = self._read_acmd_data(proc, pending, rca, 13, 64)

                    # Switch to 4-bit bus (ACMD6 arg=2) and re-read ACMD13
                    self._sd_cmd(proc, pending, 55, rca, 0x1A)
                    self._sd_cmd(proc, pending, 6, 2, 0x1A)
                    ssr_4bit = self._read_acmd_data(proc, pending, rca, 13, 64)
                    self.assertEqual(ssr_1bit[0] & 0xC0, 0x00)
                    self.assertEqual(ssr_4bit[0] & 0xC0, 0x80)
                    self.assertEqual(ssr_1bit[1:], ssr_4bit[1:])

                    if not uhs_on:
                        self.assertEqual(ssr_4bit[1:], bytes(63))
                        self.assertEqual((scr[1] >> 4) & 0x7, 3)  # SDHC Security v2.00
                        self.assertEqual(scr[2] & 0x04, 0)
                    else:
                        self.assertEqual(ssr_4bit[8], 0x04)   # Class 10
                        self.assertEqual(ssr_4bit[10], 0x90)  # 4 MiB AU_SIZE
                        self.assertEqual(ssr_4bit[13], 0x04)  # 1 s ERASE_TIMEOUT
                        self.assertEqual(ssr_4bit[14], 0x39)  # U3 + 4 MiB UHS_AU_SIZE
                        self.assertEqual(ssr_4bit[15], 0x1E)  # V30
                        self.assertEqual((scr[1] >> 4) & 0x7, 3)  # SDHC Security v2.00
                        self.assertEqual(scr[2] & 0x80, 0x80)     # SD_SPEC3

                    # Eject + re-insert preserves UHS U3 status cleanly
                    sd_event(qmp, "eject")
                    sd_event(qmp, "insert")
                    self._sdhci_enable(proc, pending)
                    self.clock_step_ns(proc, pending, 1_000_000)
                    rca2 = self._init_and_select_sd(proc, pending)
                    ssr_reins = self._read_acmd_data(proc, pending, rca2, 13, 64)
                    self.assertEqual(ssr_reins[8], 0x04 if uhs_on else 0x00)
                    self.assertEqual(ssr_reins[14], 0x39 if uhs_on else 0x00)


if __name__ == "__main__":
    unittest.main()
