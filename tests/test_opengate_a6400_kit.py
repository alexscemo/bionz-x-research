"""Offline checks for the ILCE-6400 open-gate test kit (tools/kits/opengate_a6400.py).

No camera, USB or WSL: every flow runs against the kit's SimulatedCamera.
"""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.kits import opengate_a6400 as kit

ROOT = Path(__file__).resolve().parents[1]
CAPTURED_LIBMPR = ROOT / "artifacts" / "dumps" / "a6400-live-lib" / "libmpr.so"


class KitTestCase(unittest.TestCase):
    def setUp(self):
        self.spec = kit.load_mod_spec()
        self.identity = kit.load_identity()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.private = Path(tmp.name)
        self.cam = kit.SimulatedCamera(self.spec, self.identity)

    def ready(self):
        report = kit.check(self.cam, self.spec, self.identity)
        kit.write_receipt(report, self.private)
        return report

    def apply(self, data=b"SMOD-test", confirm=True):
        return kit.apply(self.cam, self.spec, self.identity, data, lambda _q: confirm, self.private)


class TestModSpec(KitTestCase):
    def test_tables_come_from_the_mod_source(self):
        self.assertEqual(len(self.spec.av_patches), 337)
        self.assertEqual(len(self.spec.vu_offsets), 8)
        self.assertEqual(self.spec.expected_ok, 353)
        self.assertEqual({h.name for h in self.spec.hooks}, {"tkhd_hook_tramp", "stsd_hook_tramp"})
        self.assertTrue(all(h.lib == "/usr/lib/libmpr.so" for h in self.spec.hooks))

    def test_count_mismatch_is_refused(self):
        source = kit.MOD_SOURCE.read_text(encoding="utf-8").replace("#define AV_PATCH_COUNT 337",
                                                                    "#define AV_PATCH_COUNT 338")
        with self.assertRaises(kit.KitError):
            kit.parse_mod_spec(source, kit.BUILD_SCRIPT.read_text(encoding="utf-8"),
                               (ROOT / self.identity["mod_loader"]["reference_layout"]).read_text(encoding="utf-8"))


class TestLogParsing(KitTestCase):
    def test_clean_on_and_off(self):
        on = kit.parse_log_line("ON av=w0151,a0000,x0000 vu=wwwwwwwwwwwwwwww ok=00000161")
        self.assertEqual(kit.classify_log(on, self.spec)[0], "APPLIED")
        off = kit.parse_log_line("OFF av=w0150,a0001,x0000 vu=wwwwwwwwwwwwwwwa ok=00000161")
        self.assertEqual(kit.classify_log(off, self.spec)[0], "STOCK")

    def test_any_mismatch_is_mixed(self):
        for line in ("ON av=w0150,a0000,x0001 vu=wwwwwwwwwwwwwwww ok=00000160",
                     "ON av=err vu=wwwwwwwwwwwwwwww ok=00000010",
                     "ON av=w0151,a0000,x0000 vu=nomap ok=00000151",
                     "ON av=w0151,a0000,x0000 vu=wwwwwwwwwwwwwwwx ok=00000160"):
            self.assertEqual(kit.classify_log(kit.parse_log_line(line), self.spec)[0], "MIXED", line)

    def test_no_line_is_none(self):
        self.assertEqual(kit.classify_log(kit.last_log_line("garbage\n"), self.spec)[0], "NONE")


class TestCheck(KitTestCase):
    def test_stock_camera_is_ready(self):
        self.assertEqual(self.ready().verdict, "READY (stock preflight)")

    def test_wrong_library_build_is_not_ready(self):
        self.cam.lib_hashes["/usr/lib/libmpr.so"] = "1" * 64
        report = kit.check(self.cam, self.spec, self.identity)
        self.assertEqual(report.verdict, "NOT READY")
        self.assertTrue(any("libmpr.so SHA-256" in r for r in report.reasons))

    def test_shifted_hook_bytes_are_not_ready(self):
        cam = kit.SimulatedCamera(self.spec, self.identity, libmpr_shift=-0xF480)
        report = kit.check(cam, self.spec, self.identity)
        self.assertEqual(report.verdict, "NOT READY")
        self.assertTrue(any("hook tkhd_hook_tramp" in r for r in report.reasons))

    def test_partial_screen_state_is_not_ready(self):
        cell = self.spec.vu_offsets[0] + self.spec.vu_words[0][0]
        self.cam._put32(self.cam.VU_BASE + cell, self.spec.vu_words[0][2])
        self.assertEqual(kit.check(self.cam, self.spec, self.identity).verdict, "NOT READY")

    def test_missing_mod_loader_is_not_ready(self):
        self.cam.files.pop(self.identity["mod_loader"]["core"])
        self.assertEqual(kit.check(self.cam, self.spec, self.identity).verdict, "NOT READY")

    def test_check_is_read_only(self):
        kit.check(self.cam, self.spec, self.identity)
        writes = [c for c in self.cam.commands
                  if any(w in c.replace("2>/dev/null", "") for w in (">", "rm ", "echo -ne", "dd ", "/dev/mem"))]
        self.assertEqual(writes, [])

    @unittest.skipUnless(CAPTURED_LIBMPR.is_file(), "captured libmpr.so is local-only")
    def test_captured_camera_libmpr_does_not_hold_the_hook_bytes(self):
        data = CAPTURED_LIBMPR.read_bytes()
        for hook in self.spec.hooks:
            self.assertNotEqual(data[hook.offset:hook.offset + len(hook.stock)], hook.stock, hook.name)
            self.assertEqual(data.find(hook.stock), hook.offset - 0xF480, hook.name)


