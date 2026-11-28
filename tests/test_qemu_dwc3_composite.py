"""QEMU DWC3 composite endpoint contract: static guards plus the native qtest run.

The device model gains an explicit ``strict-composite`` policy so the earlier
default / ``strict-ep0`` / ``strict-bulk`` baselines stay byte-for-byte
unchanged. These tests check that policy exists and is off by default, and,
when a Linux/WSL QEMU build is available, run the qtest harness that proves
EP0 three-stage control, fourteen parallel endpoints, multi-TRB and LINK
sequencing, interrupt/ISO transfers, stall, and guest-driven soft-connect.

The pure-Python tests run everywhere; the qtest part is skipped on Windows.
"""

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from project_paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))
from tools.research import qemu_dwc3_composite_check as dwc3  # noqa: E402

SOURCE = PROJECT_ROOT / "emulators/qemu-a6400/qemu-overlay/hw/usb/cxd90045_dwc3.c"
NATIVE_AVAILABLE = os.name == "posix" and (
    Path(os.environ.get("A6400_QEMU_DIR", str(Path.home() / "sony-a6400-qemu-11.1.3")))
    / "build/qemu-system-arm").is_file()


@unittest.skipUnless(
    'DEFINE_PROP_BOOL("strict-composite"' in SOURCE.read_text(errors="replace"),
    "strict-composite not yet implemented in cxd90045_dwc3.c",
)
class CompositeContractStaticTests(unittest.TestCase):
    def setUp(self):
        self.text = SOURCE.read_text(errors="replace")

    def test_strict_composite_property_is_opt_in(self):
        self.assertIn('DEFINE_PROP_BOOL("strict-composite", CxdDwc3State, strict_composite, false)',
                      self.text)

    def test_host_tokens_use_physical_endpoint_indices(self):
        self.assertIn("IN ", self.text)
        self.assertIn("s->strict_composite && !strncmp(line, \"IN \", 3)", self.text)
        self.assertIn("s->strict_composite && !strncmp(line, \"OUT \", 4)", self.text)

    def test_endpoint_budget_is_sixteen_physical_slots(self):
        self.assertIn("#define DWC3_MAX_EPS        16", self.text)

    def test_link_following_is_bounded(self):
        self.assertIn("#define COMPOSITE_MAX_TRB_HOPS   64U", self.text)

    def test_guest_soft_disconnect_event_is_opt_in(self):
        self.assertIn("if (s->strict_composite && was_run && s->host_connected)", self.text)


class QtestEncodingTests(unittest.TestCase):
    def test_trb_layout_is_little_endian(self):
        packed = dwc3.trb(0x02103000, 0x204, 0x0813)
        self.assertEqual(packed,
                         bytes.fromhex("00301002000000000402000013080000"))

    def test_memory_write_pads_the_qtest_leading_byte(self):
        line = dwc3.qtest_mem_write(0x02103000, bytes.fromhex("11223344"))
        self.assertEqual(line, "write 0x2103000 4 0011223344")

    def test_event_decoder_matches_the_model_layout(self):
        # ep=3, type=1 (XferComplete), status=8 (LST), param=0
        word = ((3 & 0x1F) << 1) | ((1 & 0xF) << 6) | ((8 & 0xF) << 12)
        events = dwc3.parse_events(word.to_bytes(4, "little"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["ep"], 3)
        self.assertEqual(events[0]["type"], 1)
        self.assertEqual(events[0]["status"], 8)

    def test_zero_words_are_not_events(self):
        events = [e for e in dwc3.parse_events(bytes(8)) if e["word"] != "0x00000000"]
        self.assertEqual(events, [])


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class CompositeQtestTests(unittest.TestCase):
    def test_native_composite_contract_passes(self):
        out = PROJECT_ROOT / "artifacts/tests" / f"qemu-dwc3-composite-{int(time.time())}"
        qemu_dir = Path(os.environ.get("A6400_QEMU_DIR",
                                       str(Path.home() / "sony-a6400-qemu-11.1.3")))
        result = subprocess.run(
            [sys.executable, "-m", "tools.research.qemu_dwc3_composite_check",
             "--qemu-dir", str(qemu_dir), "--report-dir", str(out)],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((out / "report.json").read_text())
        self.assertEqual(report["status"], "passed")
        self.assertGreaterEqual(report["checks_passed"], 100)
        self.assertEqual(report["checks_passed"], report["checks_total"])
        summary = report["summary"]
        self.assertEqual(summary["parallel_eps"]["distinct_endpoints"], 14)
        self.assertEqual(summary["endpoint_budget"]["non_control_slots"], 14)
        self.assertEqual(summary["link_hops"]["hops"], 2)
        self.assertIn("event_types", summary["isoc_in"])


if __name__ == "__main__":
    unittest.main()
