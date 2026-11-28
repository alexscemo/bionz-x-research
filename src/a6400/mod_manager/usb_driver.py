"""Windows USB driver status and opt-in WinUSB binding for Sony camera Senser access.

PMCA's Senser path (``senserShellCommand(driverName="libusb")``) only talks to
devices that a libusb-1.0 backend can open. On Windows that means the camera's
mass-storage device (to send the service-mode switch) and the service-mode
device (``054C:0336`` / ``054C:02A9``) must be bound to WinUSB or libusbK.

This module:

* reads the bound driver service of every present ``054C:*`` USB device
  through SetupAPI (read-only);
* classifies which devices block the Senser path; and
* on explicit request, runs the bundled, hash-checked libwdi ``wdi-simple.exe``
  elevated (UAC) to bind WinUSB to one allowlisted VID:PID, then re-scans and
  writes a JSON receipt.

Nothing here runs implicitly: installation only happens through
``install_winusb`` which callers expose behind a user-confirmed button.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable

SONY_VID = 0x054C
#: Mirrors ``SONY_ID_PRODUCT_SENSER`` in Sony-PMCA-RE ``pmca/usb/sony.py``.
SENSER_PIDS: tuple[int, ...] = (0x02A9, 0x0336)
#: Driver services a libusb-1.0 backend can open on Windows.
LIBUSB_SERVICES = frozenset({"winusb", "libusbk"})
#: The Windows inbox mass-storage service; a camera in MSC mode is bound here by default.
MSC_SERVICE = "usbstor"

HELPER_DIR_PARTS = ("bundled_tools", "libwdi")
HELPER_EXE = "wdi-simple.exe"
HELPER_MANIFEST = "manifest.json"
DEFAULT_INSTALL_TIMEOUT_S = 180.0
DEFAULT_REBIND_WAIT_S = 20.0

_HWID_RE = re.compile(r"USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", re.IGNORECASE)
# Interface nodes may carry REV before MI: USB\VID_x&PID_y&REV_z&MI_00.
_MI_RE = re.compile(r"&MI_([0-9A-F]{2})", re.IGNORECASE)


@dataclass(frozen=True)
class UsbDeviceRecord:
    """One present USB device node as reported by SetupAPI."""

    instance_id: str
    vid: int
    pid: int
    mi: int | None
    service: str
    description: str = ""
    #: Published driver package (``oemNN.inf``) bound to the node, when Windows reports one.
    inf_path: str = ""

    @property
    def vid_pid(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"


class DriverMissing(RuntimeError):
    """A Sony device on the Senser path is present but not bound to a libusb-capable driver."""

    def __init__(self, devices: list[dict[str, Any]], detail: str = ""):
        self.devices = devices
        names = ", ".join(f"{d['vid_pid']} ({d['service'] or 'no driver'})" for d in devices)
        message = (
            f"Camera USB driver required: {names} must use WinUSB for service mode. "
            "Use 'Install camera USB driver' in the Mod Manager, then retry."
        )
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": "driver_missing", "devices": self.devices, "message": str(self)}


def parse_hardware_id(hardware_id: str) -> tuple[int, int, int | None] | None:
    """Return ``(vid, pid, mi)`` from a ``USB\\VID_xxxx&PID_xxxx[&MI_xx]`` id, else ``None``."""
    match = _HWID_RE.search(hardware_id or "")
    if not match:
        return None
    vid, pid = match.groups()
    mi = _MI_RE.search(hardware_id, match.end())
    return int(vid, 16), int(pid, 16), (int(mi.group(1), 16) if mi else None)


# ---------------------------------------------------------------------------
# SetupAPI scanning (read-only)
# ---------------------------------------------------------------------------

def _setupapi_records() -> list[UsbDeviceRecord]:
    """Enumerate present devices under the ``USB`` enumerator via SetupAPI."""
    import ctypes
    from ctypes import wintypes

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    DIGCF_PRESENT = 0x2
    DIGCF_ALLCLASSES = 0x4
    SPDRP_DEVICEDESC = 0x0
    SPDRP_HARDWAREID = 0x1
    SPDRP_SERVICE = 0x4
    SPDRP_DRIVER = 0x9
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID), ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_size_t)]

    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

    def _prop(handle: Any, info: SP_DEVINFO_DATA, prop: int) -> list[str]:
        buf = ctypes.create_unicode_buffer(2048)
        reg_type = wintypes.DWORD(0)
        needed = wintypes.DWORD(0)
        ok = setupapi.SetupDiGetDeviceRegistryPropertyW(
            handle, ctypes.byref(info), prop, ctypes.byref(reg_type), buf, ctypes.sizeof(buf), ctypes.byref(needed)
        )
        if not ok:
            return []
        raw = ctypes.wstring_at(ctypes.addressof(buf), ctypes.sizeof(buf) // ctypes.sizeof(ctypes.c_wchar))
        return [part for part in raw.split("\x00") if part] if reg_type.value == 7 else [raw.split("\x00", 1)[0]]

    handle = setupapi.SetupDiGetClassDevsW(None, "USB", None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if not handle or handle == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "SetupDiGetClassDevsW failed")
    records: list[UsbDeviceRecord] = []
    try:
        index = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(info)):
                break
            index += 1
            parsed = None
            for hwid in _prop(handle, info, SPDRP_HARDWAREID):
                parsed = parse_hardware_id(hwid)
                if parsed:
                    break
            if not parsed:
                continue
            id_buf = ctypes.create_unicode_buffer(512)
            setupapi.SetupDiGetDeviceInstanceIdW(handle, ctypes.byref(info), id_buf, 512, None)
            service = (_prop(handle, info, SPDRP_SERVICE) or [""])[0]
            desc = (_prop(handle, info, SPDRP_DEVICEDESC) or [""])[0]
            driver_key = (_prop(handle, info, SPDRP_DRIVER) or [""])[0]
            records.append(
                UsbDeviceRecord(
                    id_buf.value, parsed[0], parsed[1], parsed[2], service, desc, _driver_inf_path(driver_key)
                )
            )
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(handle)
    return records


def _driver_inf_path(driver_key: str) -> str:
    """Return the ``InfPath`` (e.g. ``oem42.inf``) of a ``{class-guid}\\NNNN`` driver key."""
    if not driver_key:
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Control\\Class\\{driver_key}"
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "InfPath")
            return str(value)
    except OSError:
        return ""


def scan_sony_usb(provider: Callable[[], Iterable[UsbDeviceRecord]] | None = None) -> list[UsbDeviceRecord]:
    """Return present Sony (``054C``) USB device nodes; empty on non-Windows hosts."""
    if provider is None:
        if os.name != "nt":
            return []
        provider = _setupapi_records
    return [rec for rec in provider() if rec.vid == SONY_VID]


def classify_device(rec: UsbDeviceRecord) -> dict[str, Any]:
    """Describe one Sony device's role on the Senser path and whether it needs WinUSB."""
    service = (rec.service or "").lower()
    if rec.pid in SENSER_PIDS:
        role = "service_mode"
    elif service == MSC_SERVICE:
        role = "mass_storage"
    elif service in LIBUSB_SERVICES:
        role = "libusb_bound"
    else:
        role = "other"
    libusb_ok = service in LIBUSB_SERVICES
    needs_driver = role in ("service_mode", "mass_storage") and not libusb_ok
    return {
        **asdict(rec),
        "vid_pid": rec.vid_pid,
        "role": role,
        "libusb_ok": libusb_ok,
        "needs_driver": needs_driver,
        "eligible": role in ("service_mode", "mass_storage") and rec.mi is None,
    }


