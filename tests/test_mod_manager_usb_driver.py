"""Offline tests for the Mod Manager WinUSB driver step (no USB, no elevation)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from project_paths import PROJECT_ROOT
from src.a6400.mod_manager import usb_driver
from src.a6400.mod_manager.camera_service import LiveCameraTransport
from src.a6400.mod_manager.usb_driver import (
    DriverMissing,
    UsbDeviceRecord,
    blocking_devices,
    build_wdi_args,
    classify_device,
    driver_status,
    helper_status,
    install_winusb,
    is_installable_pid,
    parse_hardware_id,
)
from tools.mod_manager_gui import API_DISPATCH_METHODS, ModManagerApi

MSC = UsbDeviceRecord("USB\\VID_054C&PID_0CA8\\1", 0x054C, 0x0CA8, None, "USBSTOR", "USB Mass Storage Device")
MSC_WINUSB = UsbDeviceRecord("USB\\VID_054C&PID_0CA8\\1", 0x054C, 0x0CA8, None, "WinUSB", "ILCE-6400")
SENSER_NONE = UsbDeviceRecord("USB\\VID_054C&PID_0336\\1", 0x054C, 0x0336, None, "", "Unknown device")
SENSER_WINUSB = UsbDeviceRecord("USB\\VID_054C&PID_0336\\1", 0x054C, 0x0336, None, "WinUSB", "Sony service")
MTP = UsbDeviceRecord("USB\\VID_054C&PID_0CAA\\1", 0x054C, 0x0CAA, None, "WUDFWpdMtp", "ILCE-6400")
OTHER_VENDOR = UsbDeviceRecord("USB\\VID_046D&PID_C52B\\1", 0x046D, 0xC52B, None, "USBSTOR", "Logitech")


def _write_helper(base: Path, *, good_hash: bool = True) -> Path:
    directory = base.joinpath(*usb_driver.HELPER_DIR_PARTS)
    directory.mkdir(parents=True)
    exe = directory / usb_driver.HELPER_EXE
    exe.write_bytes(b"MZ fake wdi-simple")
    digest = hashlib.sha256(exe.read_bytes()).hexdigest() if good_hash else "0" * 64
    # BOM on purpose: Windows PowerShell 5.1 writes one.
    (directory / usb_driver.HELPER_MANIFEST).write_text(
        "\ufeff" + json.dumps({"sha256": digest, "libwdi_commit": "30df0c0e"}), encoding="utf-8"
    )
    return exe


class ClassificationTests(unittest.TestCase):
    def test_parse_hardware_id(self):
        self.assertEqual(parse_hardware_id("USB\\VID_054C&PID_0336&REV_0100"), (0x054C, 0x0336, None))
        self.assertEqual(parse_hardware_id("USB\\VID_054c&PID_0ca8&MI_01"), (0x054C, 0x0CA8, 1))
        self.assertEqual(parse_hardware_id("USB\\VID_03F0&PID_0491&REV_0100&MI_02"), (0x03F0, 0x0491, 2))
        self.assertIsNone(parse_hardware_id("HID\\VID_054C"))

    def test_senser_pids_match_pmca(self):
        sony = PROJECT_ROOT / "Sony-PMCA-RE" / "pmca" / "usb" / "sony.py"
        if not sony.is_file():
            self.skipTest("Sony-PMCA-RE checkout not present")
        text = sony.read_text(encoding="utf-8")
        self.assertIn("SONY_ID_PRODUCT_SENSER = [0x02a9, 0x0336]", text)
        self.assertEqual(usb_driver.SENSER_PIDS, (0x02A9, 0x0336))

    def test_roles_and_blockers(self):
        self.assertEqual(classify_device(SENSER_NONE)["role"], "service_mode")
        self.assertTrue(classify_device(SENSER_NONE)["needs_driver"])
        self.assertFalse(classify_device(SENSER_WINUSB)["needs_driver"])
        self.assertEqual(classify_device(MSC)["role"], "mass_storage")
        self.assertEqual(classify_device(MTP)["role"], "other")
        self.assertFalse(classify_device(MTP)["needs_driver"])
        blockers = blocking_devices([MSC, SENSER_WINUSB, MTP])
        self.assertEqual([b["vid_pid"] for b in blockers], ["054C:0CA8"])

    def test_scan_filters_to_sony(self):
        records = usb_driver.scan_sony_usb(lambda: [MSC, OTHER_VENDOR])
        self.assertEqual(records, [MSC])

    def test_allowlist(self):
        self.assertTrue(is_installable_pid(0x0336, []))
        self.assertTrue(is_installable_pid(0x0CA8, [MSC]))
        self.assertFalse(is_installable_pid(0x0CA8, []))  # absent MSC device: never pre-bind
        self.assertFalse(is_installable_pid(0x0CAA, [MTP]))  # MTP keeps the Windows driver

    def test_driver_status_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = driver_status(scanner=lambda: [MSC, SENSER_WINUSB], helper_base=Path(tmp))
        self.assertTrue(st["platform_supported"])
        self.assertEqual([d["vid_pid"] for d in st["blocking"]], ["054C:0CA8"])
        self.assertFalse(st["helper"]["verified"])

    def test_driver_missing_message_prefix_matches_ui(self):
        exc = DriverMissing([classify_device(SENSER_NONE)])
        self.assertTrue(str(exc).startswith("Camera USB driver required"))
        ui = (PROJECT_ROOT / "tools" / "mod_manager_ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const DRIVER_ERROR_PREFIX = "Camera USB driver required";', ui)
        self.assertEqual(exc.to_dict()["error_code"], "driver_missing")


class HelperAndInstallTests(unittest.TestCase):
    def test_helper_hash_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(helper_status(Path(tmp))["present"])
            _write_helper(Path(tmp))
            st = helper_status(Path(tmp))
            self.assertTrue(st["verified"], st)
            self.assertEqual(st["libwdi_commit"], "30df0c0e")
        with tempfile.TemporaryDirectory() as tmp:
            _write_helper(Path(tmp), good_hash=False)
            self.assertFalse(helper_status(Path(tmp))["verified"])

    def test_wdi_args(self):
        args = build_wdi_args(0x054C, 0x0336, Path("C:/x"))
        self.assertEqual(args[:6], ["--vid", "0x054C", "--pid", "0x0336", "--type", "0"])
        self.assertIn("--silent", args)

    def test_refuses_mismatched_helper(self):
        runner = mock.Mock(return_value=0)
        with tempfile.TemporaryDirectory() as tmp:
            _write_helper(Path(tmp) / "b", good_hash=False)
            with self.assertRaisesRegex(RuntimeError, "helper unavailable"):
                install_winusb(0x0336, data_dir=Path(tmp) / "d", helper_base=Path(tmp) / "b",
                               scanner=lambda: [], runner=runner)
        runner.assert_not_called()

    def test_refuses_non_allowlisted(self):
        runner = mock.Mock(return_value=0)
        with tempfile.TemporaryDirectory() as tmp:
            _write_helper(Path(tmp) / "b")
            with self.assertRaises(ValueError):
                install_winusb(0x0CAA, data_dir=Path(tmp), helper_base=Path(tmp) / "b",
                               scanner=lambda: [MTP], runner=runner)
            with self.assertRaises(ValueError):
                install_winusb(0x0336, vid=0x046D, data_dir=Path(tmp), helper_base=Path(tmp) / "b",
                               scanner=lambda: [], runner=runner)
        runner.assert_not_called()

    def test_bound_after_install_writes_receipt(self):
        scans = iter([[MSC], [MSC_WINUSB]])
        runner = mock.Mock(return_value=0)
        with tempfile.TemporaryDirectory() as tmp:
            _write_helper(Path(tmp) / "b")
            receipt = install_winusb(0x0CA8, data_dir=Path(tmp) / "d", helper_base=Path(tmp) / "b",
                                     scanner=lambda: next(scans), runner=runner, sleep=lambda _s: None)
            self.assertEqual(receipt["status"], "bound")
            saved = json.loads(Path(receipt["receipt_path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["vid_pid"], "054C:0CA8")
            self.assertEqual(saved["before"][0]["service"], "USBSTOR")
            self.assertEqual(saved["after"][0]["service"], "WinUSB")
        exe_arg, args, _timeout = runner.call_args.args
        self.assertTrue(str(exe_arg).endswith("wdi-simple.exe"))
        self.assertIn("0x0CA8", args)

    def test_preinstall_absent_service_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_helper(Path(tmp) / "b")
            receipt = install_winusb(0x0336, data_dir=Path(tmp) / "d", helper_base=Path(tmp) / "b",
                                     scanner=lambda: [], runner=lambda *_a: 0)
        self.assertEqual(receipt["status"], "preinstalled")

    def test_failed_exit_code_raises_but_keeps_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_helper(Path(tmp) / "b")
            with self.assertRaisesRegex(RuntimeError, "exit -5"):
                install_winusb(0x0336, data_dir=Path(tmp) / "d", helper_base=Path(tmp) / "b",
                               scanner=lambda: [], runner=lambda *_a: -5)
            self.assertEqual(len(list((Path(tmp) / "d" / "receipts").glob("usb-driver-*.json"))), 1)


class SenserMappingTests(unittest.TestCase):
    def _transport(self, tmp: str) -> LiveCameraTransport:
        return LiveCameraTransport(PROJECT_ROOT, Path(tmp))

    def _fake_pmca(self, behaviour):
        commands = mock.MagicMock()
        commands.senserShellCommand.side_effect = behaviour
        return mock.patch.dict(sys.modules, {"pmca.commands.usb": commands})

    def test_silent_pmca_return_with_driverless_device_raises_driver_missing(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("src.a6400.mod_manager.camera_service.ensure_pmca"), \
                self._fake_pmca(lambda **_kw: None), \
                mock.patch.object(usb_driver, "scan_sony_usb", return_value=[SENSER_NONE]):
            with self.assertRaises(DriverMissing) as ctx:
                self._transport(tmp).run_senser(lambda cam: None)
        self.assertEqual(ctx.exception.devices[0]["vid_pid"], "054C:0336")

    def test_exception_without_blockers_gives_usb_mode_hint(self):
        def boom(**_kw):
            raise IOError("timeout")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("src.a6400.mod_manager.camera_service.ensure_pmca"), \
                self._fake_pmca(boom), \
                mock.patch.object(usb_driver, "scan_sony_usb", return_value=[SENSER_WINUSB]):
            with self.assertRaisesRegex(RuntimeError, "Auto"):
                self._transport(tmp).run_senser(lambda cam: None)

    def test_completed_session_is_success(self):
        seen = []

        def ok(*, driverName, complete):
            complete("CAM")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("src.a6400.mod_manager.camera_service.ensure_pmca"), \
                self._fake_pmca(ok), \
                mock.patch.object(usb_driver, "scan_sony_usb", side_effect=AssertionError("no scan")):
            self._transport(tmp).run_senser(seen.append)
        self.assertEqual(seen, ["CAM"])


class ApiTests(unittest.TestCase):
    def test_dispatch_and_status(self):
        self.assertIn("usb_driver_status", API_DISPATCH_METHODS)
        self.assertIn("install_usb_driver", API_DISPATCH_METHODS)
        with tempfile.TemporaryDirectory() as tmp:
            api = ModManagerApi(root=PROJECT_ROOT, data_dir=Path(tmp), service=mock.Mock(), drive_provider=lambda: [])
            fake = {"platform_supported": True, "blocking": [], "devices": [classify_device(MSC)]}
            with mock.patch.object(usb_driver, "driver_status", return_value=fake) as status, \
                    mock.patch.object(usb_driver, "our_driver_packages", return_value=[]):
                result = api.usb_driver_status()
            status.assert_called_once()
            self.assertEqual(result["needs_restore"], [])
            self.assertEqual(result["our_packages"], [])

    def test_job_error_carries_driver_missing_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = ModManagerApi(root=PROJECT_ROOT, data_dir=Path(tmp), service=mock.Mock(), drive_provider=lambda: [])

            def raise_missing(_cancel):
                raise DriverMissing([classify_device(SENSER_NONE)])

            api._start_worker("install", raise_missing)
            for _ in range(200):
                events = api.poll_events(0)
                if not events["job_active"] and events["events"]:
                    break
                import time
                time.sleep(0.01)
        err = events["events"][-1]
        self.assertEqual(err["kind"], "job_error")
        self.assertEqual(err["error_code"], "driver_missing")


if __name__ == "__main__":
    unittest.main()
