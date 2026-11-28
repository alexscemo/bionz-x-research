#!/usr/bin/env python3
"""Build, launch, and auto-symbolize targeted QEMU TCG trace runs.

Combines snapshot instant-resume (tools.research.qemu_snapshot), QEMU Plugin API v7
(emulators/qemu-a6400/tools/tcg_trace.c), and Tier-1 SQLite symbol resolution
(tools.research.symbol_index) into a single reproducible workflow.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
from typing import Any

from project_paths import PROJECT_ROOT
from tools.research.qemu_boot_probe import Qmp, console_snapshot
from tools.research.qemu_snapshot import (
    _build_env,
    _connect_qmp,
    _run_serial_commands,
    check_manifest,
    default_qemu_dir,
)
from tools.research.symbol_index import open_db, symbolize_offset

DEFAULT_PLUGIN_SO = Path("/tmp/a6400-tcg-plugins/libtcg_trace.so")
_MAPS_RE = re.compile(
    r"^([0-9a-fA-F]+)-([0-9a-fA-F]+)\s+\S+\s+([0-9a-fA-F]+)\s+\S+\s+\d+\s+(.*)$"
)


@dataclass(frozen=True)
class MapRegion:
    start: int
    end: int
    file_off: int
    binary: str


def parse_proc_maps(text: str) -> list[MapRegion]:
    """Parse ``/proc/<pid>/maps`` lines and collapse split RX/RW mappings by basename."""
    regions: list[MapRegion] = []
    min_base_by_name: dict[str, int] = {}
    raw: list[tuple[int, int, int, str]] = []
    for line in text.splitlines():
        m = _MAPS_RE.match(line.strip())
        if not m:
            continue
        start = int(m.group(1), 16)
        end = int(m.group(2), 16)
        off = int(m.group(3), 16)
        path_str = m.group(4).strip()
        if not path_str or path_str.startswith("["):
            continue
        name = Path(path_str).name
        if name not in min_base_by_name or start < min_base_by_name[name]:
            min_base_by_name[name] = start
        raw.append((start, end, off, name))
    for start, end, _off, name in raw:
        regions.append(MapRegion(start, end, min_base_by_name[name], name))
    return regions


def build_plugin(out_so: Path = DEFAULT_PLUGIN_SO, qemu_dir: Path | None = None) -> Path:
    """Compile ``emulators/qemu-a6400/tools/tcg_trace.c`` as a QEMU v7 shared plugin."""
    if os.name != "posix":
        raise RuntimeError("TCG plugin compilation runs on WSL2/Linux")
    qdir = qemu_dir or default_qemu_dir()
    src = PROJECT_ROOT / "emulators/qemu-a6400" / "tools" / "tcg_trace.c"
    inc_plugins = qdir / "include" / "plugins"
    if not (inc_plugins / "qemu-plugin.h").is_file():
        raise FileNotFoundError(f"Missing qemu-plugin.h under {inc_plugins}")
    out_so = Path(out_so)
    out_so.parent.mkdir(parents=True, exist_ok=True)
    glib_cflags = subprocess.check_output(
        ["pkg-config", "--cflags", "glib-2.0"], text=True
    ).split()
    cmd = [
        "gcc",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-unused-parameter",
        "-fPIC",
        "-shared",
        f"-I{inc_plugins}",
        *glib_cflags,
        str(src),
        "-o",
        str(out_so),
    ]
    subprocess.run(cmd, check=True)
    return out_so


def format_plugin_arg(
    so_path: Path,
    *,
    out_jsonl: Path,
    probes: list[str] = (),
    derefs: list[str] = (),
    mem_ranges: list[str] = (),
    vmem_ranges: list[str] = (),
    ring: int = 256,
    ring_ranges: list[str] = (),
    max_events: int = 10000,
    dump_ring: bool = False,
    exit_on_hit: bool = False,
) -> str:
    """Format a safe ``-plugin <so>,key=val,...`` QEMU argument string."""
    parts = [
        str(so_path),
        f"out={out_jsonl}",
        f"ring={ring}",
        f"max_events={max_events}",
    ]
    if dump_ring:
        parts.append("dump_ring=1")
    if exit_on_hit:
        parts.append("exit_on_hit=1")
    for p in probes:
        parts.append(f"probe={p}")
    for d in derefs:
        parts.append(f"deref={d}")
    for m in mem_ranges:
        parts.append(f"mem={m}")
    for vm in vmem_ranges:
        parts.append(f"vmem={vm}")
    for rr in ring_ranges:
        parts.append(f"ring_range={rr}")
    return ",".join(parts)


def resolve_va(
    va: int,
    regions: list[MapRegion],
    conn: sqlite3.Connection | None,
    *,
    default_binary: str | None = None,
    body: str | None = None,
) -> str | None:
    for r in regions:
        if r.start <= va < r.end:
            rel_va = va - r.file_off
            if conn is not None:
                row = conn.execute(
                    "SELECT base_va FROM binaries WHERE filename = ? LIMIT 1", (r.binary,)
                ).fetchone()
                base_va = int(row["base_va"]) if row is not None else 0
                return symbolize_offset(conn, r.binary, rel_va + base_va, body=body)
            return f"{r.binary}+0x{rel_va:x}"
    if conn is not None and default_binary:
        return symbolize_offset(conn, default_binary, va, body=body)
    return None


def symbolize_trace(
    jsonl_path: Path,
    *,
    maps_path: Path | None = None,
    db_path: Path | None = None,
    default_binary: str | None = None,
    body: str | None = None,
) -> list[dict[str, Any]]:
    """Annotate JSONL trace events with resolved ``binary!symbol+0xoff`` labels."""
    regions = parse_proc_maps(maps_path.read_text(encoding="utf-8")) if maps_path else []
    conn: sqlite3.Connection | None = None
    if db_path is None:
        from tools.research.symbol_index import default_db_path
        cand = default_db_path()
        if cand.is_file():
            conn = open_db(cand)
    elif db_path.is_file():
        conn = open_db(db_path)

    events: list[dict[str, Any]] = []
    try:
        for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            for field in ("pc", "va"):
                raw = ev.get(field)
                if isinstance(raw, str) and raw.startswith("0x") and field == "pc":
                    sym = resolve_va(int(raw, 16), regions, conn, default_binary=default_binary, body=body)
                    if sym:
                        ev["pc_sym"] = sym
            regs = ev.get("regs")
            if isinstance(regs, dict):
                if isinstance(regs.get("lr"), str) and regs["lr"].startswith("0x"):
                    lsym = resolve_va(int(regs["lr"], 16), regions, conn, default_binary=default_binary, body=body)
                    if lsym:
                        ev["lr_sym"] = lsym
                if isinstance(regs.get("pc"), str) and regs["pc"].startswith("0x"):
                    psym = resolve_va(int(regs["pc"], 16), regions, conn, default_binary=default_binary, body=body)
                    if psym:
                        ev["pc_sym"] = psym
            events.append(ev)
    finally:
        if conn is not None:
            conn.close()
    return events


def run_trace(args: argparse.Namespace) -> dict[str, Any]:
    """Run QEMU (from snapshot or cold boot) with ``tcg_trace`` attached."""
    if os.name != "posix":
        raise RuntimeError("QEMU TCG trace runs must execute inside WSL2/Linux")
    report_dir = args.report_dir.resolve()
    if report_dir.exists() and any(report_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty report dir: {report_dir}")
    report_dir.mkdir(parents=True, exist_ok=True)

    so_path = Path(args.plugin_so)
    if args.rebuild or not so_path.is_file():
        build_plugin(so_path)

    trace_jsonl = report_dir / "trace.jsonl"
    plugin_arg = format_plugin_arg(
        so_path,
        out_jsonl=trace_jsonl,
        probes=args.probe,
        derefs=args.deref,
        mem_ranges=args.mem,
        vmem_ranges=args.vmem,
        ring=args.ring,
        ring_ranges=args.ring_range,
        max_events=args.max_events,
        dump_ring=args.dump_ring,
        exit_on_hit=args.exit_on_hit,
    )

    machine_env: dict[str, str] = {}
    inputs_path = args.inputs.resolve() if args.inputs else None
    snapshot_qcow = None
    loadvm_tag = None
    if args.from_snapshot:
        manifest, snapshot_qcow, loadvm_tag = check_manifest(
            args.from_snapshot.resolve(), skip_hash_check=args.skip_hash_check
        )
        machine_env.update(manifest.get("machine_env", {}))
        if not inputs_path and manifest.get("inputs_dir"):
            inputs_path = Path(manifest["inputs_dir"])

    run_script = PROJECT_ROOT / "emulators/qemu-a6400" / "run.sh"
    with tempfile.TemporaryDirectory(prefix="a6400-tcg-run-") as tmp:
        tmp_path = Path(tmp)
        qmp_sock = tmp_path / "qmp.sock"
        serial_sock = tmp_path / "serial.sock"
        env = _build_env(
            inputs=inputs_path,
            run_dir=tmp_path / "run",
            qmp_sock=qmp_sock,
            serial_sock=serial_sock,
            machine_env=machine_env,
            snapshot_qcow=snapshot_qcow,
            loadvm_tag=loadvm_tag,
        )
        env["QEMU_EXTRA"] = env["QEMU_EXTRA"] + f" -plugin {plugin_arg} -d plugin"

        t0 = time.monotonic()
        qemu_log = report_dir / "qemu.log"
        serial_text = ""
        statuses: dict[int, int] = {}
        with qemu_log.open("wb") as log_f:
            proc = subprocess.Popen(
                ["bash", str(run_script)],
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
            )
            qmp: Qmp | None = None
            try:
                deadline = time.monotonic() + args.seconds
                if args.exit_on_hit and not args.guest_command:
                    while time.monotonic() < deadline:
                        if proc.poll() is not None:
                            break
                        time.sleep(0.05)
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=10)
                else:
                    qmp = _connect_qmp(qmp_sock, proc, deadline)
                    if args.guest_command or args.wait_for:
                        serial_text, statuses = _run_serial_commands(
                            serial_sock,
                            args.guest_command,
                            deadline,
                            wait_for=args.wait_for,
                            need_shell=args.from_snapshot is None,
                        )
                    else:
                        time.sleep(min(args.observe_seconds, max(0.2, deadline - time.monotonic())))
                    if proc.poll() is None:
                        qmp.quit(proc, timeout=15)
            finally:
                if qmp is not None:
                    qmp.close()
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=10)
        elapsed = round(time.monotonic() - t0, 3)

    events = (
        symbolize_trace(
            trace_jsonl,
            maps_path=args.maps,
            db_path=args.db,
            default_binary=args.default_binary,
            body=args.body,
        )
        if trace_jsonl.is_file()
        else []
    )
    report = {
        "elapsed_seconds": elapsed,
        "from_snapshot": str(args.from_snapshot) if args.from_snapshot else None,
        "loadvm_tag": loadvm_tag,
        "plugin_arg": plugin_arg,
        "event_count": len(events),
        "guest_statuses": statuses,
        "events": events[:200],
    }
    if serial_text:
        (report_dir / "serial.log").write_text(serial_text, encoding="utf-8")
    (report_dir / "trace-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Compile libtcg_trace.so against QEMU v7 plugin headers")
    b.add_argument("--out", type=Path, default=DEFAULT_PLUGIN_SO)

    r = sub.add_parser("run", help="Run QEMU with tcg_trace and emit symbolized JSON report")
    r.add_argument("--report-dir", type=Path, required=True)
    r.add_argument("--from-snapshot", type=Path, default=None)
    r.add_argument("--inputs", type=Path, default=None)
    r.add_argument("--probe", action="append", default=[])
    r.add_argument("--deref", action="append", default=[])
    r.add_argument("--mem", action="append", default=[])
    r.add_argument("--vmem", action="append", default=[])
    r.add_argument("--ring", type=int, default=256)
    r.add_argument("--ring-range", action="append", default=[])
    r.add_argument("--max-events", type=int, default=10000)
    r.add_argument("--dump-ring", action="store_true")
    r.add_argument("--exit-on-hit", action="store_true")
    r.add_argument("--seconds", type=float, default=30.0)
    r.add_argument("--observe-seconds", type=float, default=2.0)
    r.add_argument("--wait-for", default=None)
    r.add_argument("--guest-command", action="append", default=[])
    r.add_argument("--maps", type=Path, default=None)
    r.add_argument("--db", type=Path, default=None)
    r.add_argument("--default-binary", default=None)
    r.add_argument("--body", default=None)
    r.add_argument("--plugin-so", type=Path, default=DEFAULT_PLUGIN_SO)
    r.add_argument("--rebuild", action="store_true")
    r.add_argument("--skip-hash-check", action="store_true")

    s = sub.add_parser("symbolize", help="Symbolize an existing tcg_trace JSONL file")
    s.add_argument("--jsonl", type=Path, required=True)
    s.add_argument("--maps", type=Path, default=None)
    s.add_argument("--db", type=Path, default=None)
    s.add_argument("--default-binary", default=None)
    s.add_argument("--body", default=None)

    args = parser.parse_args(argv)
    if args.cmd == "build":
        so = build_plugin(args.out)
        print(json.dumps({"built": str(so), "bytes": so.stat().st_size}, indent=2))
        return 0
    if args.cmd == "run":
        out = run_trace(args)
        print(json.dumps({k: v for k, v in out.items() if k != "events"}, indent=2))
        return 0
    if args.cmd == "symbolize":
        events = symbolize_trace(
            args.jsonl,
            maps_path=args.maps,
            db_path=args.db,
            default_binary=args.default_binary,
            body=args.body,
        )
        print(json.dumps(events, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
