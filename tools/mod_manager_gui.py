"""Windows native WebView2 GUI and local web fallback for the BIONZ X Mod Manager."""
from __future__ import annotations

import argparse
import base64
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
from typing import Any, Callable
from urllib.parse import urlparse
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.a6400.mod_manager.camera_service import CameraService
from src.a6400.mod_manager.practice_service import PracticeCameraService
from src.a6400.mod_manager.catalog import (
    build_catalog,
    custom_mods_dir,
    import_custom_mod,
    import_custom_mod_bytes,
    remove_custom_mod,
)
from src.a6400.mod_manager.sd_sync import execute_sd_sync, list_sd_drives, plan_sd_sync
from src.a6400.mod_manager.smod import SMOD_MAX_TOTAL_BYTES
from src.a6400.mod_manager import usb_driver
from src.a6400.mod_manager.tools_bundle import (
    bundle_root,
    get_toolchain_status,
    is_frozen,
    load_settings,
    prepare_tools,
    repo_root,
    save_settings,
    user_data_dir,
    validate_mlv2_build,
)

UI_DIR = bundle_root() / "tools" / "mod_manager_ui"


def _apply_windows_dark_titlebar() -> None:
    """Ask Windows 10/11 DWM to paint our WebView2 window titlebar in dark mode."""
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        hwnd = user32.GetActiveWindow()
        if not hwnd:
            return
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMUTABLE_DARK_MODE (Win11 / Win10 1903+)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(attr),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
    except Exception:
        pass