class TestApply(KitTestCase):
    def test_apply_without_a_check_is_refused_and_writes_nothing(self):
        with self.assertRaises(kit.KitError):
            self.apply()
        self.assertNotIn(kit.REMOTE_MOD, self.cam.files)

    def test_apply_after_a_not_ready_check_is_refused(self):
        self.cam.lib_hashes["/usr/lib/viewUnified2.so"] = "2" * 64
        self.ready()
        with self.assertRaises(kit.KitError):
            self.apply()
        self.assertNotIn(kit.REMOTE_MOD, self.cam.files)

    def test_apply_after_restart_is_refused(self):
        self.ready()
        self.cam.uptime = 5.0
        with self.assertRaises(kit.KitError) as ctx:
            self.apply()
        self.assertIn("restarted", str(ctx.exception))

    def test_unconfirmed_apply_writes_nothing(self):
        self.ready()
        with self.assertRaises(kit.KitError):
            self.apply(confirm=False)
        self.assertNotIn(kit.REMOTE_MOD, self.cam.files)

    def test_ready_apply_stages_the_mod(self):
        self.ready()
        report = self.apply(b"SMOD-bytes")
        self.assertEqual(report.verdict, "STAGED")
        self.assertEqual(self.cam.files[kit.REMOTE_MOD], b"SMOD-bytes")


class TestVerifyAndPutback(KitTestCase):
    def staged(self):
        self.ready()
        kit.write_receipt(self.apply(b"SMOD-owned"), self.private)

    def test_full_cycle(self):
        self.staged()
        self.cam.toggle(True)
        self.assertEqual(kit.verify(self.cam, self.spec, self.identity).verdict, "APPLIED")
        self.cam.toggle(False)
        report = kit.putback(self.cam, self.spec, self.identity, self.private)
        self.assertEqual(report.verdict, "STOCK")
        self.assertTrue(report.facts["staged_mod_removed"])
        self.assertNotIn(kit.REMOTE_MOD, self.cam.files)

    def test_mismatched_on_is_mixed(self):
        self.staged()
        self.cam.toggle(True, mismatched=3)
        report = kit.verify(self.cam, self.spec, self.identity)
        self.assertEqual(report.verdict, "MIXED")
        self.assertTrue(any("OFF" in n for n in report.notes))

    def test_putback_while_on_keeps_the_mod(self):
        self.staged()
        self.cam.toggle(True)
        report = kit.putback(self.cam, self.spec, self.identity, self.private)
        self.assertNotEqual(report.verdict, "STOCK")
        self.assertIn(kit.REMOTE_MOD, self.cam.files)

    def test_putback_leaves_a_foreign_mod_file(self):
        self.cam.files[kit.REMOTE_MOD] = b"someone else's build"
        report = kit.putback(self.cam, self.spec, self.identity, self.private)
        self.assertEqual(report.verdict, "STOCK")
        self.assertIn(kit.REMOTE_MOD, self.cam.files)


class TestExport(KitTestCase):
    def test_export_zips_receipts_and_reads_no_settings(self):
        self.ready()
        out = kit.export(self.cam, self.private)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        self.assertTrue(any(n.endswith("check.json") for n in names))
        self.assertIn("snapshot/opengate_a1.log", names)
        self.assertFalse(any("/setting" in c or "Backup" in c for c in self.cam.commands))

    def test_selftest_runs_end_to_end(self):
        steps = kit.selftest(self.spec, self.identity, self.private)
        self.assertEqual(steps[0], "check: READY (stock preflight)")
        self.assertIn("APPLIED", steps[2])
        self.assertTrue(steps[3].startswith("putback after OFF: STOCK"))
        self.assertIn("NOT READY", steps[4])


class TestIdentity(unittest.TestCase):
    def test_identity_pins_are_well_formed(self):
        identity = json.loads(kit.IDENTITY_FILE.read_text(encoding="utf-8"))
        for path, pin in identity["libraries"].items():
            self.assertTrue(path.startswith("/usr/lib/"))
            self.assertRegex(pin["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(pin["bytes"], 0)
        self.assertFalse(identity["avcam"]["read_by_check"])


if __name__ == "__main__":
    unittest.main()
