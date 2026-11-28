# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building the standalone Sony BIONZ X Mod Manager (Windows, macOS, Linux)."""
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(os.environ.get("BIONZX_REPO_ROOT", os.getcwd())).resolve()
PMCA_DIR = Path(os.environ["BIONZX_PMCA_DIR"]).resolve() if os.environ.get("BIONZX_PMCA_DIR") else None
MLV2_DIR = Path(os.environ["BIONZX_MLV2_DIR"]).resolve() if os.environ.get("BIONZX_MLV2_DIR") else None
ARRIVEDERCI_PATH = (
    Path(os.environ["BIONZX_ARRIVEDERCI_PATH"]).resolve()
    if os.environ.get("BIONZX_ARRIVEDERCI_PATH")
    else ROOT / "mods" / "arri_isp" / "arrivederci" / "Arrivederci.mod"
)
WDI_DIR = Path(os.environ["BIONZX_WDI_DIR"]).resolve() if os.environ.get("BIONZX_WDI_DIR") else None
VERSION_FILE = os.environ.get("BIONZX_VERSION_FILE") or None
INCLUDE_DBG = os.environ.get("BIONZX_INCLUDE_DEBUGGER_BIN", "0") == "1"
WEBVIEW2_DIR = Path(os.environ["BIONZX_WEBVIEW2_DIR"]).resolve() if os.environ.get("BIONZX_WEBVIEW2_DIR") else None
WEBVIEW2_VERSION_FILE = os.environ.get("BIONZX_WEBVIEW2_VERSION_FILE") or None
ONEFILE = os.environ.get("BIONZX_ONEFILE", "0") == "1"

datas = [
    (str(ROOT / "tools" / "mod_manager_ui"), "tools/mod_manager_ui"),
    (str(ROOT / "mods" / "menu_touch_full" / "MenuTouchFull.mod"), "bundled_mods"),
    (str(ARRIVEDERCI_PATH), "bundled_mods"),
]
if os.environ.get("BIONZX_APP_VERSION_FILE"):
    datas.append((os.environ["BIONZX_APP_VERSION_FILE"], "."))
for optional_mod in (
    ROOT / "mods" / "open_gate" / "OpenGateSensor.mod",
    ROOT / "mods" / "open_gate" / "OpenGate32.mod",
):
    if optional_mod.is_file():
        datas.append((str(optional_mod), "bundled_mods"))

a6400a_mods_dir = ROOT / "mods" / "ilce_6400a"
if a6400a_mods_dir.is_dir():
    for mod_file in sorted(a6400a_mods_dir.glob("*.mod")):
        datas.append((str(mod_file), "bundled_mods/ilce_6400a"))
binaries = []
hiddenimports = [
    "src.a6400.mod_manager",
    "src.a6400.targets.usb_debugger_compat",
    "src.a6400.winusb_transport",
    "src.a6400.usb_debug_ring",
    "tools.camera_debugger",
    "tools.deploy_native_usb_debugger",
    "tools.setup_usb_debugger",
    "src.a6400.mod_manager.usb_driver",
    "src.a6400.mod_manager.driver_broker",
    "src.a6400.mod_manager.ledger",
    "src.a6400.mod_manager.updates",
    "src.a6400.mod_manager.webview_runtime",
    "multiprocessing.connection",
    "tools.research.reload_mod",
    "tools.research.elf_metadata_identity",
]
if sys.platform == "win32":
    # Browser-hosted UI uses comdlg32/shell32 pickers; Tcl/Tk (~9 MB) is not shipped.
    hiddenimports.append("src.a6400.mod_manager.native_dialogs")
else:
    hiddenimports += ["tkinter", "tkinter.filedialog"]


def _not_test_or_gui(name: str) -> bool:
    """Drop self-test suites and unused GUIs that collect_submodules would pull in."""
    parts = name.split(".")
    return not any(p in {"SelfTest", "test", "tests", "ui"} for p in parts[1:])


