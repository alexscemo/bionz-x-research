"""ILCE-6400 open-gate test kit for the OpenGateA1 mod (mods/opengate_a1).

Commands (run from the repository root):

    python -m tools.kits.opengate_a6400 check     # read-only: VERDICT READY / NOT READY
    python -m tools.kits.opengate_a6400 apply     # needs a fresh READY check; stages the .mod in /tmp_bt/mods
    python -m tools.kits.opengate_a6400 verify    # read-only: reads the mod's log and the live sites
    python -m tools.kits.opengate_a6400 putback   # after you switch the mod OFF: confirms stock, removes the staged .mod
    python -m tools.kits.opengate_a6400 export    # read-only: zips this kit's receipts and a log snapshot
    python -m tools.kits.opengate_a6400 selftest  # no camera: runs the whole flow against a simulated camera

What the kit touches on the camera:
  * check / verify / export only read: /proc files, the im.elf process memory (debugger OP_MEM),
    library files, and logs under /tmp_bt/mods. No /dev/mem access at all: tools/devmem_policy.py
    forbids /dev/mem reads of DRAM, where the av-cam.bin tables live.
  * apply writes one file, /tmp_bt/mods/OpenGateA1.mod (read back and hash-checked). The mod itself
    is switched ON by the user in MENU -> Mods; Mod Loader v2 must already be installed.
  * putback removes that file again, only after the mod's log reports a full OFF.

The site tables are parsed from mods/opengate_a1/opengate_a1.c and build_v2_wsl.sh, so the kit and
the mod cannot drift apart. Receipts go to mods/opengate_a1/kit/camera_private/ (ignored by Git).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import struct
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parents[2]
MOD_DIR = ROOT / "mods" / "opengate_a1"
KIT_DIR = MOD_DIR / "kit"
MOD_SOURCE = MOD_DIR / "opengate_a1.c"
BUILD_SCRIPT = MOD_DIR / "build_v2_wsl.sh"
IDENTITY_FILE = KIT_DIR / "identity.json"
PRIVATE_DIR = KIT_DIR / "camera_private"

REMOTE_MOD = "/tmp_bt/mods/OpenGateA1.mod"
REMOTE_LOG = "/tmp_bt/mods/opengate_a1.log"
REMOTE_LOADER_LOG = "/tmp_bt/mods/mod_loader.log"
READY_MAX_AGE_S = 30 * 60
MAX_MOD_BYTES = 1024 * 1024
OP_PS = 6  # tools.camera_debugger.OP_PS


class KitError(RuntimeError):
    """A refusal: the kit stops before doing anything it cannot justify."""


# --------------------------------------------------------------------------- mod specification

@dataclass(frozen=True)
class AvPatch:
    pa: int
    stock: int
    patched: int


@dataclass(frozen=True)
class Hook:
    name: str
    ref_va: int
    stock: bytes
    lib: str
    offset: int


@dataclass(frozen=True)
class ModSpec:
    av_patches: tuple[AvPatch, ...]
    vu_offsets: tuple[int, ...]
    vu_words: tuple[tuple[int, int, int], ...]  # (cell offset, stock, patched)
    hooks: tuple[Hook, ...]

    @property
    def expected_ok(self) -> int:
        return len(self.av_patches) + 2 * len(self.vu_offsets)


_AV_ROW = re.compile(r"\{\s*0x([0-9A-Fa-f]+)u?\s*,\s*(\d+)u?\s*,\s*(\d+)u?\s*\}")
_HOOK = re.compile(r'--hook\s+"0x([0-9A-Fa-f]+):([0-9A-Fa-f]+):([A-Za-z0-9_]+)"')
_MAP_LINE = re.compile(r"^([0-9a-f]+)-([0-9a-f]+)\s+(\S+)\s+([0-9a-f]+)\s+\S+\s+\d+\s+(\S+)\s*$")
_SWAP = re.compile(r"swap_proc_u32\(cell \+ 0x([0-9A-Fa-f]+)u,\s*enable \? 0x([0-9A-Fa-f]+)u : 0x([0-9A-Fa-f]+)u")


def parse_maps(text: str) -> dict[str, int]:
    """Base address of each file mapped r-xp at file offset 0."""
    bases: dict[str, int] = {}
    for line in text.splitlines():
        m = _MAP_LINE.match(line.strip())
        if m and m.group(3).startswith("r-x") and int(m.group(4), 16) == 0:
            bases.setdefault(m.group(5), int(m.group(1), 16))
    return bases


def parse_mod_spec(source: str, build_script: str, reference_layout: str) -> ModSpec:
    count = re.search(r"#define\s+AV_PATCH_COUNT\s+(\d+)", source)
    table = re.search(r"AV_PATCHES\[AV_PATCH_COUNT\]\s*=\s*\{(.*?)\n\};", source, re.S)
    if not count or not table:
        raise KitError("could not find AV_PATCHES in opengate_a1.c")
    av = tuple(AvPatch(int(a, 16), int(s), int(p)) for a, s, p in _AV_ROW.findall(table.group(1)))
    if len(av) != int(count.group(1)):
        raise KitError(f"AV_PATCHES has {len(av)} rows, AV_PATCH_COUNT says {count.group(1)}")
    vu_block = re.search(r"VU_CELL_OFFS\[\d+\]\s*=\s*\{(.*?)\};", source, re.S)
    if not vu_block:
        raise KitError("could not find VU_CELL_OFFS in opengate_a1.c")
    vu = tuple(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)u?", vu_block.group(1)))
    # swap_proc_u32(cell + 0x0Cu, enable ? <expected-when-ON = stock> : <patched>, ...)
    words = tuple((int(o, 16), int(stock, 16), int(patched, 16)) for o, stock, patched in _SWAP.findall(source))
    if len(words) != 2:
        raise KitError("expected two viewUnified2 word swaps in opengate_a1.c")
    bases = parse_maps(reference_layout)
    hooks = []
    for va, stock, name in _HOOK.findall(build_script):
        ref_va = int(va, 16)
        owner = next(((lib, base) for lib, base in sorted(bases.items(), key=lambda kv: -kv[1]) if base <= ref_va),
                     None)
        if owner is None:
            raise KitError(f"hook {name} at 0x{ref_va:08x} is outside the reference layout")
        hooks.append(Hook(name, ref_va, bytes.fromhex(stock), owner[0], ref_va - owner[1]))
    return ModSpec(av, vu, words, tuple(hooks))


def load_mod_spec(root: Path = ROOT) -> ModSpec:
    identity = load_identity(root)
    layout = (root / identity["mod_loader"]["reference_layout"]).read_text(encoding="utf-8")
    return parse_mod_spec(
        (root / MOD_SOURCE.relative_to(ROOT)).read_text(encoding="utf-8"),
        (root / BUILD_SCRIPT.relative_to(ROOT)).read_text(encoding="utf-8"),
        layout,
    )


def load_identity(root: Path = ROOT) -> dict[str, Any]:
    return json.loads((root / IDENTITY_FILE.relative_to(ROOT)).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- mod log

_LOG = re.compile(r"^(ON|OFF) av=(?:w([0-9a-f]{4}),a([0-9a-f]{4}),x([0-9a-f]{4})|(err)) "
                  r"vu=(nomap|[a-z]{16}) ok=([0-9a-f]{8})$")


@dataclass(frozen=True)
class LogLine:
    action: str
    written: int
    already: int
    mismatched: int
    av_error: bool
    vu: str
    ok: int

    @property
    def vu_complete(self) -> bool:
        return self.vu != "nomap" and all(c in "wa" for c in self.vu)


def parse_log_line(line: str) -> LogLine | None:
    m = _LOG.match(line.strip())
    if not m:
        return None
    action, w, a, x, err, vu, ok = m.groups()
    return LogLine(action, int(w or "0", 16), int(a or "0", 16), int(x or "0", 16), bool(err), vu, int(ok, 16))


def last_log_line(text: str) -> LogLine | None:
    for line in reversed(text.strip().splitlines()):
        parsed = parse_log_line(line)
        if parsed:
            return parsed
    return None


def classify_log(line: LogLine | None, spec: ModSpec) -> tuple[str, str]:
    """(state, explanation) for the newest log line: APPLIED, STOCK, MIXED or NONE."""
    if line is None:
        return "NONE", "the mod has not logged an ON or OFF in this power-on"
    total = len(spec.av_patches)
    clean = (not line.av_error and line.mismatched == 0 and line.written + line.already == total
             and line.vu_complete and line.ok == spec.expected_ok)
    if clean:
        return ("APPLIED" if line.action == "ON" else "STOCK"), (
            f"{line.action}: {line.written} av sites written, {line.already} already in place, 0 mismatched; "
            f"viewUnified2 {line.vu}")
    return "MIXED", (
        f"{line.action}: av written={line.written} already={line.already} mismatched={line.mismatched}"
        f"{' (open /dev/mem failed)' if line.av_error else ''}, viewUnified2={line.vu}, ok={line.ok} of "
        f"{spec.expected_ok}")


# --------------------------------------------------------------------------- camera inspection

def _exec(dbg: Any, command: str) -> str:
    from tools import camera_debugger as cd
    return dbg.request(cd.OP_EXEC, command.encode("utf-8"))["payload"].decode("utf-8", "replace")


def _uptime(dbg: Any) -> float:
    parts = _exec(dbg, "cat /proc/uptime").split()
    if not parts:
        raise KitError("could not read /proc/uptime")
    return float(parts[0])


def find_im_pid(dbg: Any) -> int:
    listing = dbg.request(OP_PS)["payload"].decode("utf-8", "replace")
    pids = [int(p[0]) for p in (line.strip().split(None, 1) for line in listing.splitlines())
            if len(p) == 2 and p[0].isdigit() and p[1].strip() == "im.elf"]
    if len(pids) != 1:
        raise KitError(f"expected exactly one im.elf process, found {pids}")
    return pids[0]


def remote_sha256(dbg: Any, path: str, size: int) -> str | None:
    """SHA-256 of a camera file: on-camera sha256sum when present, else a bounded read-back."""
    out = _exec(dbg, f"sha256sum {path} 2>/dev/null").strip().split()
    if out and re.fullmatch(r"[0-9a-f]{64}", out[0]):
        return out[0]
    data, truncated = dbg.read_all(path, limit=size + 1)
    if truncated:
        return None
    return hashlib.sha256(data).hexdigest()


@dataclass
class Report:
    command: str
    started_utc: str
    verdict: str = "NOT READY"
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def fail(self, reason: str) -> None:
        self.reasons.append(reason)

    def to_json(self) -> dict[str, Any]:
        return {"kit": "opengate_a6400", "command": self.command, "started_utc": self.started_utc,
                "verdict": self.verdict, "reasons": self.reasons, "notes": self.notes, "facts": self.facts}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inspect(dbg: Any, spec: ModSpec, identity: dict[str, Any], report: Report) -> dict[str, Any]:
    """Read-only snapshot used by check, verify and putback."""
    facts: dict[str, Any] = report.facts
    facts["uptime_start"] = _uptime(dbg)
    pid = find_im_pid(dbg)
    facts["im_pid"] = pid
    maps_raw, _ = dbg.read_all(f"/proc/{pid}/maps", limit=256 * 1024)
    bases = parse_maps(maps_raw.decode("utf-8", "replace"))

    libs: dict[str, Any] = {}
    for path, pin in identity["libraries"].items():
        digest = remote_sha256(dbg, path, int(pin["bytes"]))
        libs[path] = {"sha256": digest, "match": digest == pin["sha256"], "base": bases.get(path)}
    facts["libraries"] = libs

    vu_base = bases.get("/usr/lib/viewUnified2.so")
    vu_state = []
    if vu_base is not None:
        for cell in spec.vu_offsets:
            for word_off, stock, patched in spec.vu_words:
                value = struct.unpack("<I", dbg.memory(pid, vu_base + cell + word_off, 4))[0]
                vu_state.append("s" if value == stock else "p" if value == patched else "?")
    facts["viewunified2_sites"] = "".join(vu_state)

    hooks = {}
    for hook in spec.hooks:
        base = bases.get(hook.lib)
        if base is None:
            hooks[hook.name] = {"lib": hook.lib, "mapped": False}
            continue
        live = dbg.memory(pid, base + hook.offset, len(hook.stock))
        hooks[hook.name] = {"lib": hook.lib, "offset": f"0x{hook.offset:x}", "live": live.hex(),
                            "expected_stock": hook.stock.hex(), "stock": live == hook.stock}
    facts["hooks"] = hooks

    facts["mod_loader_core"] = _exec(dbg, f"test -f {identity['mod_loader']['core']} && echo YES").strip() == "YES"
    staged = _exec(dbg, f"test -f {REMOTE_MOD} && echo YES").strip() == "YES"
    facts["staged_mod"] = None
    if staged:
        blob, truncated = dbg.read_all(REMOTE_MOD, limit=MAX_MOD_BYTES + 1)
        facts["staged_mod"] = None if truncated else hashlib.sha256(blob).hexdigest()
    log_text = _exec(dbg, f"tail -n 20 {REMOTE_LOG} 2>/dev/null")
    facts["mod_log_tail"] = log_text.strip().splitlines()[-5:]
    state, why = classify_log(last_log_line(log_text), spec)
    facts["mod_log_state"] = state
    facts["mod_log_detail"] = why
    facts["uptime_end"] = _uptime(dbg)
    if facts["uptime_end"] < facts["uptime_start"]:
        report.fail("camera uptime went backwards during the session: it restarted")
    report.notes.append(identity["avcam"]["note"])
    return facts


def check(dbg: Any, spec: ModSpec, identity: dict[str, Any]) -> Report:
    report = Report("check", _now())
    facts = inspect(dbg, spec, identity, report)
    for path, info in facts["libraries"].items():
        if info["base"] is None:
            report.fail(f"{path} is not mapped in im.elf")
        if not info["match"]:
            report.fail(f"{path} SHA-256 {info['sha256']} is not the {identity['firmware']} build this kit was "
                        f"made for ({identity['libraries'][path]['sha256']})")
    vu = facts["viewunified2_sites"]
    if len(vu) != 2 * len(spec.vu_offsets) or set(vu) != {"s"}:
        report.fail(f"viewUnified2 screen sites are not all stock (s=stock, p=patched, ?=unknown): {vu or 'unread'}")
    for name, info in facts["hooks"].items():
        if not info.get("mapped", True):
            report.fail(f"{info['lib']} is not mapped, so hook {name} cannot be checked")
        elif not info["stock"]:
            report.fail(f"hook {name}: {info['lib']}+{info['offset']} holds {info['live']}, the mod expects "
                        f"{info['expected_stock']}; Mod Loader v2 would refuse or misplace this hook")
    if not facts["mod_loader_core"]:
        report.fail("Mod Loader v2 is not installed in this power-on (no /tmp_bt/mlv2/core.bin)")
    if facts["mod_log_state"] in ("APPLIED", "MIXED"):
        report.fail(f"the mod is not off: {facts['mod_log_detail']}")
    if not report.reasons:
        report.verdict = "READY (stock preflight)"
    return report


def verify(dbg: Any, spec: ModSpec, identity: dict[str, Any]) -> Report:
    report = Report("verify", _now())
    facts = inspect(dbg, spec, identity, report)
    state = facts["mod_log_state"]
    vu = facts["viewunified2_sites"]
    if state == "APPLIED" and vu and set(vu) == {"p"}:
        report.verdict = "APPLIED"
    elif state in ("STOCK", "NONE") and vu and set(vu) == {"s"}:
        report.verdict = "STOCK"
    else:
        report.verdict = "MIXED"
        report.fail(f"mod log: {facts['mod_log_detail']}; live viewUnified2 sites: {vu}")
        report.notes.append("Switch the mod OFF in MENU -> Mods and run verify again. If it still is not STOCK, "
                            "switch the camera off and pull the battery for 10 seconds (a cold boot reloads the "
                            "libraries and av-cam.bin from flash; inferred, not yet observed for this mod).")
    return report


# --------------------------------------------------------------------------- receipts

def write_receipt(report: Report, private_dir: Path = PRIVATE_DIR) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = private_dir / f"{stamp}-{report.command}"
    suffix = 1
    while folder.exists():
        suffix += 1
        folder = private_dir / f"{stamp}-{report.command}-{suffix}"
    folder.mkdir(parents=True)
    path = folder / f"{report.command}.json"
    path.write_text(json.dumps(report.to_json(), indent=2) + "\n", encoding="utf-8")
    return path


def latest_ready(private_dir: Path = PRIVATE_DIR) -> dict[str, Any] | None:
    receipts = sorted(private_dir.glob("*-check*/check.json")) if private_dir.is_dir() else []
    for path in reversed(receipts):
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data.get("verdict", "").startswith("READY") else None
    return None


def require_fresh_ready(previous: dict[str, Any] | None, current: Report) -> None:
    """apply only runs after a READY check from this power-on and this camera build."""
    if previous is None:
        raise KitError("no READY check on record: run `check` first")
    if not current.verdict.startswith("READY"):
        raise KitError("the check made just now is NOT READY: " + "; ".join(current.reasons))
    then = previous["facts"]
    now = current.facts
    if now["uptime_start"] < then["uptime_end"]:
        raise KitError("the camera restarted since the READY check: run `check` again")
    if now["uptime_start"] - then["uptime_end"] > READY_MAX_AGE_S:
        raise KitError("the READY check is older than 30 minutes: run `check` again")
    for path, info in now["libraries"].items():
        if then["libraries"].get(path, {}).get("sha256") != info["sha256"]:
            raise KitError(f"{path} changed since the READY check")


# --------------------------------------------------------------------------- commands

def apply(dbg: Any, spec: ModSpec, identity: dict[str, Any], mod_bytes: bytes,
          confirm: Callable[[str], bool], private_dir: Path = PRIVATE_DIR) -> Report:
    previous = latest_ready(private_dir)
    current = check(dbg, spec, identity)
    write_receipt(current, private_dir)
    require_fresh_ready(previous, current)
    if not 0 < len(mod_bytes) <= MAX_MOD_BYTES:
        raise KitError("the .mod file is empty or larger than 1 MiB")
    digest = hashlib.sha256(mod_bytes).hexdigest()
    if not confirm(f"Stage OpenGateA1.mod ({len(mod_bytes)} bytes, sha256 {digest[:16]}...) in {REMOTE_MOD}?"):
        raise KitError("not confirmed; nothing was written")
    result = dbg.push(REMOTE_MOD, mod_bytes)
    report = Report("apply", _now(), verdict="STAGED")
    report.facts.update({"remote_path": REMOTE_MOD, "sha256": digest, "bytes": len(mod_bytes),
                         "readback_verified": bool(result.get("readback_verified")),
                         "uptime": _uptime(dbg)})
    report.notes.append("Next, on the camera: MENU -> tab 7 (Mods) -> switch 'OpenGate A1' ON, leave the menu, "
                        "then run `verify`. Record only XAVC S 4K 24p while it is applied.")
    return report


def putback(dbg: Any, spec: ModSpec, identity: dict[str, Any], private_dir: Path = PRIVATE_DIR) -> Report:
    report = verify(dbg, spec, identity)
    report.command = "putback"
    if report.verdict != "STOCK":
        report.notes.insert(0, "Switch 'OpenGate A1' OFF in MENU -> Mods first, then run putback again.")
        return report
    staged = report.facts.get("staged_mod")
    pushed = [json.loads(p.read_text(encoding="utf-8")).get("facts", {}).get("sha256")
              for p in sorted(private_dir.glob("*-apply*/apply.json"))] if private_dir.is_dir() else []
    if staged and staged in pushed:
        _exec(dbg, f"rm {REMOTE_MOD}")
        gone = _exec(dbg, f"test -f {REMOTE_MOD} || echo GONE").strip() == "GONE"
        report.facts["staged_mod_removed"] = gone
        if not gone:
            report.verdict = "STOCK (staged .mod not removed)"
    elif staged:
        report.notes.append(f"{REMOTE_MOD} was not staged by this kit (hash unknown); it was left in place.")
    return report


def export(dbg: Any | None, private_dir: Path = PRIVATE_DIR) -> Path:
    """Zip this kit's receipts plus a read-only log snapshot. Never reads /setting or Backup files."""
    private_dir.mkdir(parents=True, exist_ok=True)
    snapshot: dict[str, str] = {}
    if dbg is not None:
        for name, command in (
                ("uptime.txt", "cat /proc/uptime"),
                ("version.txt", "cat /proc/version"),
                ("opengate_a1.log", f"tail -n 200 {REMOTE_LOG} 2>/dev/null"),
                ("mod_loader.log", f"tail -n 200 {REMOTE_LOADER_LOG} 2>/dev/null"),
                ("klog-tail.txt", "tail -n 200 /proc/klog 2>/dev/null"),
        ):
            snapshot[name] = _exec(dbg, command)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = private_dir / f"opengate_a6400-logs-{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(private_dir.rglob("*.json")):
            zf.write(path, path.relative_to(private_dir).as_posix())
        for name, text in snapshot.items():
            zf.writestr(f"snapshot/{name}", text)
    return out