def blocking_devices(records: Iterable[UsbDeviceRecord]) -> list[dict[str, Any]]:
    """Return classified devices that block the PMCA Senser path."""
    return [c for c in (classify_device(r) for r in records) if c["needs_driver"] and c["eligible"]]


def is_installable_pid(pid: int, records: Iterable[UsbDeviceRecord]) -> bool:
    """Allowlist: PMCA Senser PIDs, or a present Sony device currently bound to USBSTOR."""
    if pid in SENSER_PIDS:
        return True
    return any(r.pid == pid and r.mi is None and (r.service or "").lower() == MSC_SERVICE for r in records)


# ---------------------------------------------------------------------------
# Bundled libwdi helper
# ---------------------------------------------------------------------------

def helper_dir(base: Path | None = None) -> Path:
    if base is None:
        from .tools_bundle import bundle_root

        base = bundle_root()
    return Path(base).joinpath(*HELPER_DIR_PARTS)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def helper_status(base: Path | None = None) -> dict[str, Any]:
    """Report whether the bundled ``wdi-simple.exe`` is present and matches its manifest hash."""
    directory = helper_dir(base)
    exe = directory / HELPER_EXE
    manifest_path = directory / HELPER_MANIFEST
    status: dict[str, Any] = {"path": str(exe), "present": exe.is_file(), "verified": False, "detail": ""}
    if not exe.is_file():
        status["detail"] = "wdi-simple.exe is not bundled with this build"
        return status
    if not manifest_path.is_file():
        status["detail"] = "libwdi manifest.json is missing; refusing to run an unverified helper"
        return status
    try:
        # utf-8-sig: Windows PowerShell 5.1 writes a BOM with Out-File -Encoding utf8.
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        status["detail"] = f"libwdi manifest.json unreadable: {exc}"
        return status
    expected = str(manifest.get("sha256", "")).lower()
    actual = _sha256_file(exe)
    status.update(
        {
            "sha256": actual,
            "expected_sha256": expected,
            "libwdi_commit": manifest.get("libwdi_commit"),
            "verified": bool(expected) and actual == expected,
        }
    )
    if not status["verified"]:
        status["detail"] = "wdi-simple.exe SHA-256 does not match the bundled manifest"
    return status