class ModManagerApi:
    """JS-to-Python bridge exposed to WebView2 and the local HTTP fallback server."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        data_dir: Path | None = None,
        service: CameraService | None = None,
        drive_provider: Any = None,
        practice: bool = False,
    ):
        self.root = repo_root(root)
        self.data_dir = user_data_dir(data_dir, root=self.root)
        self._live_service: CameraService | None = service
        if practice:
            self.service = PracticeCameraService(root=self.root, data_dir=self.data_dir)
        else:
            self.service = service or CameraService(root=self.root, data_dir=self.data_dir)
            self._live_service = self.service
        self.drive_provider = drive_provider
        self._window: Any = None

        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=400)
        self._seq = 0
        self._job_active = False
        self._job_kind: str | None = None
        self._cancel_event: threading.Event | None = None
        self._last_camera: dict[str, Any] = {
            "connected": False,
            "state": "offline",
            "transport": "none",
            "debugger_active": False,
            "model_name": None,
            "firmware_version": None,
            "mlv2_hook_state": "none",
            "staged_mods": [],
            "detail": "Click 'Scan Camera' after connecting your Sony camera via USB.",
        }

    def attach_window(self, window: Any) -> None:
        self._window = window

    @property
    def practice(self) -> bool:
        """True while the simulated camera is in use (no USB, drivers, SD or network)."""
        return isinstance(self.service, PracticeCameraService)

    def _refuse_in_practice(self, what: str) -> None:
        if self.practice:
            raise RuntimeError(f"{what} is not available in practice mode (no real camera is used).")

    def _switch_service(self, practice: bool) -> dict[str, Any]:
        with self._lock:
            if self._job_active:
                raise RuntimeError(f"Wait until the current operation ({self._job_kind}) finishes.")
            if practice and not self.practice:
                self.service = PracticeCameraService(root=self.root, data_dir=self.data_dir)
            elif not practice and self.practice:
                if self._live_service is None:
                    self._live_service = CameraService(root=self.root, data_dir=self.data_dir)
                self.service = self._live_service
            self._last_camera = {**self._last_camera, "connected": False, "state": "offline",
                                 "debugger_active": False, "model_name": None, "firmware_version": None,
                                 "staged_mods": [], "mlv2_hook_state": "none"}
            if not practice:
                self._msc_switch_tried = False
        return self.get_initial_state()

    def enter_practice_mode(self) -> dict[str, Any]:
        """Swap in the simulated camera so the whole flow can be tried with nothing attached."""
        return self._switch_service(True)

    def exit_practice_mode(self) -> dict[str, Any]:
        """Return to the real camera service."""
        return self._switch_service(False)

    def _emit_event(self, kind: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            self._events.append(
                {
                    "seq": self._seq,
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "kind": kind,
                    **payload,
                }
            )

    def poll_events(self, after_seq: int = 0) -> dict[str, Any]:
        """Return buffered worker events newer than ``after_seq`` plus active job state."""
        with self._lock:
            events = [e for e in self._events if e["seq"] > int(after_seq)]
            return {
                "events": events,
                "last_seq": self._seq,
                "job_active": self._job_active,
                "job_kind": self._job_kind,
            }

    def _target_id(self) -> str:
        tid = str(load_settings(self.data_dir).get("target_id") or "ilce_6400")
        return tid if tid in ("ilce_6400", "ilce_6400a") else "ilce_6400"

    def _toolchain(self) -> dict[str, Any]:
        return get_toolchain_status(root=self.root, data_dir=self.data_dir, target_id=self._target_id())

    def _catalog(self) -> dict[str, Any]:
        return build_catalog(root=self.root, data_dir=self.data_dir, target_id=self._target_id())

    def set_target_profile(self, target_id: str) -> dict[str, Any]:
        if target_id not in ("ilce_6400", "ilce_6400a"):
            raise ValueError(f"Unsupported target profile: {target_id!r}")
        settings = save_settings(self.data_dir, {"target_id": target_id})
        return {
            "target_id": target_id,
            "settings": settings,
            "catalog": self._catalog(),
            "toolchain": self._toolchain(),
        }

    def get_initial_state(self) -> dict[str, Any]:
        return {
            "catalog": self._catalog(),
            "toolchain": self._toolchain(),
            "camera": self._last_camera,
            "settings": load_settings(self.data_dir),
            "receipts": self.service.list_receipts(),
            "platform": {"os": os.name, "frozen": is_frozen()},
            "practice": self.practice,
        }

    def scan_camera(self) -> dict[str, Any]:
        toolchain = self._toolchain()
        snapshot = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
        self._last_camera = snapshot
        return {"camera": snapshot, "toolchain": toolchain}

    def read_live_tweaks(self) -> dict[str, Any]:
        return self.service.read_live_tweaks()

    def import_custom_mod_bytes(self, filename: str, base64_data: str) -> dict[str, Any]:
        if len(base64_data) > (SMOD_MAX_TOTAL_BYTES * 2):
            raise ValueError("Encoded .mod file exceeds maximum 1 MiB loader size")
        raw = base64.b64decode(base64_data, validate=True)
        added = import_custom_mod_bytes(filename, raw, self.data_dir)
        return {"added": added, "catalog": self._catalog()}

    def import_custom_mod_dialog(self) -> dict[str, Any]:
        chosen = self._pick_file_dialog("Select Sony .mod binary", ("Sony Mod Files (*.mod)",))
        if not chosen:
            return {"cancelled": True}
        added = import_custom_mod(Path(chosen), self.data_dir)
        return {"cancelled": False, "added": added, "catalog": self._catalog()}

    def remove_custom_mod(self, mod_id: str) -> dict[str, Any]:
        removed = remove_custom_mod(mod_id, self.data_dir)
        return {"removed": removed, "catalog": self._catalog()}

    def preview_install(
        self,
        selection: dict[str, Any],
        known_tweaks: dict[str, bool] | None = None,
        rescan: bool = False,
    ) -> dict[str, Any]:
        toolchain = self._toolchain()
        catalog = self._catalog()
        camera = self._last_camera
        if rescan or not camera.get("connected"):
            camera = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
            self._last_camera = camera
        plan = self.service.plan_install(
            selection,
            catalog,
            camera,
            toolchain["mlv2"],
            known_tweaks=known_tweaks,
        )
        return {"plan": plan, "camera": camera, "toolchain": toolchain}

    def _start_worker(self, kind: str, target_fn: Any) -> dict[str, Any]:
        with self._lock:
            if self._job_active:
                raise RuntimeError(f"Another operation ({self._job_kind}) is already running")
            self._job_active = True
            self._job_kind = kind
            self._cancel_event = threading.Event()
            cancel_event = self._cancel_event

        def _runner() -> None:
            try:
                target_fn(cancel_event)
            except usb_driver.DriverMissing as exc:
                self._emit_event("job_error", {"job": kind, "error": str(exc), **exc.to_dict()})
            except Exception as exc:
                self._emit_event("job_error", {"job": kind, "error": str(exc)})
            finally:
                with self._lock:
                    self._job_active = False
                    self._job_kind = None
                    self._cancel_event = None

        thread = threading.Thread(target=_runner, name=f"modmgr-{kind}", daemon=True)
        thread.start()
        return {"started": True, "job": kind}

    def start_install(
        self,
        selection: dict[str, Any],
        confirmed_permanent: bool = False,
        known_tweaks: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Launch the 1-Click install pipeline on a background thread and stream progress events."""

        def _run(cancel_event: threading.Event) -> None:
            self._emit_event(
                "progress",
                {"step": "scan", "status": "running", "message": "Scanning camera and verifying tools..."},
            )
            toolchain = self._toolchain()
            catalog = self._catalog()
            camera = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
            self._last_camera = camera
            plan = self.service.plan_install(
                selection,
                catalog,
                camera,
                toolchain["mlv2"],
                known_tweaks=known_tweaks,
            )
            if plan["blocking_errors"]:
                raise RuntimeError("; ".join(plan["blocking_errors"]))
            if not plan["has_changes"]:
                self._emit_event(
                    "job_done",
                    {
                        "job": "install",
                        "result": {"status": "no_changes", "message": "Camera already matches selected toggles."},
                        "camera": camera,
                        "receipts": self.service.list_receipts(),
                    },
                )
                return

            receipt = self.service.execute_install(
                plan,
                catalog,
                toolchain["mlv2"],
                confirmed_permanent=bool(confirmed_permanent),
                progress_cb=lambda ev: self._emit_event("progress", ev),
                cancel_event=cancel_event,
            )
            refreshed_camera = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
            self._last_camera = refreshed_camera
            self._emit_event(
                "job_done",
                {
                    "job": "install",
                    "result": receipt,
                    "camera": refreshed_camera,
                    "receipts": self.service.list_receipts(),
                },
            )

        return self._start_worker("install", _run)

    def cancel_active_job(self) -> dict[str, Any]:
        with self._lock:
            if self._cancel_event is not None:
                self._cancel_event.set()
                return {"cancelling": True}
            return {"cancelling": False}

    def list_sd_drives(self) -> dict[str, Any]:
        if self.practice:
            return {"drives": [], "practice": True}
        return {"drives": list_sd_drives(drive_provider=self.drive_provider)}

    def _selected_mod_dicts(self, runtime_selection: dict[str, bool]) -> tuple[list[dict[str, Any]], set[str]]:
        catalog = self._catalog()
        desired: list[dict[str, Any]] = []
        managed: set[str] = set()
        for mod in catalog["runtime_mods"]:
            managed.add(str(mod["filename"]))
            if runtime_selection.get(str(mod["id"])) and mod.get("available"):
                desired.append(mod)
        return desired, managed

    def preview_sd_sync(self, drive_root: str, runtime_selection: dict[str, bool]) -> dict[str, Any]:
        desired, managed = self._selected_mod_dicts(runtime_selection or {})
        plan = plan_sd_sync(drive_root, desired, managed)
        return {"plan": plan}

    def start_sd_sync(self, drive_root: str, runtime_selection: dict[str, bool]) -> dict[str, Any]:
        self._refuse_in_practice("SD card sync")
        desired, managed = self._selected_mod_dicts(runtime_selection or {})
        plan = plan_sd_sync(drive_root, desired, managed)
        receipt = execute_sd_sync(drive_root, plan, data_dir=self.data_dir)
        return {
            "receipt": receipt,
            "drives": list_sd_drives(drive_provider=self.drive_provider),
            "receipts": self.service.list_receipts(),
        }

    def prepare_tools(self) -> dict[str, Any]:
        def _run(_cancel: threading.Event) -> None:
            status = prepare_tools(
                root=self.root,
                data_dir=self.data_dir,
                progress_cb=lambda msg: self._emit_event(
                    "progress", {"step": "tools", "status": "running", "message": msg}
                ),
                target_id=self._target_id(),
            )
            self._emit_event(
                "job_done",
                {"job": "prepare_tools", "toolchain": status, "catalog": self._catalog()},
            )

        return self._start_worker("prepare_tools", _run)

    def usb_driver_status(self) -> dict[str, Any]:
        """Read-only: present Sony USB devices, Senser-path blockers, helper readiness, pending restores."""
        if self.practice:
            return {"platform_supported": False, "devices": [], "our_packages": [], "needs_restore": [],
                    "practice": True}
        status = usb_driver.driver_status()
        packages = usb_driver.our_driver_packages() if status.get("platform_supported") else []
        records = [usb_driver.UsbDeviceRecord(**{k: d[k] for k in (
            "instance_id", "vid", "pid", "mi", "service", "description", "inf_path")}) for d in status["devices"]]
        status["our_packages"] = [p.__dict__ for p in packages]
        status["needs_restore"] = usb_driver.needs_restore(records, packages)
        return status

    def restore_windows_drivers(self) -> dict[str, Any]:
        """Remove every WinUSB package this app installed so Windows uses its stock drivers again."""
        self._refuse_in_practice("Changing USB drivers")

        def _run(_cancel: threading.Event) -> None:
            transport = getattr(self.service, "transport", None)
            restore = getattr(transport, "restore_windows_drivers", None)
            if not callable(restore):
                raise RuntimeError("This camera transport cannot change Windows drivers.")
            self._emit_event("progress", {"step": "usb_driver", "status": "running",
                                          "message": "Restoring Windows' own USB drivers (administrator approval)..."})
            result = restore()
            self._emit_event("job_done", {"job": "usb_driver_restore", "result": result,
                                          "usb_driver": self.usb_driver_status(),
                                          "receipts": self.service.list_receipts()})

        return self._start_worker("usb_driver_restore", _run)

    def check_for_update(self) -> dict[str, Any]:
        """Compare this build with the newest ``mod-manager-v*`` GitHub release (no auto-install)."""
        from src.a6400.mod_manager.updates import check_for_update

        if self.practice or load_settings(self.data_dir).get("update_check") is False:
            return {"disabled": True}
        return check_for_update()

    def open_release_page(self) -> dict[str, Any]:
        """Open the project's Releases page in the default browser (fixed URL only)."""
        from src.a6400.mod_manager.updates import RELEASES_PAGE

        webbrowser.open(RELEASES_PAGE)
        return {"opened": RELEASES_PAGE}

    def detect_and_prepare(self) -> dict[str, Any]:
        """Wizard step 1: detect the camera; if it is in MTP/PC Remote, ask it once to switch to MSC."""
        with self._lock:
            busy = self._job_active
        if busy:
            return {"camera": self._last_camera, "toolchain": None, "switched": None, "busy": True}
        toolchain = self._toolchain()
        camera = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
        switched = None
        if not camera.get("connected") and not getattr(self, "_msc_switch_tried", False):
            switch = getattr(getattr(self.service, "transport", None), "switch_to_msc", None)
            if callable(switch):
                self._msc_switch_tried = True  # one automatic attempt per app session
                try:
                    switched = switch()
                except Exception as exc:
                    switched = [{"sent": False, "error": f"{type(exc).__name__}: {exc}"}]
                if any(s.get("sent") for s in switched or []):
                    camera = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
        self._last_camera = camera
        status = self.usb_driver_status() if os.name == "nt" else None
        return {"camera": camera, "toolchain": toolchain, "switched": switched, "usb_driver": status}

    def get_ledger(self) -> dict[str, Any]:
        """Read-only: what this app has changed on the connected camera and can undo."""
        return self.service.get_ledger()

    def _start_restore(self, kind: str, last_group_only: bool) -> dict[str, Any]:
        def _run(_cancel: threading.Event) -> None:
            receipt = self.service.restore_stock(
                last_group_only=last_group_only,
                progress_cb=lambda ev: self._emit_event("progress", ev),
            )
            self._emit_event("job_done", {"job": kind, "result": receipt,
                                          "ledger": self.service.get_ledger(),
                                          "receipts": self.service.list_receipts()})

        return self._start_worker(kind, _run)

    def start_restore_stock(self) -> dict[str, Any]:
        """Undo every persistent change recorded for this camera (tweaks, debugger)."""
        return self._start_restore("restore_stock", False)

    def start_undo_last(self) -> dict[str, Any]:
        """Undo only the most recent install's persistent changes."""
        return self._start_restore("undo_last", True)

    def shutdown(self) -> None:
        """Release session resources; the elevated broker restores mass-storage drivers on exit."""
        close = getattr(getattr(self.service, "transport", None), "close", None)
        if callable(close):
            close()

    def install_usb_driver(self, pid: int) -> dict[str, Any]:
        """Bind WinUSB to one allowlisted Sony PID via the bundled libwdi helper (UAC prompt)."""
        self._refuse_in_practice("Changing USB drivers")
        target_pid = int(pid)

        def _run(_cancel: threading.Event) -> None:
            receipt = usb_driver.install_winusb(
                target_pid,
                data_dir=self.data_dir,
                progress_cb=lambda msg: self._emit_event(
                    "progress", {"step": "usb_driver", "status": "running", "message": msg}
                ),
            )
            self._emit_event(
                "job_done",
                {
                    "job": "usb_driver",
                    "result": receipt,
                    "usb_driver": usb_driver.driver_status(),
                    "receipts": self.service.list_receipts(),
                },
            )

        return self._start_worker("usb_driver", _run)

    def set_mlv2_folder(self, folder_path: str) -> dict[str, Any]:
        verified = validate_mlv2_build(Path(folder_path))
        key = "mlv2_dir_ilce_6400a" if verified.get("target_id") == "ilce_6400a" else "mlv2_dir"
        save_settings(self.data_dir, {key: str(Path(folder_path).resolve())})
        return {"toolchain": self._toolchain()}

    def pick_mlv2_folder(self) -> dict[str, Any]:
        chosen = self._pick_folder_dialog("Select Mod Loader v2 Build Directory")
        if not chosen:
            return {"cancelled": True}
        return {"cancelled": False, **self.set_mlv2_folder(chosen)}

    def run_debugger_check_only(self) -> dict[str, Any]:
        return self.service.check_debugger_compatibility()

    def collect_onboarding(self) -> dict[str, Any]:
        return self.service.collect_onboarding_bundle()

    def open_folder(self, which: str) -> dict[str, Any]:
        if which == "receipts":
            target = self.data_dir / "receipts"
            target.mkdir(parents=True, exist_ok=True)
        elif which == "custom_mods":
            target = custom_mods_dir(self.data_dir)
        elif which == "mlv2":
            mlv2_path = self._toolchain()["mlv2"].get("path")
            target = Path(mlv2_path) if mlv2_path else self.data_dir
        else:
            raise ValueError(f"Unknown folder key: {which}")

        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        return {"opened": str(target)}

    def _pick_file_dialog(self, title: str, file_types: tuple[str, ...]) -> str | None:
        if self._window is not None:
            import webview

            dialog_type = getattr(getattr(webview, "FileDialog", webview), "OPEN", getattr(webview, "OPEN_DIALOG", 10))
            res = self._window.create_file_dialog(
                dialog_type, allow_multiple=False, file_types=file_types
            )
            return str(res[0]) if res else None
        if os.name == "nt":
            from src.a6400.mod_manager import native_dialogs

            return native_dialogs.open_file(title, file_types)
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            chosen = filedialog.askopenfilename(title=title, filetypes=[("Mod files", "*.mod")])
            return chosen or None
        finally:
            root.destroy()

    def _pick_folder_dialog(self, title: str) -> str | None:
        if self._window is not None:
            import webview

            dialog_type = getattr(getattr(webview, "FileDialog", webview), "FOLDER", getattr(webview, "FOLDER_DIALOG", 20))
            res = self._window.create_file_dialog(dialog_type)
            return str(res[0]) if res else None
        if os.name == "nt":
            from src.a6400.mod_manager import native_dialogs

            return native_dialogs.pick_folder(title)
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            chosen = filedialog.askdirectory(title=title)
            return chosen or None
        finally:
            root.destroy()