# --------------------------------------------------------------------------- simulated camera

class SimulatedCamera:
    """A debugger stand-in for selftest and unit tests: stock libraries, Mod Loader present."""

    VU_BASE = 0x155E6000
    MPR_BASE = 0x07144000

    def __init__(self, spec: ModSpec, identity: dict[str, Any], *, libmpr_shift: int = 0):
        self.spec = spec
        self.identity = identity
        self.uptime = 100.0
        self.files: dict[str, bytes] = {identity["mod_loader"]["core"]: b"core"}
        self.mem: dict[int, int] = {}
        for cell in spec.vu_offsets:
            for word_off, stock, _patched in spec.vu_words:
                self._put32(self.VU_BASE + cell + word_off, stock)
        for hook in spec.hooks:
            addr = self.MPR_BASE + hook.offset + libmpr_shift
            for i, b in enumerate(hook.stock):
                self.mem[addr + i] = b
        self.log: list[str] = []
        self.lib_hashes = {p: pin["sha256"] for p, pin in identity["libraries"].items()}
        self.commands: list[str] = []

    def _put32(self, addr: int, value: int) -> None:
        for i, b in enumerate(struct.pack("<I", value)):
            self.mem[addr + i] = b

    # debugger interface -------------------------------------------------------------------
    def request(self, op: int, payload: bytes = b"", arg0: int = 0, arg1: int = 0) -> dict[str, Any]:
        if op == OP_PS:
            return {"payload": b"  1 init\n412 im.elf\n", "status": 0, "flags": 0}
        command = payload.decode()
        self.commands.append(command)
        self.uptime += 0.5
        out = ""
        if command == "cat /proc/uptime":
            out = f"{self.uptime:.2f} 10.00\n"
        elif command.startswith("sha256sum "):
            out = f"{self.lib_hashes.get(command.split()[1], '0' * 64)}  {command.split()[1]}\n"
        elif command.startswith("test -f ") and "&& echo YES" in command:
            out = "YES\n" if command.split()[2] in self.files else ""
        elif command.startswith("test -f ") and "|| echo GONE" in command:
            out = "" if command.split()[2] in self.files else "GONE\n"
        elif command.startswith(f"tail -n 20 {REMOTE_LOG}") or command.startswith(f"tail -n 200 {REMOTE_LOG}"):
            out = "\n".join(self.log) + ("\n" if self.log else "")
        elif command.startswith("rm "):
            self.files.pop(command.split()[1], None)
        elif command in ("cat /proc/version",) or command.startswith("tail -n 200"):
            out = "simulated\n"
        return {"payload": out.encode(), "status": 0, "flags": 0}

    def read_all(self, path: str, limit: int = 2 * 1024 * 1024) -> tuple[bytes, bool]:
        if path.endswith("/maps"):
            text = (f"{self.MPR_BASE:08x}-07d32000 r-xp 00000000 fc:0f 22451      /usr/lib/libmpr.so\n"
                    f"{self.VU_BASE:08x}-15e92000 r-xp 00000000 fc:0f 22532      /usr/lib/viewUnified2.so\n")
            return text.encode(), False
        data = self.files.get(path, b"")
        return data[:limit], len(data) > limit

    def memory(self, pid: int, address: int, size: int) -> bytes:
        return bytes(self.mem.get(address + i, 0) for i in range(size))

    def push(self, path: str, data: bytes) -> dict[str, Any]:
        self.files[path] = bytes(data)
        return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "readback_verified": True}

    # user actions on the camera ------------------------------------------------------------
    def toggle(self, on: bool, *, mismatched: int = 0) -> None:
        total = len(self.spec.av_patches)
        for cell in self.spec.vu_offsets:
            for word_off, stock, patched in self.spec.vu_words:
                self._put32(self.VU_BASE + cell + word_off, patched if on else stock)
        ok = total - mismatched + 2 * len(self.spec.vu_offsets)
        self.log.append(f"{'ON' if on else 'OFF'} av=w{total - mismatched:04x},a0000,x{mismatched:04x} "
                        f"vu={'w' * 16} ok={ok:08x}")