def build_wdi_args(vid: int, pid: int, dest: Path) -> list[str]:
    tag = f"{vid:04x}_{pid:04x}"
    return [
        "--vid", f"0x{vid:04X}",
        "--pid", f"0x{pid:04X}",
        "--type", "0",
        "--name", f"Sony Camera {vid:04X}:{pid:04X} (BIONZ X WinUSB)",
        "--inf", f"bionzx_{tag}.inf",
        "--dest", str(dest),
        "--timeout", "120000",
        "--silent",
    ]


def is_elevated() -> bool:
    """True when this process already has administrator rights (for example inside Setup)."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_elevated(exe: Path, args: list[str], timeout_s: float) -> int:
    """Run ``exe`` with administrator rights and return its exit code.

    Already elevated (Setup, the driver broker): run it directly, so no second UAC prompt.
    Otherwise ask through a UAC prompt.
    """
    if is_elevated():
        return run_direct(exe, args, timeout_s)
    code = shell_execute_elevated(exe, args, cwd=Path(exe).parent, wait=True, timeout_s=timeout_s)
    assert code is not None
    return code


def run_direct(exe: Path, args: list[str], timeout_s: float) -> int:
    """Run ``exe`` in the current (already elevated) process context; used by the driver broker."""
    import subprocess

    proc = subprocess.run([str(exe), *args], cwd=str(Path(exe).parent), timeout=timeout_s, check=False)
    return int(__import__("ctypes").c_int32(proc.returncode).value)


def shell_execute_elevated(
    exe: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    wait: bool = True,
    timeout_s: float = DEFAULT_INSTALL_TIMEOUT_S,
) -> int | None:
    """Start ``exe`` through a UAC prompt; return its exit code when ``wait``, else ``None``."""
    import ctypes
    import subprocess
    from ctypes import wintypes

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NO_CONSOLE = 0x00008000
    SW_HIDE = 0

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
    info.lpVerb = "runas"
    info.lpFile = str(exe)
    info.lpParameters = subprocess.list2cmdline(args)
    info.lpDirectory = str(cwd or Path(exe).parent)
    info.nShow = SW_HIDE
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == 1223:  # ERROR_CANCELLED
            raise PermissionError("Administrator approval was declined; no driver was changed.")
        raise OSError(err, f"ShellExecuteExW failed to start {Path(exe).name}")
    if not wait:
        kernel32.CloseHandle(info.hProcess)
        return None
    try:
        WAIT_TIMEOUT = 0x102
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout_s * 1000)) == WAIT_TIMEOUT:
            raise TimeoutError(f"{Path(exe).name} did not finish within {timeout_s:.0f}s")
        code = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return ctypes.c_int32(code.value).value
    finally:
        kernel32.CloseHandle(info.hProcess)


def _write_receipt(data_dir: Path, payload: dict[str, Any]) -> Path:
    receipts = Path(data_dir) / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = receipts / f"usb-driver-{stamp}.json"
    counter = 1
    while candidate.exists():
        candidate = receipts / f"usb-driver-{stamp}-{counter}.json"
        counter += 1
    candidate.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return candidate


def install_winusb(
    pid: int,
    *,
    data_dir: Path,
    vid: int = SONY_VID,
    helper_base: Path | None = None,
    scanner: Callable[[], list[UsbDeviceRecord]] | None = None,
    runner: Callable[[Path, list[str], float], int] | None = None,
    progress_cb: Callable[[str], None] | None = None,
    timeout_s: float = DEFAULT_INSTALL_TIMEOUT_S,
    rebind_wait_s: float = DEFAULT_REBIND_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Bind WinUSB to one allowlisted Sony VID:PID using the bundled libwdi helper (UAC prompt)."""
    say = progress_cb or (lambda _msg: None)
    scan = scanner or scan_sony_usb
    if os.name != "nt" and runner is None:
        raise RuntimeError("WinUSB driver installation is only needed (and supported) on Windows.")
    if vid != SONY_VID:
        raise ValueError(f"Refusing to install a driver for non-Sony VID {vid:04X}")
    if not 0 < int(pid) <= 0xFFFF:
        raise ValueError(f"Invalid USB product id {pid!r}")

    before = scan()
    if not is_installable_pid(pid, before):
        raise ValueError(
            f"{vid:04X}:{pid:04X} is not an allowlisted service-mode PID and is not a present "
            "Sony mass-storage device; refusing to change its driver."
        )

    helper = helper_status(helper_base)
    if not helper["verified"]:
        raise RuntimeError(f"Bundled driver helper unavailable: {helper['detail']}")

    dest = Path(data_dir) / "usb_driver" / f"{vid:04x}_{pid:04x}"
    dest.mkdir(parents=True, exist_ok=True)
    args = build_wdi_args(vid, pid, dest)
    say(f"Requesting administrator approval to bind WinUSB to {vid:04X}:{pid:04X}...")
    exit_code = (runner or _run_elevated)(Path(helper["path"]), args, timeout_s)
    say(f"wdi-simple.exe exited with code {exit_code}")

    after = scan()
    target_present = any(r.pid == pid for r in before)
    deadline = time.monotonic() + (rebind_wait_s if target_present and exit_code == 0 else 0)
    while target_present and exit_code == 0 and time.monotonic() < deadline:
        if any(r.pid == pid and (r.service or "").lower() in LIBUSB_SERVICES for r in after):
            break
        sleep(1.0)
        after = scan()

    bound = [r for r in after if r.pid == pid and (r.service or "").lower() in LIBUSB_SERVICES]
    if exit_code != 0:
        status = "failed"
    elif not target_present:
        status = "preinstalled"
    elif bound:
        status = "bound"
    else:
        status = "installed_not_rebound"
    receipt = {
        "kind": "usb_driver_install",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "vid_pid": f"{vid:04X}:{pid:04X}",
        "status": status,
        "exit_code": exit_code,
        "helper": {k: helper.get(k) for k in ("path", "sha256", "libwdi_commit")},
        "args": args,
        "before": [classify_device(r) for r in before if r.pid == pid],
        "after": [classify_device(r) for r in after if r.pid == pid],
    }
    receipt["receipt_path"] = str(_write_receipt(Path(data_dir), receipt))
    if status == "failed":
        raise RuntimeError(f"WinUSB install for {vid:04X}:{pid:04X} failed (wdi-simple exit {exit_code}).")
    return receipt


