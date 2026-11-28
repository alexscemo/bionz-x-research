"""QEMU sony-a6000 (ILCE-6000, CXD90014) machine: firmware-free checks.

The machine boots only with the user's own Sony updater and NAND dump
(emulators/a6000/README.md), so these tests do not boot it. They check that
the machine registers, that it builds its board with a placeholder kernel
image, and that the a6000 tooling keeps its inputs out of Git.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from project_paths import PROJECT_ROOT
from tests.test_qemu_emmc import NATIVE_AVAILABLE, QEMU

A6000_DIR = PROJECT_ROOT / "emulators/a6000"

# Device models that only the A6000 board instantiates.
A6000_ONLY_DEVICES = {
    "cxd90014-aif", "cxd90014-campig", "cxd90014-cec", "cxd90014-ddrc",
    "cxd90014-dmac", "cxd90014-hdmi", "cxd90014-idt", "cxd90014-jog",
    "cxd90014-ldec", "cxd90014-mdma", "cxd90014-nand", "cxd90014-opd",
    "cxd90014-sysctl", "cxd90014-usbhdc", "a6k-nfc", "a6k-fbview",
    "ar6003-sdio",
}
# Shared CXD90014 models the A6000 board reuses from the a6400 overlay.
SHARED_DEVICES = {
    "cxd90014-adc", "cxd90014-evsel", "cxd90014-gpio", "cxd90014-p2s",
    "cxd90014-regfile", "cxd90014-sa", "cxd90014-sio", "cxd90014-vph",
    "cxd4115-timer", "dmp-smaph-s30",
}


def _hmp(machine: str, commands: list[str], kernel: Path | None) -> subprocess.CompletedProcess:
    args = [str(QEMU), "-M", machine, "-display", "none", "-S",
            "-monitor", "stdio", "-serial", "null"]
    if kernel is not None:
        args += ["-kernel", str(kernel)]
    script = "".join(c + "\n" for c in commands + ["quit"])
    return subprocess.run(args, input=script, capture_output=True, text=True, timeout=60)


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class QemuA6000MachineTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Placeholder kernel: the machine is only constructed (-S), never run.
        self.kernel = Path(tmp.name) / "placeholder.bin"
        self.kernel.write_bytes(bytes(4096))

    def test_machine_is_registered(self) -> None:
        out = subprocess.run([str(QEMU), "-M", "help"], capture_output=True,
                             text=True, timeout=30, check=True).stdout
        self.assertRegex(out, r"(?m)^sony-a6000\s+Sony ILCE-6000 \(CXD90014")

    def test_requires_a_kernel(self) -> None:
        proc = _hmp("sony-a6000", [], kernel=None)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("-kernel vmlinux.bin is required", proc.stderr + proc.stdout)

    def test_board_instantiates_a6000_and_shared_devices(self) -> None:
        proc = _hmp("sony-a6000", ["info qtree"], self.kernel)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        types = {line.split()[1].rstrip(",")
                 for line in proc.stdout.splitlines() if line.strip().startswith("dev:")}
        self.assertLessEqual(A6000_ONLY_DEVICES | SHARED_DEVICES, types)

    def test_vph_scan_without_arm_is_a6000_only(self) -> None:
        a6000 = _hmp("sony-a6000",
                     ["qom-get /machine/peripheral/vph scan-without-arm"], self.kernel)
        self.assertIn("true", a6000.stdout)
        a6400 = subprocess.run(
            [str(QEMU), "-device", "cxd90014-vph,help"],
            capture_output=True, text=True, timeout=30).stdout
        self.assertRegex(a6400, r"scan-without-arm=<bool>.*\(default: off\)")


class A6000ToolingTests(unittest.TestCase):
    def test_run_sh_uses_instruction_count_time(self) -> None:
        text = (A6000_DIR / "run.sh").read_text(encoding="utf-8")
        self.assertIn('-icount "${A6K_ICOUNT:-shift=0,sleep=on}"', text)

    def test_local_inputs_are_ignored(self) -> None:
        ignore = (A6000_DIR / ".gitignore").read_text(encoding="utf-8").split()
        for entry in ("/firmware/", "/data/", "/state/", "/dumps/",
                      "/provision/a6kcopy", "/provision/*.cpio"):
            self.assertIn(entry, ignore)

    def test_scripts_carry_licence_and_provenance(self) -> None:
        for path in sorted(A6000_DIR.rglob("*")):
            if path.suffix not in {".sh", ".py", ".c"} or "pmca-socket" in path.parts:
                continue
            head = path.read_text(encoding="utf-8")[:600]
            with self.subTest(path=path.relative_to(PROJECT_ROOT).as_posix()):
                self.assertIn("SPDX-License-Identifier: GPL-2.0-or-later", head)
                self.assertIn("voxivoid/a6000-qemu", head)


if __name__ == "__main__":
    unittest.main()
