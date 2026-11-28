"""How the standalone Mod Manager hosts its HTML UI without installing anything.

The release package stays small by using what Windows already ships, in this order:

1. ``webview2_bundled``: an optional Microsoft WebView2 Fixed Version runtime in
   ``<app>/webview2_runtime`` (opt-in build, about 950 MB unpacked; off by default).
2. ``webview2_system``: the Evergreen WebView2 runtime that Windows 11 includes and
   Windows Update / Microsoft Edge install on Windows 10.
3. ``edge_app``: a chromeless Microsoft Edge ``--app`` window (Edge is part of
   Windows 10 and 11) talking to the app's localhost UI server.
4. ``browser``: the default browser, as a last resort.

For (1), pywebview (6.2+) is pointed at the folder through
``webview.settings['WEBVIEW2_RUNTIME_PATH']``, which also makes its WinForms backend
skip the Evergreen registry probe.

On Windows 10 the Fixed Version renderer runs in an AppContainer sandbox and needs
read/execute access for the ``ALL APPLICATION PACKAGES`` (``S-1-15-2-1``) and
``ALL RESTRICTED APPLICATION PACKAGES`` (``S-1-15-2-2``) SIDs on the runtime folder.
Setup grants them; for the portable zip the app grants them itself with the in-box
``icacls`` when the folder belongs to the user.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

RUNTIME_DIR_NAME = "webview2_runtime"
RUNTIME_EXE = "msedgewebview2.exe"
APP_CONTAINER_SIDS = ("*S-1-15-2-1", "*S-1-15-2-2")
ACL_MARKER = ".bionzx-acl-ok"
_CREATE_NO_WINDOW = 0x08000000

Runner = Callable[[list[str]], int]


def bundled_runtime_dir(base: Path | None = None) -> Path | None:
    """Return the bundled runtime folder when it holds ``msedgewebview2.exe``, else None."""
    if base is None:
        from .tools_bundle import bundle_root

        base = bundle_root()
    candidate = Path(base) / RUNTIME_DIR_NAME
    if (candidate / RUNTIME_EXE).is_file():
        return candidate.resolve()
    return None


def runtime_version(runtime_dir: Path | None) -> str | None:
    """Read the runtime version the release build recorded next to the runtime files."""
    if runtime_dir is None:
        return None
    version_file = Path(runtime_dir) / "bionzx_runtime_version.txt"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip() or None
    return None


def _default_runner(args: list[str]) -> int:
    flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(args, capture_output=True, check=False, creationflags=flags, timeout=120).returncode


def ensure_app_container_acl(runtime_dir: Path, *, runner: Runner | None = None) -> dict[str, Any]:
    """Grant the AppContainer SIDs read/execute on the runtime folder once (portable zip case).

    Only folders the current user can write to are touched (a Program Files install is
    already handled by Setup). A marker file records success so later launches skip it.
    """
    runtime_dir = Path(runtime_dir)
    marker = runtime_dir / ACL_MARKER
    if marker.is_file():
        return {"status": "already_granted"}
    if sys.platform != "win32" and runner is None:
        return {"status": "not_windows"}
    if not os.access(runtime_dir, os.W_OK):
        return {"status": "read_only_folder"}
    cmd = ["icacls", str(runtime_dir)]
    for sid in APP_CONTAINER_SIDS:
        cmd += ["/grant", f"{sid}:(OI)(CI)(RX)"]
    cmd += ["/T", "/C", "/Q"]
    code = (runner or _default_runner)(cmd)
    if code != 0:
        return {"status": "failed", "exit_code": code}
    try:
        marker.write_text("granted\n", encoding="utf-8")
    except OSError:
        pass
    return {"status": "granted"}


def prepare_webview(
    webview_module: Any,
    data_dir: Path,
    *,
    base: Path | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Point pywebview at the bundled runtime and a writable user-data folder.

    Returns the ``webview.start`` keyword arguments plus a report. Must run before
    ``webview.start`` because pywebview's WinForms backend checks the runtime on import.
    """
    storage = Path(data_dir) / "webview2"
    storage.mkdir(parents=True, exist_ok=True)
    runtime_dir = bundled_runtime_dir(base)
    report: dict[str, Any] = {
        "bundled_runtime": str(runtime_dir) if runtime_dir else None,
        "storage_path": str(storage),
    }
    if runtime_dir is not None:
        settings = getattr(webview_module, "settings", None)
        if not isinstance(settings, dict) or "WEBVIEW2_RUNTIME_PATH" not in settings:
            raise RuntimeError(
                "This pywebview version cannot use a bundled WebView2 runtime (needs pywebview >= 6.2)."
            )
        settings["WEBVIEW2_RUNTIME_PATH"] = str(runtime_dir)
        report["acl"] = ensure_app_container_acl(runtime_dir, runner=runner)
    return {"start_kwargs": {"gui": "edgechromium", "debug": False, "storage_path": str(storage)}, "report": report}


EVERGREEN_CLIENT_KEY = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def evergreen_runtime_installed() -> bool:
    """Return True when the system-wide Evergreen WebView2 runtime is registered."""
    if sys.platform != "win32":
        return False
    import winreg

    probes = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{EVERGREEN_CLIENT_KEY}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{EVERGREEN_CLIENT_KEY}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{EVERGREEN_CLIENT_KEY}"),
    )
    for hive, path in probes:
        try:
            with winreg.OpenKey(hive, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if version and str(version) != "0.0.0.0":
            return True
    return False


def desktop_runtime_available(base: Path | None = None) -> bool:
    """True when either the bundled runtime or the Evergreen runtime can host the window.

    Without one, pywebview would silently fall back to the legacy MSHTML engine, which
    cannot run the UI, so the caller should switch to the browser fallback instead.
    """
    return bundled_runtime_dir(base) is not None or evergreen_runtime_installed()


EDGE_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"


def find_edge(env: dict[str, str] | None = None, *, use_registry: bool = True) -> Path | None:
    """Locate the in-box Microsoft Edge (``msedge.exe``); None when it is not present."""
    if env is None:
        if sys.platform != "win32":
            return None
        env = dict(os.environ)
    if use_registry and sys.platform == "win32":
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, EDGE_APP_PATHS_KEY) as key:
                    value, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            candidate = Path(str(value).strip('"'))
            if candidate.is_file():
                return candidate
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = env.get(var)
        if root:
            candidate = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return candidate
    return None


def edge_app_command(edge: Path, url: str, profile_dir: Path, *, width: int = 1140, height: int = 780) -> list[str]:
    """Edge command line for a chromeless app window with its own throwaway profile.

    A dedicated ``--user-data-dir`` keeps the window in its own browser process, so the
    caller can stop the UI server when the user closes it, and never touches the user's
    Edge profile, history or sign-in.
    """
    return [
        str(edge),
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        f"--window-size={width},{height}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def desktop_host(base: Path | None = None) -> str:
    """Which UI host this PC will get: see the module docstring for the order."""
    if bundled_runtime_dir(base) is not None:
        return "webview2_bundled"
    if evergreen_runtime_installed():
        return "webview2_system"
    if find_edge() is not None:
        return "edge_app"
    return "browser"


def runtime_status(base: Path | None = None) -> dict[str, Any]:
    """Self-test summary of the UI host (no window is created)."""
    runtime_dir = bundled_runtime_dir(base)
    edge = find_edge()
    return {
        "bundled": runtime_dir is not None,
        "path": str(runtime_dir) if runtime_dir else None,
        "version": runtime_version(runtime_dir),
        "system_webview2": evergreen_runtime_installed(),
        "edge": str(edge) if edge else None,
        "host": desktop_host(base),
    }