def selftest(spec: ModSpec, identity: dict[str, Any], private_dir: Path) -> list[str]:
    """Run check -> apply -> verify -> putback on a simulated camera; returns the step log."""
    steps = []
    cam = SimulatedCamera(spec, identity)
    r = check(cam, spec, identity)
    write_receipt(r, private_dir)
    steps.append(f"check: {r.verdict}")
    a = apply(cam, spec, identity, b"SMOD-simulated", lambda _q: True, private_dir)
    write_receipt(a, private_dir)
    steps.append(f"apply: {a.verdict}")
    cam.toggle(True)
    steps.append(f"verify after ON: {verify(cam, spec, identity).verdict}")
    cam.toggle(False)
    p = putback(cam, spec, identity, private_dir)
    steps.append(f"putback after OFF: {p.verdict} (staged .mod removed: {p.facts.get('staged_mod_removed')})")
    shifted = SimulatedCamera(spec, identity, libmpr_shift=-0xF480)
    steps.append(f"check with libmpr hooks 0xF480 lower: {check(shifted, spec, identity).verdict}")
    return steps


# --------------------------------------------------------------------------- CLI

@contextlib.contextmanager
def _connect(args: argparse.Namespace) -> Iterator[Any]:
    from tools import camera_debugger as cd
    with cd.connect(args.transport, host=args.host, port=args.port) as dbg:
        yield dbg


