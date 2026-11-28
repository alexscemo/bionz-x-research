"""Offline tests for the fully standalone Mod Manager build (no USB, no network, no window)."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from src.a6400.mod_manager import native_dialogs, tools_bundle, usb_driver, webview_runtime
from src.a6400.mod_manager.tools_bundle import BundledAssetError
import tools.mod_manager_gui as gui


def _no_network(url: str) -> bytes:
    raise AssertionError(f"standalone build tried to download {url}")


class FrozenOfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bundle = self.tmp / "meipass"
        self.bundle.mkdir()
        patches = [
            mock.patch.object(tools_bundle, "is_frozen", return_value=True),
            mock.patch.object(tools_bundle, "bundle_root", return_value=self.bundle),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_offline_only_follows_frozen(self) -> None:
        self.assertTrue(tools_bundle.offline_only())

    def test_missing_arrivederci_raises_without_download(self) -> None:
        with self.assertRaises(BundledAssetError) as ctx:
            tools_bundle.ensure_arrivederci_v5(root=self.tmp, data_dir=self.tmp / "data", fetcher=_no_network)
        self.assertIn("Reinstall the Mod Manager", str(ctx.exception))

    def test_missing_mlv2_raises_without_download(self) -> None:
        with self.assertRaises(BundledAssetError):
            tools_bundle.ensure_mlv2_release(root=self.tmp, data_dir=self.tmp / "data", fetcher=_no_network)

    def test_missing_pmca_raises_without_download(self) -> None:
        with mock.patch.object(tools_bundle, "find_pmca_dir", return_value=None):
            with self.assertRaises(BundledAssetError):
                tools_bundle.ensure_pmca(root=self.tmp, data_dir=self.tmp / "data", fetcher=_no_network)

    def test_prepare_tools_only_verifies_when_frozen(self) -> None:
        with mock.patch.object(tools_bundle, "ensure_payload_binaries", side_effect=AssertionError("download")):
            with self.assertRaises(BundledAssetError):
                tools_bundle.prepare_tools(root=self.tmp, data_dir=self.tmp / "data", fetcher=_no_network)

    def test_verify_bundled_tools_names_missing_payloads(self) -> None:
        status = {
            "pmca": {"ready": True},
            "debugger_payloads": {
                "ready": False,
                "files": [{"name": "a6400_usb_debugd.elf", "verified": False, "error": None}],
            },
            "mlv2": {"available": True},
            "arrivederci_v5": {"ready": True},
        }
        with mock.patch.object(tools_bundle, "get_toolchain_status", return_value=status):
            with self.assertRaises(BundledAssetError) as ctx:
                tools_bundle.verify_bundled_tools(root=self.tmp)
        self.assertIn("a6400_usb_debugd.elf", str(ctx.exception))

    def test_verify_bundled_tools_passes_when_complete(self) -> None:
        status = {
            "pmca": {"ready": True},
            "debugger_payloads": {"ready": True, "files": []},
            "mlv2": {"available": True},
            "arrivederci_v5": {"ready": True},
        }
        with mock.patch.object(tools_bundle, "get_toolchain_status", return_value=status):
            result = tools_bundle.verify_bundled_tools(root=self.tmp)
        self.assertTrue(result["offline_only"])


class WebViewRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.runtime = self.tmp / webview_runtime.RUNTIME_DIR_NAME
        self.runtime.mkdir()
        (self.runtime / webview_runtime.RUNTIME_EXE).write_bytes(b"MZ")
        (self.runtime / "bionzx_runtime_version.txt").write_text("131.0.2903.112", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_bundled_runtime_detection(self) -> None:
        self.assertEqual(webview_runtime.bundled_runtime_dir(self.tmp), self.runtime.resolve())
        self.assertIsNone(webview_runtime.bundled_runtime_dir(self.tmp / "nothing"))
        status = webview_runtime.runtime_status(self.tmp)
        self.assertTrue(status["bundled"])
        self.assertEqual(status["version"], "131.0.2903.112")

    def test_prepare_webview_points_pywebview_at_bundle(self) -> None:
        fake_webview = mock.Mock()
        fake_webview.settings = {"WEBVIEW2_RUNTIME_PATH": None}
        calls: list[list[str]] = []
        prepared = webview_runtime.prepare_webview(
            fake_webview, self.tmp / "data", base=self.tmp, runner=lambda cmd: calls.append(cmd) or 0
        )
        self.assertEqual(fake_webview.settings["WEBVIEW2_RUNTIME_PATH"], str(self.runtime.resolve()))
        kwargs = prepared["start_kwargs"]
        self.assertEqual(kwargs["gui"], "edgechromium")
        self.assertEqual(Path(kwargs["storage_path"]), self.tmp / "data" / "webview2")
        self.assertTrue(Path(kwargs["storage_path"]).is_dir())
        self.assertEqual(prepared["report"]["acl"]["status"], "granted")
        self.assertEqual(calls[0][0], "icacls")
        self.assertIn("*S-1-15-2-2:(OI)(CI)(RX)", calls[0])

    def test_acl_granted_once(self) -> None:
        calls: list[list[str]] = []
        runner = lambda cmd: calls.append(cmd) or 0  # noqa: E731
        self.assertEqual(webview_runtime.ensure_app_container_acl(self.runtime, runner=runner)["status"], "granted")
        self.assertEqual(webview_runtime.ensure_app_container_acl(self.runtime, runner=runner)["status"], "already_granted")
        self.assertEqual(len(calls), 1)

    def test_acl_failure_reported_and_retried(self) -> None:
        result = webview_runtime.ensure_app_container_acl(self.runtime, runner=lambda cmd: 5)
        self.assertEqual(result, {"status": "failed", "exit_code": 5})
        self.assertFalse((self.runtime / webview_runtime.ACL_MARKER).exists())

    def test_old_pywebview_is_rejected(self) -> None:
        fake_webview = mock.Mock()
        fake_webview.settings = {}
        with self.assertRaises(RuntimeError):
            webview_runtime.prepare_webview(fake_webview, self.tmp / "data", base=self.tmp, runner=lambda cmd: 0)

    def test_no_bundle_leaves_pywebview_untouched(self) -> None:
        fake_webview = mock.Mock()
        fake_webview.settings = {"WEBVIEW2_RUNTIME_PATH": None}
        prepared = webview_runtime.prepare_webview(fake_webview, self.tmp / "data", base=self.tmp / "none")
        self.assertIsNone(fake_webview.settings["WEBVIEW2_RUNTIME_PATH"])
        self.assertIsNone(prepared["report"]["bundled_runtime"])


class _Event:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeWindow:
    def __init__(self) -> None:
        self.events = mock.Mock()
        self.events.loaded = _Event()
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


class _FakeWebview:
    def __init__(self, *, fail: bool = False) -> None:
        self.settings = {"WEBVIEW2_RUNTIME_PATH": None}
        self.fail = fail
        self.window: _FakeWindow | None = None
        self.start_kwargs: dict | None = None

    def create_window(self, *args, **kwargs) -> _FakeWindow:
        self.window = _FakeWindow()
        return self.window

    def start(self, **kwargs) -> None:
        self.start_kwargs = kwargs
        if self.fail:
            raise RuntimeError("WebView2 failed to initialize")
        for handler in self.window.events.loaded.handlers:
            handler()


class _FakeApi:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.window = None
        self.shutdown_calls = 0

    def attach_window(self, window) -> None:
        self.window = window

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class DesktopLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        patches = [
            mock.patch.object(webview_runtime, "desktop_runtime_available", return_value=True),
            mock.patch.object(webview_runtime, "bundled_runtime_dir", return_value=None),
            mock.patch.object(gui, "_apply_windows_dark_titlebar"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_start_failure_raises_desktop_unavailable_and_keeps_api(self) -> None:
        api = _FakeApi(self.tmp)
        with self.assertRaises(gui.DesktopUnavailable):
            gui.launch_desktop(api, webview_module=_FakeWebview(fail=True))
        self.assertEqual(api.shutdown_calls, 0)  # main() reuses the API for the browser fallback

    def test_missing_runtime_raises_before_window(self) -> None:
        fake = _FakeWebview()
        with mock.patch.object(webview_runtime, "desktop_runtime_available", return_value=False):
            with self.assertRaises(gui.DesktopUnavailable):
                gui.launch_desktop(_FakeApi(self.tmp), webview_module=fake)
        self.assertIsNone(fake.window)

    def test_normal_run_uses_writable_storage_and_shuts_down(self) -> None:
        api = _FakeApi(self.tmp)
        fake = _FakeWebview()
        result = gui.launch_desktop(api, webview_module=fake)
        self.assertTrue(result["loaded"])
        self.assertEqual(Path(fake.start_kwargs["storage_path"]), self.tmp / "webview2")
        self.assertEqual(api.shutdown_calls, 1)

    def test_smoke_mode_writes_result(self) -> None:
        out = self.tmp / "smoke.json"
        fake = _FakeWebview()
        result = gui.launch_desktop(_FakeApi(self.tmp), webview_module=fake, smoke_out=out)
        self.assertTrue(result["loaded"])
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["loaded"])

    def test_main_falls_back_to_browser(self) -> None:
        with mock.patch.object(gui, "ModManagerApi", return_value=_FakeApi(self.tmp)), \
                mock.patch.object(gui, "launch_desktop", side_effect=gui.DesktopUnavailable("no runtime")), \
                mock.patch.object(gui, "launch_web_mode") as web:
            self.assertEqual(gui.main([]), 0)
        web.assert_called_once()
        self.assertTrue(web.call_args.kwargs["app_window"])  # Edge app window, not a browser tab
        self.assertTrue((self.tmp / "logs" / "desktop-launch.log").is_file())


class _FakeProc:
    """Stands in for the Edge process: lives ``life_s`` seconds, then exits."""

    def __init__(self, life_s: float) -> None:
        self.life_s = life_s
        self.t0 = time.monotonic()

    def wait(self, timeout: float | None = None) -> int:
        remaining = self.life_s - (time.monotonic() - self.t0)
        if timeout is not None and remaining > timeout:
            time.sleep(timeout)
            raise subprocess.TimeoutExpired("msedge.exe", timeout)
        time.sleep(max(0.0, remaining))
        return 0


class EdgeAppWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.edge = self.tmp / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        self.edge.parent.mkdir(parents=True)
        self.edge.write_bytes(b"MZ")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_find_edge_in_program_files(self) -> None:
        env = {"ProgramFiles(x86)": str(self.tmp / "none"), "ProgramFiles": str(self.tmp)}
        self.assertEqual(webview_runtime.find_edge(env, use_registry=False), self.edge)
        self.assertIsNone(webview_runtime.find_edge({}, use_registry=False))

    def test_edge_command_uses_throwaway_profile(self) -> None:
        cmd = webview_runtime.edge_app_command(self.edge, "http://127.0.0.1:5000/", self.tmp / "p")
        self.assertEqual(cmd[0], str(self.edge))
        self.assertIn("--app=http://127.0.0.1:5000/", cmd)
        self.assertIn(f"--user-data-dir={self.tmp / 'p'}", cmd)
        self.assertIn("--no-first-run", cmd)

    def test_host_order(self) -> None:
        with mock.patch.object(webview_runtime, "bundled_runtime_dir", return_value=None), \
                mock.patch.object(webview_runtime, "evergreen_runtime_installed", return_value=False), \
                mock.patch.object(webview_runtime, "find_edge", return_value=self.edge):
            self.assertEqual(webview_runtime.desktop_host(), "edge_app")
        with mock.patch.object(webview_runtime, "bundled_runtime_dir", return_value=None), \
                mock.patch.object(webview_runtime, "evergreen_runtime_installed", return_value=True):
            self.assertEqual(webview_runtime.desktop_host(), "webview2_system")
        with mock.patch.object(webview_runtime, "bundled_runtime_dir", return_value=None), \
                mock.patch.object(webview_runtime, "evergreen_runtime_installed", return_value=False), \
                mock.patch.object(webview_runtime, "find_edge", return_value=None):
            self.assertEqual(webview_runtime.desktop_host(), "browser")

    def test_server_stops_when_edge_window_closes(self) -> None:
        api = _FakeApi(self.tmp)
        launched: list[list[str]] = []

        def popen(cmd):
            launched.append(cmd)
            return _FakeProc(0.6)

        with mock.patch.object(webview_runtime, "find_edge", return_value=self.edge), \
                mock.patch.object(gui.webbrowser, "open", side_effect=AssertionError("browser")):
            host = gui.launch_web_mode(api, app_window=True, popen=popen, early_exit_s=0.1)
        self.assertEqual(host, "edge_app")
        self.assertEqual(api.shutdown_calls, 1)
        profile = Path(next(a for a in launched[0] if a.startswith("--user-data-dir=")).split("=", 1)[1])
        self.assertEqual(profile.parent, self.tmp / "edge-app")
        self.assertFalse(profile.exists())  # throwaway profile removed

    def test_edge_exiting_at_once_falls_back_to_browser(self) -> None:
        api = _FakeApi(self.tmp)
        servers: list = []
        real_create = gui.create_web_server

        def create(api_, **kwargs):
            server, token = real_create(api_, **kwargs)
            servers.append(server)
            return server, token

        def fake_open(url):
            threading.Thread(target=servers[0].shutdown, daemon=True).start()
            return True

        with mock.patch.object(webview_runtime, "find_edge", return_value=self.edge), \
                mock.patch.object(gui, "create_web_server", side_effect=create), \
                mock.patch.object(gui.webbrowser, "open", side_effect=fake_open) as browser:
            host = gui.launch_web_mode(api, app_window=True, popen=lambda cmd: _FakeProc(0.0), early_exit_s=1.0)
        self.assertEqual(host, "browser")
        browser.assert_called_once()


class NativeDialogTests(unittest.TestCase):
    def test_filter_string(self) -> None:
        text = native_dialogs.filter_string(("Sony Mod Files (*.mod)", "no pattern here"))
        self.assertEqual(text, "Sony Mod Files (*.mod)\0*.mod\0All files (*.*)\0*.*\0\0")

    def test_filter_string_multiple_patterns(self) -> None:
        text = native_dialogs.filter_string(("Images (*.png *.jpg)",))
        self.assertTrue(text.startswith("Images (*.png *.jpg)\0*.png;*.jpg\0"))

    def test_browser_ui_uses_native_dialogs_on_windows(self) -> None:
        api = gui.ModManagerApi.__new__(gui.ModManagerApi)
        api._window = None
        with mock.patch.object(gui.os, "name", "nt"), \
                mock.patch.object(native_dialogs, "open_file", return_value="C:/x.mod") as pick, \
                mock.patch.object(native_dialogs, "pick_folder", return_value="C:/mlv2") as folder:
            self.assertEqual(api._pick_file_dialog("t", ("Sony Mod Files (*.mod)",)), "C:/x.mod")
            self.assertEqual(api._pick_folder_dialog("t"), "C:/mlv2")
        pick.assert_called_once_with("t", ("Sony Mod Files (*.mod)",))
        folder.assert_called_once_with("t")


class ElevationTests(unittest.TestCase):
    def test_already_elevated_runs_helper_directly(self) -> None:
        with mock.patch.object(usb_driver, "is_elevated", return_value=True), \
                mock.patch.object(usb_driver, "run_direct", return_value=0) as direct, \
                mock.patch.object(usb_driver, "shell_execute_elevated") as uac:
            self.assertEqual(usb_driver._run_elevated(Path("wdi-simple.exe"), ["--vid", "0x054C"], 5.0), 0)
        direct.assert_called_once()
        uac.assert_not_called()

    def test_not_elevated_asks_uac(self) -> None:
        with mock.patch.object(usb_driver, "is_elevated", return_value=False), \
                mock.patch.object(usb_driver, "run_direct") as direct, \
                mock.patch.object(usb_driver, "shell_execute_elevated", return_value=0) as uac:
            self.assertEqual(usb_driver._run_elevated(Path("wdi-simple.exe"), [], 5.0), 0)
        uac.assert_called_once()
        direct.assert_not_called()


class SelfTestReportTests(unittest.TestCase):
    def test_reports_offline_and_runtime_status(self) -> None:
        report = gui.run_self_test(require_frozen_assets=False)
        self.assertIn("offline_only", report)
        self.assertIn("webview2_runtime", report["assets"])


if __name__ == "__main__":
    unittest.main()