API_DISPATCH_METHODS = (
    "get_initial_state",
    "set_target_profile",
    "scan_camera",
    "read_live_tweaks",
    "import_custom_mod_bytes",
    "import_custom_mod_dialog",
    "remove_custom_mod",
    "preview_install",
    "start_install",
    "cancel_active_job",
    "list_sd_drives",
    "preview_sd_sync",
    "start_sd_sync",
    "prepare_tools",
    "usb_driver_status",
    "install_usb_driver",
    "restore_windows_drivers",
    "detect_and_prepare",
    "check_for_update",
    "open_release_page",
    "get_ledger",
    "start_restore_stock",
    "start_undo_last",
    "set_mlv2_folder",
    "pick_mlv2_folder",
    "run_debugger_check_only",
    "collect_onboarding",
    "open_folder",
    "poll_events",
    "enter_practice_mode",
    "exit_practice_mode",
)


_STATIC_MIME = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def create_web_server(
    api: ModManagerApi,
    *,
    ui_dir: Path = UI_DIR,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Start an authenticated localhost HTTP server for ``--web`` mode and tests."""
    session_token = token or secrets.token_urlsafe(24)
    resolved_ui = Path(ui_dir).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path).path
            rel = "index.html" if parsed in ("", "/") else parsed.lstrip("/")
            candidate = (resolved_ui / rel).resolve()
            if not candidate.is_relative_to(resolved_ui) or not candidate.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = candidate.read_bytes()
            ctype = _STATIC_MIME.get(candidate.suffix.lower()) or mimetypes.guess_type(str(candidate))[0]
            if candidate.name == "index.html":
                html = data.decode("utf-8").replace(
                    "</head>",
                    f'  <meta name="mm-token" content="{session_token}">\n</head>',
                    1,
                )
                data = html.encode("utf-8")
                ctype = "text/html"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{ctype or 'application/octet-stream'}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            if self.headers.get("X-ModManager-Token") != session_token:
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Invalid session token"})
                return
            parsed = urlparse(self.path).path
            if not parsed.startswith("/api/"):
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown endpoint"})
                return
            method_name = parsed[len("/api/") :]
            if method_name not in API_DISPATCH_METHODS:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Unknown API method: {method_name}"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > SMOD_MAX_TOTAL_BYTES * 3:
                    self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "Payload too large"})
                    return
                raw = self.rfile.read(length) if length > 0 else b"{}"
                kwargs = json.loads(raw.decode("utf-8")) if raw.strip() else {}
                if not isinstance(kwargs, dict):
                    raise ValueError("JSON body must be an object")
                result = getattr(api, method_name)(**kwargs)
                self._send_json(HTTPStatus.OK, {"ok": True, "data": result})
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

    server = ThreadingHTTPServer((host, port), Handler)
    return server, session_token


def _open_edge_app_window(
    api: ModManagerApi,
    url: str,
    *,
    edge: Path,
    popen: Callable[..., Any] = subprocess.Popen,
) -> tuple[Any, Path]:
    """Start a chromeless Edge window on ``url`` with a throwaway profile under the data dir."""
    from src.a6400.mod_manager import webview_runtime

    profiles = Path(api.data_dir) / "edge-app"
    for stale in profiles.glob("run-*"):
        shutil.rmtree(stale, ignore_errors=True)  # left behind by a crash; never the user's Edge profile
    profile = profiles / f"run-{os.getpid()}"
    profile.mkdir(parents=True, exist_ok=True)
    proc = popen(webview_runtime.edge_app_command(edge, url, profile))
    return proc, profile


def launch_web_mode(
    api: ModManagerApi,
    *,
    port: int = 0,
    open_browser: bool = True,
    app_window: bool = False,
    popen: Callable[..., Any] = subprocess.Popen,
    early_exit_s: float = 3.0,
) -> str:
    """Serve the UI on localhost; returns the host used ("edge_app", "browser" or "none").

    With ``app_window`` the UI opens in a chromeless Microsoft Edge window (in-box on
    Windows 10/11) and the server stops when that window closes. Without Edge, or if Edge
    exits at once, the default browser is used and the server runs until interrupted.
    """
    from src.a6400.mod_manager import webview_runtime

    server, _token = create_web_server(api, port=port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"BIONZ X Mod Manager web UI listening at {url}")
    host = "none"
    profile: Path | None = None
    edge = webview_runtime.find_edge() if (open_browser and app_window) else None
    if edge is not None:
        proc, profile = _open_edge_app_window(api, url, edge=edge, popen=popen)
        try:
            proc.wait(timeout=early_exit_s)
            print("Edge app window exited immediately; opening the default browser instead.")
        except subprocess.TimeoutExpired:
            host = "edge_app"

            def _stop_when_closed() -> None:
                proc.wait()
                server.shutdown()

            threading.Thread(target=_stop_when_closed, name="edge-app-watch", daemon=True).start()
    if host == "none" and open_browser:
        host = "browser"
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        api.shutdown()
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)
    return host


class DesktopUnavailable(RuntimeError):
    """The WebView2 desktop window could not start; the caller falls back to ``--web``."""


def _log_desktop_failure(api: ModManagerApi, exc: BaseException) -> Path | None:
    import traceback

    try:
        log_dir = Path(api.data_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "desktop-launch.log"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"--- {datetime.now().isoformat(timespec='seconds')}\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        return path
    except OSError:
        return None


def launch_desktop(
    api: ModManagerApi,
    *,
    webview_module: Any = None,
    smoke_out: Path | None = None,
    smoke_timeout_s: float = 90.0,
) -> dict[str, Any]:
    """Open the Edge WebView2 window, preferring the bundled Fixed Version runtime.

    Raises ``DesktopUnavailable`` when no WebView2 runtime can host the UI or the window
    fails to start, so ``main`` can fall back to the localhost browser UI instead of
    exiting. With ``smoke_out`` the window closes itself after the page loads (CI check).
    """
    from src.a6400.mod_manager import webview_runtime

    if webview_module is None:
        try:
            import webview as webview_module
        except Exception as exc:
            raise DesktopUnavailable(f"pywebview could not be imported: {exc}") from exc
    if not webview_runtime.desktop_runtime_available():
        raise DesktopUnavailable("No WebView2 runtime is bundled or installed")
    try:
        prepared = webview_runtime.prepare_webview(webview_module, api.data_dir)
    except Exception as exc:
        raise DesktopUnavailable(str(exc)) from exc

    index_url = (UI_DIR / "index.html").resolve().as_uri()
    window = webview_module.create_window(
        "Sony BIONZ X Mod Manager",
        url=index_url,
        js_api=api,
        width=1140,
        height=780,
        min_size=(940, 640),
        background_color="#0B0D12",
    )
    api.attach_window(window)
    result: dict[str, Any] = {"loaded": False, "runtime": prepared["report"]}

    def _write_smoke() -> None:
        if smoke_out is not None:
            Path(smoke_out).write_text(json.dumps(result, indent=2), encoding="utf-8")

    def _on_loaded() -> None:
        _apply_windows_dark_titlebar()
        result["loaded"] = True
        if smoke_out is not None:
            _write_smoke()
            threading.Timer(1.0, window.destroy).start()

    window.events.loaded += _on_loaded
    if smoke_out is not None:
        def _smoke_timeout() -> None:
            if not result["loaded"]:
                result["error"] = f"page did not load within {smoke_timeout_s:.0f} s"
                _write_smoke()
                window.destroy()

        timer = threading.Timer(smoke_timeout_s, _smoke_timeout)
        timer.daemon = True
        timer.start()
    try:
        webview_module.start(**prepared["start_kwargs"])
    except Exception as exc:
        # Keep the API alive: main() reuses it for the browser fallback.
        result["error"] = f"{type(exc).__name__}: {exc}"
        _write_smoke()
        raise DesktopUnavailable(result["error"]) from exc
    if smoke_out is None:
        api.shutdown()
    return result


SELF_TEST_IMPORTS = (
    "usb",
    "libusb_package",
    "Cryptodome",
    "elftools",
    "yaml",
    "asn1crypto",
    "certifi",
    "webview",
    "pmca.commands.usb",
)


def run_self_test(*, require_frozen_assets: bool | None = None) -> dict[str, Any]:
    """Import runtime dependencies and resolve bundled assets without touching USB."""
    import importlib

    from src.a6400.mod_manager.tools_bundle import find_pmca_dir

    frozen = is_frozen() if require_frozen_assets is None else require_frozen_assets
    report: dict[str, Any] = {"frozen": is_frozen(), "imports": {}, "assets": {}, "ok": True}
    pmca_dir = find_pmca_dir()  # also puts a found checkout on sys.path; never downloads
    report["assets"]["pmca"] = str(pmca_dir) if pmca_dir else None
    for name in SELF_TEST_IMPORTS:
        try:
            importlib.import_module(name)
            report["imports"][name] = "ok"
        except Exception as exc:  # report every failure, not only the first
            report["imports"][name] = f"{type(exc).__name__}: {exc}"
            report["ok"] = False
    report["assets"]["ui_index"] = (UI_DIR / "index.html").is_file()
    if not report["assets"]["ui_index"]:
        report["ok"] = False
    from src.a6400.mod_manager import webview_runtime
    from src.a6400.mod_manager.tools_bundle import offline_only, verify_bundled_tools

    report["offline_only"] = offline_only()
    report["assets"]["webview2_runtime"] = webview_runtime.runtime_status()
    if frozen:
        helper = usb_driver.helper_status()
        report["assets"]["wdi_simple"] = helper
        if os.name == "nt" and not helper["verified"]:
            report["ok"] = False
        if pmca_dir is None:
            report["ok"] = False
        if os.name == "nt" and report["assets"]["webview2_runtime"]["host"] == "browser":
            # No WebView2 and no Edge: the UI still opens in the default browser, but say so.
            report["assets"]["webview2_runtime"]["warning"] = "no WebView2 runtime or Microsoft Edge found"
        try:
            # Hash-checks the debugger payloads, Mod Loader v2 and Arrivederci; never downloads.
            status = verify_bundled_tools()
            report["assets"]["bundled_tools"] = "verified"
            report["assets"]["mlv2"] = status["mlv2"].get("path")
        except Exception as exc:
            report["assets"]["bundled_tools"] = f"{type(exc).__name__}: {exc}"
            report["ok"] = False
    return report


def _parse_vid_pid(text: str) -> tuple[int, int]:
    vid_s, sep, pid_s = text.partition(":")
    if not sep:
        raise argparse.ArgumentTypeError("expected VID:PID in hex, for example 054C:0336")
    return int(vid_s, 16), int(pid_s, 16)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sony BIONZ X Mod Manager GUI")
    parser.add_argument("--web", action="store_true", help="Serve UI over localhost HTTP in default browser")
    parser.add_argument("--port", type=int, default=0, help="Port for --web mode (default: ephemeral)")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open browser in --web mode")
    parser.add_argument("--practice", action="store_true",
                        help="Start with a pretend camera to try the app safely (no USB, drivers, SD or network)")
    parser.add_argument("--self-test", action="store_true", help="Check bundled dependencies and exit (no USB access)")
    parser.add_argument("--self-test-out", type=Path, help="Also write the --self-test JSON report to this file")
    parser.add_argument("--smoke-ui", type=Path, metavar="RESULT.json",
                        help="Open the desktop window, write RESULT.json once the UI loads, then exit (CI check)")
    parser.add_argument(
        "--install-driver",
        type=_parse_vid_pid,
        action="append",
        metavar="VID:PID",
        help="Bind WinUSB to an allowlisted Sony service-mode VID:PID (UAC prompt) and exit",
    )
    parser.add_argument("--restore-all-drivers", action="store_true",
                        help="Remove every WinUSB package this app installed (run elevated) and exit")
    # Internal: elevated driver broker started by the GUI (see src/a6400/mod_manager/driver_broker.py).
    parser.add_argument("--driver-broker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pipe", help=argparse.SUPPRESS)
    parser.add_argument("--token", help=argparse.SUPPRESS)
    parser.add_argument("--data-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.driver_broker:
        from src.a6400.mod_manager.driver_broker import run_broker

        if not (args.pipe and args.token and args.data_dir):
            parser.error("--driver-broker needs --pipe, --token and --data-dir")
        return run_broker(args.pipe, args.token, args.data_dir)

    if args.restore_all_drivers:
        data_dir = args.data_dir or user_data_dir(None, root=repo_root(None))
        try:
            receipt = usb_driver.restore_windows_driver(None, data_dir=data_dir, progress_cb=print)
            print(f"restore: {receipt['status']} (receipt {receipt['receipt_path']})")
            return 0
        except Exception as exc:
            print(f"restore failed: {exc}")
            return 1

    if args.self_test:
        report = run_self_test()
        text = json.dumps(report, indent=2)
        print(text)  # windowed frozen builds have no stdout; --self-test-out is the CI channel
        if args.self_test_out:
            args.self_test_out.write_text(text, encoding="utf-8")
        return 0 if report["ok"] else 1

    if args.install_driver:
        data_dir = user_data_dir(None, root=repo_root(None))
        code = 0
        for vid, pid in args.install_driver:
            try:
                receipt = usb_driver.install_winusb(pid, vid=vid, data_dir=data_dir, progress_cb=print)
                print(f"{receipt['vid_pid']}: {receipt['status']} (receipt {receipt['receipt_path']})")
            except Exception as exc:
                print(f"{vid:04X}:{pid:04X}: {exc}")
                code = 1
        return code

    api = ModManagerApi(practice=args.practice)
    if args.smoke_ui:
        try:
            result = launch_desktop(api, smoke_out=args.smoke_ui)
        except DesktopUnavailable as exc:
            args.smoke_ui.write_text(json.dumps({"loaded": False, "error": str(exc)}), encoding="utf-8")
            return 1
        finally:
            api.shutdown()
        return 0 if result.get("loaded") else 1

    if args.web:
        launch_web_mode(api, port=args.port, open_browser=not args.no_open)
        return 0

    try:
        launch_desktop(api)
    except DesktopUnavailable as exc:
        log_path = _log_desktop_failure(api, exc)
        print(f"Desktop window unavailable ({exc}); opening the UI in an Edge app window. Log: {log_path}")
        launch_web_mode(api, port=args.port, open_browser=not args.no_open, app_window=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