def driver_status(
    *,
    scanner: Callable[[], list[UsbDeviceRecord]] | None = None,
    helper_base: Path | None = None,
) -> dict[str, Any]:
    """Read-only summary for the GUI: present Sony devices, blockers, and helper readiness."""
    supported = os.name == "nt" or scanner is not None
    records = (scanner or scan_sony_usb)() if supported else []
    devices = [classify_device(r) for r in records]
    return {
        "platform_supported": supported,
        "devices": devices,
        "blocking": [d for d in devices if d["needs_driver"] and d["eligible"]],
        "senser_pids": [f"{SONY_VID:04X}:{p:04X}" for p in SENSER_PIDS],
        "helper": helper_status(helper_base) if supported else {"present": False, "verified": False, "detail": "not Windows"},
    }


# ---------------------------------------------------------------------------
# Reversal: remove driver packages this app installed (requires elevation)
# ---------------------------------------------------------------------------

#: ``--inf`` names this app passes to wdi-simple (see ``build_wdi_args``).
OUR_INF_PREFIX = "bionzx_"
_OUR_INF_RE = re.compile(r"^bionzx_([0-9a-f]{4})_([0-9a-f]{4})\.inf$", re.IGNORECASE)


@dataclass(frozen=True)
class DriverPackage:
    """A published driver package (``oemNN.inf``) that this app created."""

    published_name: str
    original_name: str
    vid: int
    pid: int


