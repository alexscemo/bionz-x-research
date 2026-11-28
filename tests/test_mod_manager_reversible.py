"""Offline tests for one-click reversibility: driver broker, driver restore, ledger, Restore to stock."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from project_paths import PROJECT_ROOT
from src.a6400.mod_manager import driver_broker, usb_driver
from src.a6400.mod_manager.camera_service import CameraService
from src.a6400.mod_manager.ledger import KIND_DEBUGGER, KIND_TWEAK, CameraLedger, b64
from src.a6400.mod_manager.updates import check_for_update
from src.a6400.mod_manager.usb_driver import DriverPackage, UsbDeviceRecord
from tools.mod_manager_gui import API_DISPATCH_METHODS, ModManagerApi

IDENTITY = {"model_name": "ILCE-6400", "model_code": "0032858730", "firmware_version": "2.00"}
ABSENT = "Senser file control error 130"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeSenserCam:
    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)
        self.ops: list[tuple[str, str]] = []

    def readFile(self, path):
        if path not in self.files:
            raise Exception(f"{ABSENT} for {path}")
        return self.files[path]

    def writeFile(self, path, data):
        self.ops.append(("write", path))
        self.files[path] = bytes(data)

    def deleteFile(self, path):
        self.ops.append(("delete", path))
        if path not in self.files:
            raise Exception(f"{ABSENT} for {path}")
        del self.files[path]


class FakeTransport:
    def __init__(self, cam: FakeSenserCam, tweaks: dict[str, bool]):
        self.cam = cam
        self.tweaks = dict(tweaks)

    def detect_msc_identity(self):
        return dict(IDENTITY)

    def run_senser(self, callback):
        callback(self.cam)

    def query_tweaks(self, cam):
        return [{"id": k, "enabled": v} for k, v in self.tweaks.items()]

    def apply_tweaks(self, cam, desired):
        self.tweaks.update({k: bool(v) for k, v in desired.items()})
        return [{"id": k, "enabled": self.tweaks[k], "value": ""} for k in desired]


class FakeOps:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def bind(self, pid):
        self.calls.append(("bind", pid))
        return {"status": "bound" if pid not in usb_driver.SENSER_PIDS else "preinstalled"}

    def restore(self, pid):
        self.calls.append(("restore", pid))
        return {"status": "restored"}


class FakeConn:
    def __init__(self, messages):
        self.inbox = list(messages)
        self.sent: list[dict] = []

    def recv(self):
        if not self.inbox:
            raise EOFError
        return self.inbox.pop(0)

    def send(self, msg):
        self.sent.append(msg)


def _plan(tweaks):
    return {
        "blocking_errors": [], "requires_confirmation": False, "mods_to_add": [], "mods_to_update": [],
        "tweak_changes": [{"id": k, "enabled": v} for k, v in tweaks.items()],
        "needs_debugger_bootstrap": False, "needs_runtime_sync": False, "camera_model": "ILCE-6400",
    }


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------
class BrokerServeTests(unittest.TestCase):
    def test_restores_session_msc_binds_on_disconnect(self):
        ops = FakeOps()
        conn = FakeConn([{"cmd": "bind", "pid": 0x0336}, {"cmd": "bind", "pid": 0x0CA8}])  # then EOF
        restored = driver_broker.serve(conn, ops)
        self.assertEqual(restored, [0x0CA8])  # service-mode PID stays bound by design
        self.assertEqual(ops.calls[-1], ("restore", 0x0CA8))

    def test_explicit_restore_is_not_repeated(self):
        ops = FakeOps()
        conn = FakeConn([{"cmd": "bind", "pid": 0x0CA8}, {"cmd": "restore", "pid": 0x0CA8}, {"cmd": "quit"}])
        driver_broker.serve(conn, ops)
        self.assertEqual([c for c in ops.calls if c[0] == "restore"], [("restore", 0x0CA8)])

    def test_rejects_unknown_commands(self):
        conn = FakeConn([{"cmd": "exec", "argv": ["calc.exe"]}, "garbage", {"cmd": "quit"}])
        driver_broker.serve(conn, FakeOps())
        self.assertFalse(conn.sent[0]["ok"])
        self.assertFalse(conn.sent[1]["ok"])
        self.assertTrue(conn.sent[2]["ok"])

    def test_errors_are_reported_and_serving_continues(self):
        ops = FakeOps()
        ops.bind = mock.Mock(side_effect=ValueError("not allowlisted"))
        conn = FakeConn([{"cmd": "bind", "pid": 0x1234}, {"cmd": "ping"}])
        driver_broker.serve(conn, ops)
        self.assertIn("not allowlisted", conn.sent[0]["error"])
        self.assertTrue(conn.sent[1]["ok"])


@unittest.skipUnless(os.name == "nt", "named pipes are Windows-only")
class BrokerPipeTests(unittest.TestCase):
    def _launcher(self, ops, key_override=None):
        from multiprocessing.connection import Client

        def launch(pipe, token, _data_dir):
            def run():
                try:
                    conn = Client(pipe, family="AF_PIPE", authkey=bytes.fromhex(key_override or token))
                except Exception:
                    return
                driver_broker.serve(conn, ops)
                conn.close()

            threading.Thread(target=run, daemon=True).start()

        return launch

    def test_round_trip_and_restore_on_close(self):
        ops = FakeOps()
        with tempfile.TemporaryDirectory() as tmp:
            client = driver_broker.BrokerClient(Path(tmp), launcher=self._launcher(ops), connect_timeout_s=10)
            self.assertEqual(client.call("bind", pid=0x0CA8)["status"], "bound")
            client.close()
        for _ in range(100):
            if ("restore", 0x0CA8) in ops.calls:
                break
            threading.Event().wait(0.02)
        self.assertIn(("restore", 0x0CA8), ops.calls)

    def test_wrong_token_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = driver_broker.BrokerClient(
                Path(tmp), launcher=self._launcher(FakeOps(), key_override="00" * 32), connect_timeout_s=5
            )
            with self.assertRaises(driver_broker.BrokerUnavailable):
                client.call("ping")

    def test_declined_uac_is_unavailable(self):
        def declined(*_a):
            raise PermissionError("Administrator approval was declined")

        with tempfile.TemporaryDirectory() as tmp:
            client = driver_broker.BrokerClient(Path(tmp), launcher=declined, connect_timeout_s=2)
            with self.assertRaisesRegex(driver_broker.BrokerUnavailable, "declined"):
                client.call("bind", pid=0x0CA8)


# ---------------------------------------------------------------------------
# Driver package ownership and restore
# ---------------------------------------------------------------------------
class DriverRestoreTests(unittest.TestCase):
    def _fake_windows(self, root: Path) -> None:
        repo = root / "System32" / "DriverStore" / "FileRepository"
        (repo / "bionzx_054c_0ca8.inf_amd64_abc").mkdir(parents=True)
        (repo / "bionzx_054c_0ca8.inf_amd64_abc" / "bionzx_054c_0ca8.inf").write_text("[Version]\nours-0ca8\n")
        (repo / "other.inf_amd64_x").mkdir()
        (repo / "other.inf_amd64_x" / "other.inf").write_text("[Version]\nnot ours\n")
        inf = root / "INF"
        inf.mkdir()
        (inf / "oem7.inf").write_text("[Version]\nours-0ca8\n")
        (inf / "oem8.inf").write_text("[Version]\nnot ours\n")
        (inf / "oem9.inf").write_text("[Version]\nours-0ca8 but edited\n")

    def test_ownership_is_proven_by_identical_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fake_windows(Path(tmp))
            pkgs = usb_driver.our_driver_packages(Path(tmp))
        self.assertEqual(pkgs, [DriverPackage("oem7.inf", "bionzx_054c_0ca8.inf", 0x054C, 0x0CA8)])

    def test_needs_restore_only_for_our_package_on_msc_pid(self):
        pkgs = [DriverPackage("oem7.inf", "bionzx_054c_0ca8.inf", 0x054C, 0x0CA8)]
        ours = UsbDeviceRecord("i", 0x054C, 0x0CA8, None, "WinUSB", "", "oem7.inf")
        zadig = UsbDeviceRecord("i", 0x054C, 0x0CA8, None, "WinUSB", "", "oem3.inf")
        senser = UsbDeviceRecord("i", 0x054C, 0x0336, None, "WinUSB", "", "oem7.inf")
        self.assertEqual(len(usb_driver.needs_restore([ours, zadig, senser], pkgs)), 1)

    def test_restore_deletes_only_matching_packages(self):
        pkgs = [DriverPackage("oem7.inf", "bionzx_054c_0ca8.inf", 0x054C, 0x0CA8),
                DriverPackage("oem5.inf", "bionzx_054c_0336.inf", 0x054C, 0x0336)]
        bound = UsbDeviceRecord("i", 0x054C, 0x0CA8, None, "WinUSB", "", "oem7.inf")
        stock = UsbDeviceRecord("i", 0x054C, 0x0CA8, None, "USBSTOR", "", "usbstor.inf")
        scans = iter([[bound], [stock]])
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            receipt = usb_driver.restore_windows_driver(
                0x0CA8, data_dir=Path(tmp), packages_provider=lambda: pkgs,
                pnputil=lambda args: calls.append(args) or 0, scanner=lambda: next(scans), sleep=lambda _s: None,
            )
            self.assertTrue(Path(receipt["receipt_path"]).is_file())
        self.assertEqual(receipt["status"], "restored")
        self.assertEqual(calls[0], ["/delete-driver", "oem7.inf", "/uninstall", "/force"])
        self.assertNotIn("oem5.inf", json.dumps(calls))

    def test_restore_all_and_nothing_to_restore(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            none = usb_driver.restore_windows_driver(None, data_dir=Path(tmp), packages_provider=lambda: [],
                                                     pnputil=lambda a: calls.append(a) or 0, scanner=lambda: [])
        self.assertEqual(none["status"], "nothing_to_restore")
        self.assertEqual(calls, [])

    def test_failed_pnputil_raises(self):
        pkgs = [DriverPackage("oem7.inf", "bionzx_054c_0ca8.inf", 0x054C, 0x0CA8)]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "Could not restore"):
                usb_driver.restore_windows_driver(None, data_dir=Path(tmp), packages_provider=lambda: pkgs,
                                                  pnputil=lambda a: 5, scanner=lambda: [], sleep=lambda _s: None)


# ---------------------------------------------------------------------------
# Ledger and Restore to stock
# ---------------------------------------------------------------------------
class LedgerTests(unittest.TestCase):
    def test_snapshot_is_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = CameraLedger(Path(tmp), IDENTITY)
            led.ensure_snapshot(tweaks={"recLimit": False})
            led.ensure_snapshot(tweaks={"recLimit": True}, preload=b"X", preload_existed=True)
            again = CameraLedger(Path(tmp), IDENTITY)
        self.assertEqual(again.snapshot["tweaks"], {"recLimit": False})
        self.assertEqual(again.snapshot["preload_b64"], b64(b"X"))

    def test_install_records_tweak_changes_and_restore_reverts_them(self):
        cam = FakeSenserCam({})
        transport = FakeTransport(cam, {"recLimit": False, "recLimit4k": False, "language": True})
        with tempfile.TemporaryDirectory() as tmp:
            svc = CameraService(root=PROJECT_ROOT, data_dir=Path(tmp), transport=transport)
            svc.execute_install(_plan({"recLimit": True, "recLimit4k": True}), {}, {})
            ledger = svc.get_ledger()
            self.assertEqual(len(ledger["active"]), 2)
            self.assertEqual(transport.tweaks["recLimit"], True)

            receipt = svc.restore_stock()
            self.assertEqual(receipt["status"], "restored")
            self.assertEqual(transport.tweaks, {"recLimit": False, "recLimit4k": False, "language": True})
            self.assertEqual(svc.get_ledger()["active"], [])
            self.assertEqual(svc.restore_stock()["status"], "nothing_to_restore")

    def test_undo_last_only_reverts_newest_group(self):
        cam = FakeSenserCam({})
        transport = FakeTransport(cam, {"recLimit": False, "language": False})
        with tempfile.TemporaryDirectory() as tmp:
            svc = CameraService(root=PROJECT_ROOT, data_dir=Path(tmp), transport=transport)
            led = CameraLedger(Path(tmp), IDENTITY)
            led.append(KIND_TWEAK, "g1", False, True, tweak_id="recLimit")
            led.append(KIND_TWEAK, "g2", False, True, tweak_id="language")
            transport.tweaks = {"recLimit": True, "language": True}
            svc.restore_stock(last_group_only=True)
            self.assertEqual(transport.tweaks, {"recLimit": True, "language": False})
            self.assertEqual([e["group"] for e in svc.get_ledger()["active"]], ["g1"])

    def _debugger_entry(self, tmp: Path, before: bytes, existed: bool):
        led = CameraLedger(tmp, IDENTITY)
        led.append(KIND_DEBUGGER, "g1", {"preload_b64": b64(before), "preload_existed": existed},
                   {"preload_b64": b64(before + b"/setting/usb_debug_launcher.so\n")},
                   remote_files=["/setting/a6400_usb_debugd.elf", "/setting/usb_debug_launcher.so"])

    def test_debugger_uninstall_restores_preload_before_deleting_payloads(self):
        files = {"/etc/ld.so.preload": b"/lib/a.so\n/setting/usb_debug_launcher.so\n",
                 "/setting/a6400_usb_debugd.elf": b"ELF", "/setting/usb_debug_launcher.so": b"SO"}
        cam = FakeSenserCam(files)
        with tempfile.TemporaryDirectory() as tmp:
            self._debugger_entry(Path(tmp), b"/lib/a.so\n", existed=True)
            svc = CameraService(root=PROJECT_ROOT, data_dir=Path(tmp), transport=FakeTransport(cam, {}))
            receipt = svc.restore_stock()
        self.assertEqual(cam.files, {"/etc/ld.so.preload": b"/lib/a.so\n"})
        self.assertEqual(cam.ops[0], ("write", "/etc/ld.so.preload"))  # preload first, then payloads
        self.assertEqual(receipt["debugger"]["removed"], ["/setting/a6400_usb_debugd.elf", "/setting/usb_debug_launcher.so"])

    def test_debugger_uninstall_deletes_preload_that_did_not_exist(self):
        files = {"/etc/ld.so.preload": b"/setting/usb_debug_launcher.so\n", "/setting/usb_debug_launcher.so": b"SO"}
        cam = FakeSenserCam(files)
        with tempfile.TemporaryDirectory() as tmp:
            self._debugger_entry(Path(tmp), b"", existed=False)
            svc = CameraService(root=PROJECT_ROOT, data_dir=Path(tmp), transport=FakeTransport(cam, {}))
            svc.restore_stock()
        self.assertEqual(cam.files, {})  # daemon file was already absent; nothing left behind

    def test_failed_restore_keeps_entries_active(self):
        cam = FakeSenserCam({"/etc/ld.so.preload": b"x"})
        cam.writeFile = mock.Mock(side_effect=IOError("usb dropped"))
        with tempfile.TemporaryDirectory() as tmp:
            self._debugger_entry(Path(tmp), b"orig", existed=True)
            svc = CameraService(root=PROJECT_ROOT, data_dir=Path(tmp), transport=FakeTransport(cam, {}))
            with self.assertRaises(IOError):
                svc.restore_stock()
            self.assertEqual(len(svc.get_ledger()["active"]), 1)


# ---------------------------------------------------------------------------
# Update check and GUI API
# ---------------------------------------------------------------------------
class UpdateAndApiTests(unittest.TestCase):
    def test_update_check_picks_newest_stable_tag(self):
        releases = [
            {"tag_name": "look-lab-v9.0.0", "html_url": "x"},
            {"tag_name": "mod-manager-v1.2.0", "html_url": "u120"},
            {"tag_name": "mod-manager-v1.10.0", "html_url": "u1100", "prerelease": True},
            {"tag_name": "mod-manager-v1.3.1", "html_url": "u131"},
        ]
        res = check_for_update("1.2.0", fetcher=lambda _u: json.dumps(releases).encode())
        self.assertEqual((res["latest"], res["update_available"], res["url"]), ("1.3.1", True, "u131"))
        self.assertFalse(check_for_update("dev", fetcher=lambda _u: json.dumps(releases).encode())["update_available"])

    def test_update_check_offline_is_not_an_error(self):
        def offline(_u):
            raise OSError("no network")

        self.assertIn("error", check_for_update("1.0.0", fetcher=offline))

    def test_new_methods_are_dispatchable(self):
        for name in ("restore_windows_drivers", "detect_and_prepare", "check_for_update", "open_release_page",
                     "get_ledger", "start_restore_stock", "start_undo_last"):
            self.assertIn(name, API_DISPATCH_METHODS)

    def test_detect_and_prepare_switches_once(self):
        service = mock.Mock()
        service.detect_camera.side_effect = [{"connected": False, "state": "offline"},
                                             {"connected": True, "state": "msc_verified"},
                                             {"connected": False, "state": "offline"}]
        service.transport.switch_to_msc.return_value = [{"sent": True}]
        with tempfile.TemporaryDirectory() as tmp:
            api = ModManagerApi(root=PROJECT_ROOT, data_dir=Path(tmp), service=service, drive_provider=lambda: [])
            with mock.patch.object(api, "usb_driver_status", return_value={}):
                first = api.detect_and_prepare()
                second = api.detect_and_prepare()
        self.assertEqual(first["camera"]["state"], "msc_verified")
        self.assertIsNone(second["switched"])
        service.transport.switch_to_msc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
