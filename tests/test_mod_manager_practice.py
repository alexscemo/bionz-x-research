"""Practice mode: the simulated camera never reaches USB, drivers, SD cards or the network."""
from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from src.a6400.mod_manager.practice_service import PracticeCameraService, PracticeTransport
from tools.mod_manager_gui import ModManagerApi, main


def _wait_job(api: ModManagerApi, timeout_s: float = 10.0) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        res = api.poll_events(0)
        if not res["job_active"] and any(e["kind"] in ("job_done", "job_error") for e in res["events"]):
            return res["events"]
        time.sleep(0.02)
    raise AssertionError("practice job did not finish")


class PracticeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.svc = PracticeCameraService(data_dir=Path(self.tmp.name), step_delay_s=0, sleep=lambda _s: None)

    def test_transport_refuses_every_live_call(self) -> None:
        transport = PracticeTransport()
        self.assertIsNone(getattr(transport, "switch_to_msc", None))
        with self.assertRaises(AttributeError):
            transport.detect_msc_identity()

    def test_camera_appears_after_first_poll(self) -> None:
        first = self.svc.detect_camera()
        second = self.svc.detect_camera()
        self.assertFalse(first["connected"])
        self.assertTrue(second["connected"])
        self.assertEqual(second["state"], "msc_verified")
        self.assertTrue(second["practice"])

    def test_install_then_restore_round_trip(self) -> None:
        self.svc.detect_camera()
        plan = {
            "blocking_errors": [], "requires_confirmation": True, "needs_debugger_bootstrap": True,
            "needs_runtime_sync": True, "tweak_changes": [{"id": "recLimit", "title": "Unlimited", "enabled": True}],
            "mods_to_add": [{"filename": "a.mod", "sha256": "00"}], "mods_to_update": [], "mods_to_keep": [],
        }
        with self.assertRaises(RuntimeError):
            self.svc.execute_install(plan, {}, {}, confirmed_permanent=False)
        events: list[dict] = []
        receipt = self.svc.execute_install(plan, {}, {}, confirmed_permanent=True, progress_cb=events.append)
        self.assertEqual(receipt["status"], "success")
        self.assertIn("waiting_power_cycle", [e["status"] for e in events])
        cam = self.svc.detect_camera()
        self.assertEqual(cam["state"], "debugger_ready")
        self.assertEqual([m["filename"] for m in cam["staged_mods"]], ["a.mod"])
        self.assertEqual(len(self.svc.get_ledger()["active"]), 2)

        undone = self.svc.restore_stock()
        self.assertEqual(undone["status"], "restored")
        self.assertEqual(self.svc.get_ledger()["active"], [])
        self.assertEqual(self.svc.detect_camera()["state"], "msc_verified")
        self.assertEqual(self.svc.restore_stock()["status"], "nothing_to_restore")


class PracticeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = ModManagerApi(data_dir=Path(self.tmp.name), practice=True)
        self.api.service._step_delay_s = 0

    def test_initial_state_reports_practice(self) -> None:
        self.assertTrue(self.api.get_initial_state()["practice"])

    def test_live_only_operations_are_refused(self) -> None:
        self.assertEqual(self.api.usb_driver_status()["devices"], [])
        self.assertEqual(self.api.list_sd_drives()["drives"], [])
        self.assertTrue(self.api.check_for_update()["disabled"])
        for call in (lambda: self.api.install_usb_driver(0x0336),
                     self.api.restore_windows_drivers,
                     lambda: self.api.start_sd_sync("E:\\", {})):
            with self.assertRaisesRegex(RuntimeError, "practice mode"):
                call()

    def test_wizard_detect_finds_pretend_camera(self) -> None:
        self.assertFalse(self.api.detect_and_prepare()["camera"]["connected"])
        res = self.api.detect_and_prepare()
        self.assertTrue(res["camera"]["connected"])
        self.assertTrue(res["usb_driver"]["practice"] if res["usb_driver"] else True)

    def test_full_install_job_uses_real_planner(self) -> None:
        self.api.detect_and_prepare()
        self.api.detect_and_prepare()
        catalog = self.api.get_initial_state()["catalog"]
        tweak_ids = [t["id"] for t in catalog.get("pmca_tweaks", [])][:1]
        selection = {"runtime_mods": {}, "pmca_tweaks": {tid: True for tid in tweak_ids}}
        preview = self.api.preview_install(selection, rescan=False)
        self.assertEqual(preview["plan"]["blocking_errors"], [])
        self.api.start_install(selection, confirmed_permanent=True)
        events = _wait_job(self.api)
        done = [e for e in events if e["kind"] == "job_done"]
        self.assertTrue(done, events)
        self.assertEqual(done[-1]["result"]["status"], "success")
        self.assertTrue(done[-1]["result"]["practice"])

    def test_switching_back_uses_live_service_lazily(self) -> None:
        self.assertIsNone(self.api._live_service)
        state = self.api.exit_practice_mode()
        self.assertFalse(state["practice"])
        self.assertFalse(self.api.practice)
        self.assertTrue(self.api.enter_practice_mode()["practice"])

    def test_cli_flag_is_known(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["--help"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
