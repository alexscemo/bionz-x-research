"""Native Windows file and folder pickers through in-box comdlg32/shell32 (ctypes only).

Used by the browser-hosted UI (Edge app window or default browser), where pywebview's
dialogs are not available. This replaces tkinter on Windows, which saves the ~9 MB
Tcl/Tk runtime in the standalone package.
"""
from __future__ import annotations

import re
import sys

MAX_PATH_CHARS = 32768

OFN_NOCHANGEDIR = 0x00000008
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000

BIF_RETURNONLYFSDIRS = 0x00000001
BIF_EDITBOX = 0x00000010
BIF_NEWDIALOGSTYLE = 0x00000040

_PATTERN_RE = re.compile(r"^(?P<label>.*?)\s*\((?P<patterns>[^()]*)\)\s*$")


def filter_string(file_types: tuple[str, ...] | list[str]) -> str:
    """Turn pywebview-style ``"Mod files (*.mod)"`` entries into a comdlg32 filter string.

    The result is ``label NUL patterns NUL ... NUL`` and always ends with "All files".
    Entries without a ``(pattern)`` part are skipped.
    """
    parts: list[str] = []
    for entry in file_types:
        match = _PATTERN_RE.match(str(entry))
        if not match or not match.group("patterns").strip():
            continue
        patterns = ";".join(p.strip() for p in re.split(r"[;\s]+", match.group("patterns")) if p.strip())
        parts += [str(entry).strip(), patterns]
    parts += ["All files (*.*)", "*.*"]
    return "\0".join(parts) + "\0\0"


def _owner_window() -> int | None:
    import ctypes

    # The browser window the user just clicked in, so the dialog opens on top of it.
    return ctypes.windll.user32.GetForegroundWindow() or None


def open_file(title: str, file_types: tuple[str, ...] | list[str] = ()) -> str | None:
    """Show the Windows "Open" dialog; return the chosen path or None when cancelled."""
    if sys.platform != "win32":
        raise OSError("native_dialogs.open_file is Windows-only")
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    filters = ctypes.create_unicode_buffer(filter_string(file_types))
    path_buf = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = _owner_window()
    ofn.lpstrFilter = ctypes.cast(filters, wintypes.LPCWSTR)
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(path_buf, wintypes.LPWSTR)
    ofn.nMaxFile = MAX_PATH_CHARS
    ofn.lpstrTitle = title
    ofn.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR
    get_open = ctypes.windll.comdlg32.GetOpenFileNameW
    get_open.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    get_open.restype = wintypes.BOOL
    if not get_open(ctypes.byref(ofn)):
        return None  # cancelled (or CommDlgExtendedError, which we treat the same)
    return path_buf.value or None


def pick_folder(title: str) -> str | None:
    """Show the Windows folder picker; return the chosen folder or None when cancelled."""
    if sys.platform != "win32":
        raise OSError("native_dialogs.pick_folder is Windows-only")
    import ctypes
    from ctypes import wintypes

    class BROWSEINFOW(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    # BIF_NEWDIALOGSTYLE needs a single-threaded COM apartment on this (server) thread.
    hr = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    try:
        display = ctypes.create_unicode_buffer(260)
        info = BROWSEINFOW()
        info.hwndOwner = _owner_window()
        info.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
        info.lpszTitle = title
        info.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX
        browse = shell32.SHBrowseForFolderW
        browse.argtypes = [ctypes.POINTER(BROWSEINFOW)]
        browse.restype = ctypes.c_void_p
        pidl = browse(ctypes.byref(info))
        if not pidl:
            return None
        try:
            path_buf = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
            to_path = shell32.SHGetPathFromIDListW
            to_path.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
            to_path.restype = wintypes.BOOL
            if not to_path(pidl, path_buf):
                return None  # a virtual location (for example "This PC") with no file-system path
            return path_buf.value or None
        finally:
            free = ole32.CoTaskMemFree
            free.argtypes = [ctypes.c_void_p]
            free(pidl)
    finally:
        if hr in (0, 1):  # S_OK / S_FALSE: this call owns one CoInitialize reference
            ole32.CoUninitialize()