def _print(report: Report, receipt: Path | None) -> None:
    print(f"VERDICT {report.verdict}")
    for reason in report.reasons:
        print(f"  !! {reason}")
    for note in report.notes:
        print(f"  -- {note}")
    if receipt:
        print(f"receipt: {receipt.relative_to(ROOT) if receipt.is_relative_to(ROOT) else receipt}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.kits.opengate_a6400", description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("check", "apply", "verify", "putback", "export", "selftest"))
    parser.add_argument("--transport", default="auto", choices=("auto", "winusb", "tcp", "qemu"))
    parser.add_argument("--host", help="TCP debugger host (Wi-Fi debugger)")
    parser.add_argument("--port", type=int, default=4640)
    parser.add_argument("--mod", type=Path, help="OpenGateA1.mod to stage (apply)")
    parser.add_argument("--yes", action="store_true", help="answer the apply confirmation with yes")
    args = parser.parse_args(argv)

    spec = load_mod_spec()
    identity = load_identity()
    try:
        if args.command == "selftest":
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                for line in selftest(spec, identity, Path(tmp)):
                    print(line)
            return 0
        if args.command == "export":
            with _connect(args) as dbg:
                print(f"log zip: {export(dbg)}")
            return 0
        with _connect(args) as dbg:
            if args.command == "check":
                report = check(dbg, spec, identity)
            elif args.command == "verify":
                report = verify(dbg, spec, identity)
            elif args.command == "putback":
                report = putback(dbg, spec, identity)
            else:
                if not args.mod or not args.mod.is_file():
                    raise KitError("apply needs --mod <path to the built OpenGateA1.mod> "
                                   "(wsl.exe --exec bash mods/opengate_a1/build_v2_wsl.sh <out-dir>)")
                confirm = (lambda _q: True) if args.yes else (lambda q: input(f"{q} Type YES: ").strip() == "YES")
                report = apply(dbg, spec, identity, args.mod.read_bytes(), confirm)
        receipt = write_receipt(report)
        _print(report, receipt)
        return 0 if report.verdict.startswith(("READY", "STAGED", "APPLIED", "STOCK")) else 1
    except KitError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
