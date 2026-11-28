"""Opt-out update check against GitHub Releases (``mod-manager-vX.Y.Z`` tags). Never auto-installs."""
from __future__ import annotations

import json
import re
from typing import Any, Callable
import urllib.request

from .tools_bundle import bundle_root

RELEASES_API = "https://api.github.com/repos/ivica00/bionz-x-research/releases?per_page=30"
RELEASES_PAGE = "https://github.com/ivica00/bionz-x-research/releases"
TAG_RE = re.compile(r"^mod-manager-v(\d+)\.(\d+)\.(\d+)$")
VERSION_FILE = "app_version.txt"


def app_version() -> str:
    """Version baked in by the packager (``app_version.txt`` in the bundle), else ``"dev"``."""
    path = bundle_root() / VERSION_FILE
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"
    return text if re.fullmatch(r"\d+\.\d+\.\d+", text) else "dev"


def _parse(version: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version or "")
    return tuple(int(x) for x in match.groups()) if match else None  # type: ignore[return-value]


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "BionzX-Mod-Manager"})
    with urllib.request.urlopen(req, timeout=3) as resp:  # short: offline PCs are normal
        return resp.read()


def check_for_update(current: str | None = None, fetcher: Callable[[str], bytes] | None = None) -> dict[str, Any]:
    """Return the newest published Mod Manager release and whether it is newer than ``current``."""
    current = current or app_version()
    result: dict[str, Any] = {"current": current, "latest": None, "update_available": False, "url": RELEASES_PAGE}
    try:
        releases = json.loads((fetcher or _fetch)(RELEASES_API).decode("utf-8"))
    except Exception as exc:  # offline is normal; report, never fail the app
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    best: tuple[tuple[int, int, int], dict[str, Any]] | None = None
    for rel in releases if isinstance(releases, list) else []:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        match = TAG_RE.match(str(rel.get("tag_name", "")))
        if not match:
            continue
        ver = tuple(int(x) for x in match.groups())
        if best is None or ver > best[0]:
            best = (ver, rel)  # type: ignore[assignment]
    if best is not None:
        ver, rel = best
        result["latest"] = ".".join(map(str, ver))
        result["url"] = str(rel.get("html_url") or RELEASES_PAGE)
        cur = _parse(current)
        result["update_available"] = cur is not None and ver > cur
    return result
