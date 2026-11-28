"""Headless QEMU runner wrapper with QMP screendump, unmodelled-MMIO, and sequencer receipts.

Runs emulators/qemu-a6400/run.sh in an isolated temporary run directory, captures serial
output, QEMU stdout/stderr, a QMP screendump, and optional `-d unimp` counts,
and locates the mode-sequencer index in guest .bss via the startup-table
signature in av-cam.bin (no hardcoded per-build .bss address).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from project_paths import PROJECT_ROOT
from tools.research.inventory import digest

AVCAM_LOAD_VA = 0x635C6000
STARTUP_TABLE_SIG = struct.pack("<7H", 0x0105, 0x0285, 0x0320, 0x0400, 0x0500, 0x0600, 0x0780)
BSS_SCAN_PA = 0x04300000
BSS_SCAN_SIZE = 4 * 1024 * 1024

TARGET_PROFILES = {
    "a6400": {"machine": "sony-a6400", "data_subdir": "artifacts/boot-inputs-a6400"},
    "ilce_6400": {"machine": "sony-a6400", "data_subdir": "artifacts/boot-inputs-a6400"},
    "a6100": {"machine": "sony-a6100", "data_subdir": "artifacts/boot-inputs-a6100"},
    "ilce_6100": {"machine": "sony-a6100", "data_subdir": "artifacts/boot-inputs-a6100"},
    "6100a": {"machine": "sony-a6100", "data_subdir": "artifacts/boot-inputs-a6100"},
    "a6400a": {"machine": "sony-a6400a", "data_subdir": "artifacts/boot-inputs-6400a"},
    "6400a": {"machine": "sony-a6400a", "data_subdir": "artifacts/boot-inputs-6400a"},
    "ilce_6400a": {"machine": "sony-a6400a", "data_subdir": "artifacts/boot-inputs-6400a"},
}


def resolve_target(
    args: argparse.Namespace,
    *,
    project_root: Path = PROJECT_ROOT,
) -> tuple[str, Path, str]:
    """Resolve (machine, data_dir, submicro_frames) from --target and explicit flags."""
    target = getattr(args, "target", "") or ""
    profile = TARGET_PROFILES.get(target.lower()) if target else None
    if target and profile is None:
        raise ValueError(f"Unknown --target {target!r}; choose from {sorted(TARGET_PROFILES)}")

    machine = getattr(args, "machine", "") or (profile["machine"] if profile else "sony-a6400")
    raw_data_dir = getattr(args, "data_dir", "") or (
        str(project_root / profile["data_subdir"]) if profile else ""
    )
    if not raw_data_dir:
        raise ValueError("Provide --target or --data-dir")
    data_dir = Path(raw_data_dir).resolve()

    submicro_frames = getattr(args, "submicro_frames", "") or ""
    if not submicro_frames and profile is not None:
        candidate = data_dir / "submicro-frames-powered.bin"
        if candidate.is_file():
            submicro_frames = str(candidate)

    return machine, data_dir, submicro_frames


def find_sequencer_table_va(avcam_path: Path) -> tuple[int, str]:
    """Locate the 43-step mode-sequencer startup table VA in av-cam.bin."""
    data = avcam_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    idx = data.find(STARTUP_TABLE_SIG)
    if idx < 0:
        raise ValueError(f"Startup mode-sequencer table signature not found in {avcam_path}")
    return AVCAM_LOAD_VA + idx, sha


def find_sequencer_in_ram(ram_bytes: bytes, table_va: int, base_pa: int = BSS_SCAN_PA) -> dict | None:
    """Locate `<table_va, 0x2B>` inside a guest .bss snapshot and read its index."""
    pat = struct.pack("<II", table_va, 0x2B)
    idx = ram_bytes.find(pat)
    if idx < 0 or idx < 0x0C:
        return None
    obj_off = idx - 0x0C
    if obj_off + 0x20 > len(ram_bytes):
        return None
    active, tbl, count, step = struct.unpack_from("<4I", ram_bytes, obj_off + 0x08)
    return {
        "obj_pa": base_pa + obj_off,
        "index_pa": base_pa + obj_off + 0x14,
        "active": active,
        "table_va": tbl,
        "count": count,
        "step": step,
    }


def _qmp_screendump(sock_path: Path, out_ppm: Path) -> bool:
    if not sock_path.exists():
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect(str(sock_path))
        sock.recv(4096)
        sock.sendall(b'{"execute":"qmp_capabilities"}\n')
        sock.recv(4096)
        cmd = json.dumps({"execute": "screendump", "arguments": {"filename": str(out_ppm)}}) + "\n"
        sock.sendall(cmd.encode("utf-8"))
        resp = sock.recv(4096).decode("utf-8", errors="replace")
        return '"return"' in resp and out_ppm.is_file()
    except OSError:
        return False
    finally:
        sock.close()


def _qmp_read_sequencer(sock_path: Path, table_va: int) -> dict | None:
    if not sock_path.exists():
        return None
    with tempfile.TemporaryDirectory(prefix="qmp-seq-") as tmp:
        dump = Path(tmp) / "bss.bin"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10.0)
        try:
            sock.connect(str(sock_path))
            sock.recv(4096)
            sock.sendall(b'{"execute":"qmp_capabilities"}\n')
            sock.recv(4096)
            cmd = json.dumps({
                "execute": "pmemsave",
                "arguments": {"val": BSS_SCAN_PA, "size": BSS_SCAN_SIZE, "filename": str(dump)},
            }) + "\n"
            sock.sendall(cmd.encode("utf-8"))
            resp = sock.recv(4096).decode("utf-8", errors="replace")
            if '"return"' not in resp or not dump.is_file():
                return None
            return find_sequencer_in_ram(dump.read_bytes(), table_va)
        except OSError:
            return None
        finally:
            sock.close()


def parse_ppm(ppm_path: Path) -> dict:
    if not ppm_path.is_file():
        return {"present": False, "width": 0, "height": 0, "nonzero_bytes": 0, "sha256": None}
    data = ppm_path.read_bytes()
    match = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", data)
    if not match:
        return {"present": True, "width": 0, "height": 0, "nonzero_bytes": 0, "sha256": digest(ppm_path)}
    width, height = int(match.group(1)), int(match.group(2))
    pixels = data[match.end():]
    return {
        "present": True,
        "width": width,
        "height": height,
        "nonzero_bytes": sum(1 for b in pixels if b != 0),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def parse_logs(serial_text: str, stdout_text: str, *, d_unimp: bool = False) -> dict:
    ch53_bases = re.findall(r"\[VPH\] ch53 base=(0x[0-9a-fA-F]+) ctrl=(0x[0-9a-fA-F]+)", stdout_text)
    ch53_enabled = any(b != "0x00000000" and c != "0x0" for b, c in ch53_bases)
    ch51_bases = re.findall(r"\[VPH\] ch51 base=(0x[0-9a-fA-F]+) .*?en144=(0x[0-9a-fA-F]+)", stdout_text)
    ch51_enabled = any(b != "0x00000000" for b, _ in ch51_bases)
    unmod_pairs = sorted(set(re.findall(r"(cxd90045\.[^\s:]+unmodelled[^\n]*)", stdout_text)))
    return {
        "boot": {
            "kernel_booted": "Kernel command line:" in serial_text or "Booting Linux" in serial_text,
            "uart3_ok": "uart3: ttyAM1 at MMIO" in serial_text,
            "dmp_ppf_transfers": len(re.findall(r"\[DMP\] ppf-transfer\b", stdout_text)),
            "dmp_psc_fills": len(re.findall(r"\[DMP\] psc-fill\b", stdout_text)),
            "vph_ch53_enabled": ch53_enabled,
            "vph_ch51_enabled": ch51_enabled,
            "kernel_panic": "Kernel panic" in serial_text,
            "vector_exception": "vector exception" in serial_text,
        },
        "unmodelled": {
            "enabled": d_unimp,
            "distinct_pairs": len(unmod_pairs) if d_unimp else None,
            "lines": unmod_pairs[:50] if d_unimp else [],
        },
    }


def run_headless(args: argparse.Namespace) -> dict:
    run_sh = PROJECT_ROOT / "emulators/qemu-a6400" / "run.sh"
    toolchain = json.loads((PROJECT_ROOT / "tools" / "toolchain.json").read_text(encoding="utf-8"))
    qemu_dir = Path(args.qemu_dir or os.environ.get("A6400_QEMU_DIR")
                    or Path.home() / f"sony-a6400-qemu-{toolchain['qemu']['version']}")
    qemu_bin = qemu_dir / "build" / "qemu-system-arm"
    machine, data_dir, submicro_frames = resolve_target(args)
    manifest_path = data_dir / "boot-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    outputs = manifest.get("outputs", {})

    table_va = None
    if (data_dir / "av-cam.bin").is_file():
        try:
            table_va, _ = find_sequencer_table_va(data_dir / "av-cam.bin")
        except ValueError:
            table_va = None

    with tempfile.TemporaryDirectory(prefix="qemu-headless-") as tmpdir:
        tmp = Path(tmpdir)
        serial_log = tmp / "serial.log"
        stdout_log = tmp / "stdout.log"
        qmp_sock = tmp / "qmp.sock"
        screen_ppm = Path(args.screen_out) if args.screen_out else (tmp / "screen.ppm")
        run_dir = tmp / "run"

        extra = [f"-icount {args.icount}"] if args.icount else []
        extra.append(f"-qmp unix:{qmp_sock},server=on,wait=off")
        if args.d_unimp:
            extra.append("-d unimp")

        env = os.environ.copy()
        env.update({
            "A6400_QEMU_DIR": str(qemu_dir),
            "A6400_QEMU_DATA_DIR": str(data_dir),
            "RUN_DIR": str(run_dir),
            "MACHINE_NAME": machine,
            "SERIAL": f"file:{serial_log}",
            "EVSEL_LATE_US": str(args.evsel_late_us),
            "REAR_CONTROLS": "1" if args.rear_controls else "0",
            "ULOGIO": args.ulogio or "",
            "QEMU_EXTRA": " ".join(extra),
        })
        if submicro_frames:
            sm_path = Path(submicro_frames).resolve()
            env["SUBMICRO_FRAMES"] = str(sm_path)
            env["SUBMICRO_FRAMES_SHA"] = args.submicro_sha or digest(sm_path)
            env.pop("SUBMICRO_PAGE", None)
        else:
            env["SUBMICRO_PAGE"] = ""
            env.pop("SUBMICRO_FRAMES", None)

        t0 = time.monotonic()
        with stdout_log.open("wb") as out_fh:
            proc = subprocess.Popen(
                ["bash", str(run_sh)], cwd=str(PROJECT_ROOT), env=env,
                stdout=out_fh, stderr=subprocess.STDOUT, start_new_session=True,
            )
            screendump_at = max(1.0, args.timeout - 5.0)
            captured = False
            seq_info = None
            timed_out = False
            while True:
                rc = proc.poll()
                elapsed = time.monotonic() - t0
                if not captured and elapsed >= screendump_at and qmp_sock.exists():
                    captured = _qmp_screendump(qmp_sock, screen_ppm)
                    if table_va is not None:
                        seq_info = _qmp_read_sequencer(qmp_sock, table_va)
                if rc is not None:
                    break
                if elapsed >= args.timeout:
                    timed_out = True
                    if not captured and qmp_sock.exists():
                        captured = _qmp_screendump(qmp_sock, screen_ppm)
                        if table_va is not None:
                            seq_info = _qmp_read_sequencer(qmp_sock, table_va)
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    try:
                        rc = proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        rc = proc.wait()
                    break
                time.sleep(0.5)

        serial_text = serial_log.read_text(encoding="utf-8", errors="replace") if serial_log.is_file() else ""
        stdout_text = stdout_log.read_text(encoding="utf-8", errors="replace") if stdout_log.is_file() else ""
        parsed = parse_logs(serial_text, stdout_text, d_unimp=args.d_unimp)
        display = parse_ppm(screen_ppm)
        if parsed["boot"]["kernel_panic"]:
            status = "kernel_panic"
        elif parsed["boot"]["vector_exception"]:
            status = "vector_exception"
        elif rc == 0:
            status = "completed"
        elif timed_out:
            status = "timeout"
        else:
            status = "runner_error"

        receipt = {
            "schema_version": 1,
            "machine": machine,
            "target_id": manifest.get("target_id", "unknown"),
            "qemu_sha256": digest(qemu_bin) if qemu_bin.is_file() else None,
            "image_sha256": (outputs.get("emmc.img") or {}).get("sha256"),
            "avcam_sha256": (outputs.get("av-cam.bin") or {}).get("sha256"),
            "status": status,
            "wall_s": round(time.monotonic() - t0, 2),
            "boot": parsed["boot"],
            "display": display,
            "sequencer": {
                "step": seq_info["step"] if seq_info else None,
                "index_pa": hex(seq_info["index_pa"]) if seq_info else None,
                "table_va": hex(seq_info["table_va"]) if seq_info else None,
            },
            "unmodelled": parsed["unmodelled"],
        }

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="", choices=["", *sorted(TARGET_PROFILES)])
    parser.add_argument("--machine", default="")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--submicro-frames", default="")
    parser.add_argument("--submicro-sha", default="")
    parser.add_argument("--ulogio", default="")
    parser.add_argument("--timeout", type=float, default=150.0)
    parser.add_argument("--icount", default="shift=0,sleep=on")
    parser.add_argument("--evsel-late-us", type=int, default=500)
    parser.add_argument("--rear-controls", action="store_true", default=True)
    parser.add_argument("--no-rear-controls", dest="rear_controls", action="store_false")
    parser.add_argument("--d-unimp", action="store_true")
    parser.add_argument("--qemu-dir", default="")
    parser.add_argument("--screen-out", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if not args.target and not args.data_dir:
        parser.error("Provide --target or --data-dir")
    receipt = run_headless(args)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