def _windows_dir() -> Path:
    return Path(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows")


def our_driver_packages(windows_dir: Path | None = None) -> list[DriverPackage]:
    """Find published ``oemNN.inf`` files whose bytes match a ``bionzx_*.inf`` in the driver store.

    Ownership is proven by content: Windows copies the original INF into
    ``System32/DriverStore/FileRepository/<original>.inf_<arch>_<hash>/`` and publishes an
    identical ``INF/oemNN.inf``. Packages that do not match byte-for-byte are never touched.
    """
    win = Path(windows_dir) if windows_dir is not None else _windows_dir()
    repo = win / "System32" / "DriverStore" / "FileRepository"
    originals: dict[str, tuple[str, int, int]] = {}
    if repo.is_dir():
        for folder in repo.glob(f"{OUR_INF_PREFIX}*.inf_*"):
            for inf in folder.glob(f"{OUR_INF_PREFIX}*.inf"):
                match = _OUR_INF_RE.match(inf.name)
                if match:
                    originals[_sha256_file(inf)] = (inf.name, int(match.group(1), 16), int(match.group(2), 16))
    packages: list[DriverPackage] = []
    if originals:
        for published in sorted((win / "INF").glob("oem*.inf")):
            try:
                digest = _sha256_file(published)
            except OSError:
                continue
            if digest in originals:
                name, vid, pid = originals[digest]
                packages.append(DriverPackage(published.name, name, vid, pid))
    return packages


def needs_restore(records: Iterable[UsbDeviceRecord], packages: Iterable[DriverPackage]) -> list[dict[str, Any]]:
    """Present Sony mass-storage PIDs still bound to one of our WinUSB packages."""
    ours = {p.published_name.lower() for p in packages}
    out = []
    for rec in records:
        if rec.pid in SENSER_PIDS or rec.mi is not None:
            continue
        if (rec.service or "").lower() in LIBUSB_SERVICES and rec.inf_path.lower() in ours:
            out.append(classify_device(rec))
    return out


def _run_pnputil(args: list[str]) -> int:
    import subprocess

    exe = _windows_dir() / "System32" / "pnputil.exe"
    proc = subprocess.run([str(exe), *args], capture_output=True, text=True, check=False)
    return proc.returncode


def restore_windows_driver(
    pid: int | None,
    *,
    data_dir: Path,
    vid: int = SONY_VID,
    packages_provider: Callable[[], list[DriverPackage]] | None = None,
    pnputil: Callable[[list[str]], int] | None = None,
    scanner: Callable[[], list[UsbDeviceRecord]] | None = None,
    progress_cb: Callable[[str], None] | None = None,
    rebind_wait_s: float = DEFAULT_REBIND_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Delete our WinUSB package(s) for ``pid`` (or all, when ``pid`` is None) so Windows rebinds stock drivers.

    Must run elevated. ``pnputil /delete-driver <oem> /uninstall /force`` removes the package
    and reinstalls the best remaining driver (``USBSTOR`` for a camera in mass-storage mode).
    """
    say = progress_cb or (lambda _msg: None)
    run = pnputil or _run_pnputil
    scan = scanner or scan_sony_usb
    packages = [p for p in (packages_provider or our_driver_packages)() if p.vid == vid and (pid is None or p.pid == pid)]
    before = scan()
    results = []
    for pkg in packages:
        say(f"Removing WinUSB package {pkg.published_name} ({pkg.original_name})...")
        code = run(["/delete-driver", pkg.published_name, "/uninstall", "/force"])
        results.append({"package": asdict(pkg), "exit_code": code})
    if packages:
        run(["/scan-devices"])  # best effort; older Windows 10 builds lack it
    after = scan()
    deadline = time.monotonic() + (rebind_wait_s if packages else 0)
    def _still_ours(records: list[UsbDeviceRecord]) -> bool:
        removed = {p.published_name.lower() for p in packages}
        return any(r.inf_path.lower() in removed for r in records)
    while _still_ours(after) and time.monotonic() < deadline:
        sleep(1.0)
        after = scan()
    failed = [r for r in results if r["exit_code"] != 0]
    status = "nothing_to_restore" if not packages else ("failed" if failed or _still_ours(after) else "restored")
    receipt = {
        "kind": "usb_driver_restore",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "target": f"{vid:04X}:{pid:04X}" if pid is not None else "all",
        "status": status,
        "packages": results,
        "before": [classify_device(r) for r in before],
        "after": [classify_device(r) for r in after],
    }
    receipt["receipt_path"] = str(_write_receipt(Path(data_dir), receipt))
    if status == "failed":
        raise RuntimeError(f"Could not restore the Windows driver ({receipt['target']}); see {receipt['receipt_path']}")
    return receipt