for pkg in ("webview", "libusb_package", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

for pkg in ("usb", "Cryptodome", "elftools", "yaml", "asn1crypto", "axmlparserpy", "tlslite"):
    hiddenimports += collect_submodules(pkg, filter=_not_test_or_gui)

hiddenimports.append("config")
if sys.platform == "win32":
    hiddenimports += collect_submodules("comtypes", filter=_not_test_or_gui)
    hiddenimports += ["win32file", "win32api", "win32con", "pywintypes"]

pathex = [str(ROOT)]
if PMCA_DIR and (PMCA_DIR / "pmca").is_dir() and (PMCA_DIR / "config.py").is_file():
    pathex.append(str(PMCA_DIR))
    datas.append((str(PMCA_DIR / "config.py"), "."))
    datas.append((str(PMCA_DIR / "pmca"), "pmca"))
    if (PMCA_DIR / "certs").is_dir():
        datas.append((str(PMCA_DIR / "certs"), "certs"))
    hiddenimports += collect_submodules("pmca", filter=_not_test_or_gui)  # pmca.ui is PMCA's own Tk GUI

if MLV2_DIR and MLV2_DIR.is_dir():
    for fname in ("core.bin", "mlv2.req", "mlv2_guard_arm", "build.json", "shim.bin"):
        candidate = MLV2_DIR / fname
        if candidate.is_file():
            datas.append((str(candidate), "bundled_tools/mlv2"))

if WDI_DIR and (WDI_DIR / "wdi-simple.exe").is_file():
    # libwdi WinUSB helper; the app verifies wdi-simple.exe against manifest.json before use.
    for fname in ("wdi-simple.exe", "manifest.json", "LICENSE-libwdi.txt"):
        candidate = WDI_DIR / fname
        if candidate.is_file():
            datas.append((str(candidate), "bundled_tools/libwdi"))

if INCLUDE_DBG:
    for rel in (
        "devcrashday_a6400/a6400_usb_debugd.elf",
        "devcrashday_a6400/hook/usb_debug_launcher.so",
    ):
        candidate = ROOT / rel
        if not candidate.is_file():
            # The standalone build must never need a runtime download (build script verified hashes).
            raise SystemExit(f"USB debugger payload missing: {candidate}")
        datas.append((str(candidate), str(Path(rel).parent).replace("\\", "/")))

if WEBVIEW2_DIR:
    # Microsoft WebView2 Fixed Version runtime, copied as plain files (never analyzed or
    # rewritten). The app points pywebview at <_MEIPASS>/webview2_runtime.
    if not (WEBVIEW2_DIR / "msedgewebview2.exe").is_file():
        raise SystemExit(f"WebView2 runtime folder has no msedgewebview2.exe: {WEBVIEW2_DIR}")
    datas.append((str(WEBVIEW2_DIR), "webview2_runtime"))
    if WEBVIEW2_VERSION_FILE:
        datas.append((WEBVIEW2_VERSION_FILE, "webview2_runtime"))

EXCLUDES = [
    "pytest", "unicorn", "capstone", "PIL", "numpy",
    "Cryptodome.SelfTest", "comtypes.test", "lib2to3", "pydoc_data", "idlelib",
    # pywin32's MFC/Pythonwin GUI layer; only win32file/win32api/pywintypes are used.
    "win32ui", "win32uiole", "pythonwin", "dde",
]
if sys.platform == "win32":
    EXCLUDES += ["tkinter", "_tkinter"]

a = Analysis(
    [str(ROOT / "tools" / "mod_manager_gui.py")],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

# Native files the app never loads: WebView2Loader for other CPU architectures and
# IntelliSense XML docs shipped inside the pywebview/pythonnet wheels.
_DROP_PARTS = ("/runtimes/win-arm64/", "/runtimes/win-x86/")


def _keep(dest: str) -> bool:
    d = "/" + dest.replace("\\", "/")
    if any(part in d for part in _DROP_PARTS):
        return False
    if d.endswith(".xml") and ("/webview/" in d or "/pythonnet/" in d):
        return False
    return True


a.binaries = [entry for entry in a.binaries if _keep(entry[0])]
a.datas = [entry for entry in a.datas if _keep(entry[0])]
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="BionzX-Mod-Manager",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        version=VERSION_FILE,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="BionzX-Mod-Manager",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        version=VERSION_FILE,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="BionzX-Mod-Manager",
    )
