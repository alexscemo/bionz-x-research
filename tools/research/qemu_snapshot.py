"""QEMU snapshot creation and instant-resume helper for sony-a6400.

Supports live QMP savevm/loadvm, cold-boot snapshot bundle capture into a
directory with a SHA-256-pinned manifest, and <1s instant resume via -loadvm.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from project_paths import PROJECT_ROOT
from tools.research.qemu_boot_probe import (
    Qmp,
    console_snapshot,
    guest_statuses,
    serial_readiness,
)
from tools.research.toolchain import toolchain_config

TAG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
HMP_ERROR_MARKERS = (
    "error",
    "does not exist",
    "does not have",
    "not found",
    "not writable",
    "could not",
    "failed",
    "unknown",
)


def validate_tag(tag: str) -> str:
    """Validate a QEMU savevm/loadvm snapshot tag."""
    if not tag or not TAG_RE.match(tag):
        raise ValueError(f"Invalid snapshot tag {tag!r}; expected 1..32 chars [A-Za-z0-9_.-]")
    return tag


def sha256_file(path: Path) -> str:
    """Compute SHA-256 of a file using 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def parse_hmp_result(command: str, output: object) -> str:
    """Validate QEMU human-monitor-command text output and raise on HMP errors."""
    text = output if isinstance(output, str) else ""
    low = text.lower()
    for marker in HMP_ERROR_MARKERS:
        if marker in low:
            raise RuntimeError(f"HMP command {command!r} failed: {text.strip()}")
    return text.strip()


def hmp(qmp: Qmp, command: str) -> str:
    """Execute an HMP command over QMP and validate its response."""
    reply = qmp.request("human-monitor-command", {"command-line": command})
    return parse_hmp_result(command, reply)


def save_live(qmp: Qmp, tag: str) -> dict[str, object]:
    """Save a VM snapshot inside a running QEMU instance."""
    tag = validate_tag(tag)
    t0 = time.monotonic()
    out = hmp(qmp, f"savevm {tag}")
    elapsed = round(time.monotonic() - t0, 3)
    listing = hmp(qmp, "info snapshots")
    if tag not in listing:
        raise RuntimeError(f"Snapshot {tag!r} missing from 'info snapshots': {listing}")
    return {"action": "savevm", "tag": tag, "seconds": elapsed, "output": out, "snapshots": listing}


def load_live(qmp: Qmp, tag: str) -> dict[str, object]:
    """Restore a VM snapshot inside a running QEMU instance."""
    tag = validate_tag(tag)
    t0 = time.monotonic()
    out = hmp(qmp, f"loadvm {tag}")
    elapsed = round(time.monotonic() - t0, 3)
    return {"action": "loadvm", "tag": tag, "seconds": elapsed, "output": out}


def default_qemu_dir() -> Path:
    version = toolchain_config()["qemu"]["version"]
    return Path(os.environ.get("A6400_QEMU_DIR", str(Path.home() / f"sony-a6400-qemu-{version}")))


def check_manifest(
    bundle_dir: Path,
    *,
    tag: str | None = None,
    expected_env: dict[str, str] | None = None,
    skip_hash_check: bool = False,
) -> tuple[dict, Path, str]:
    """Load and validate a snapshot bundle manifest fail-closed."""
    manifest_path = bundle_dir / "snapshot-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing snapshot manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError(f"Unsupported snapshot manifest schema: {manifest.get('schema_version')!r}")

    resolved_tag = validate_tag(tag or manifest.get("tag", ""))
    qcow_rel = manifest.get("snapshot_qcow2", "snapshot.qcow2")
    qcow_path = (bundle_dir / qcow_rel).resolve()
    if not qcow_path.is_file():
        raise FileNotFoundError(f"Missing snapshot qcow2: {qcow_path}")

    if expected_env:
        recorded_env = manifest.get("machine_env", {})
        for key, value in expected_env.items():
            if key in recorded_env and str(recorded_env[key]) != str(value):
                raise ValueError(
                    f"Snapshot machine option mismatch for {key}: "
                    f"recorded={recorded_env[key]!r}, requested={value!r}"
                )

    if not skip_hash_check:
        expected_sha = manifest.get("snapshot_qcow2_sha256")
        if not expected_sha:
            raise ValueError("Snapshot manifest missing snapshot_qcow2_sha256")
        actual_sha = sha256_file(qcow_path)
        if actual_sha != expected_sha:
            raise ValueError(
                f"Snapshot qcow2 SHA-256 mismatch: expected {expected_sha}, got {actual_sha}"
            )
    return manifest, qcow_path, resolved_tag


def _connect_qmp(qmp_sock: Path, proc: subprocess.Popen, deadline: float) -> Qmp:
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with code {proc.returncode}")
        try:
            return Qmp(qmp_sock, deadline)
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for QMP socket {qmp_sock}")


def _run_serial_commands(
    serial_sock: Path,
    commands: list[str],
    deadline: float,
    *,
    wait_for: str | None = None,
    need_shell: bool = True,
) -> tuple[str, dict[int, int]]:
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(1.0)
    while time.monotonic() < deadline:
        try:
            sock.connect(str(serial_sock))
            break
        except OSError:
            time.sleep(0.1)
    else:
        sock.close()
        raise TimeoutError(f"Timed out waiting for serial socket {serial_sock}")

    buf = bytearray()
    shell_seen = not need_shell
    marker_seen = wait_for is None
    sent = False
    sent_at = 0.0
    try:
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
                if chunk:
                    buf.extend(chunk)
            except TimeoutError:
                pass
            text = console_snapshot(bytes(buf))
            shell_seen, marker_seen = serial_readiness(text, shell_seen, marker_seen, wait_for)
            if shell_seen and marker_seen and not sent:
                if commands:
                    payload = "".join(
                        f"{cmd}; printf '__QEMU_STATUS_{idx}__:%d\\n' $?\n"
                        for idx, cmd in enumerate(commands)
                    ).encode("utf-8")
                    sock.sendall(payload)
                sent = True
                sent_at = time.monotonic()
            if sent:
                statuses = guest_statuses(text)
                if len(statuses) >= len(commands):
                    return text, statuses
                if not commands and time.monotonic() - sent_at >= 0.2:
                    return text, statuses
        text = console_snapshot(bytes(buf))
        raise TimeoutError(f"Timed out waiting for guest readiness/commands (last 400 chars: {text[-400:]!r})")
    finally:
        sock.close()


def _build_env(
    *,
    inputs: Path | None,
    run_dir: Path,
    qmp_sock: Path,
    serial_sock: Path,
    machine_env: dict[str, str],
    snapshot_qcow: Path | None = None,
    loadvm_tag: str | None = None,
) -> dict[str, str]:
    env = dict(os.environ)
    env["RUN_DIR"] = str(run_dir)
    env["TIMEOUT"] = "0"
    env["SERIAL"] = f"unix:{serial_sock},server=on,wait=off"
    timing = env.get("A6400_TIMING", "-icount shift=0,sleep=on")
    env["QEMU_EXTRA"] = f"{timing} -qmp unix:{qmp_sock},server=on,wait=off"
    if inputs is not None:
        env["A6400_QEMU_DATA_DIR"] = str(inputs)
    for key, value in machine_env.items():
        env[key] = value
    if snapshot_qcow is not None:
        env["SNAPSHOT_OVERLAY"] = str(snapshot_qcow)
    if loadvm_tag is not None:
        env["LOADVM"] = validate_tag(loadvm_tag)
    return env


def create_snapshot_bundle(args: argparse.Namespace) -> dict:
    """Boot QEMU to the requested readiness point, savevm, and write a manifest bundle."""
    bundle_dir = args.bundle_dir.resolve()
    if bundle_dir.exists() and any(bundle_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty bundle dir: {bundle_dir}")
    bundle_dir.mkdir(parents=True, exist_ok=True)
    tag = validate_tag(args.tag)

    machine_env: dict[str, str] = {}
    if args.rear_controls:
        machine_env["REAR_CONTROLS"] = "1"
    if args.liveview_source:
        machine_env["LIVEVIEW_SOURCE"] = args.liveview_source
    if args.evsel_late_us:
        machine_env["EVSEL_LATE_US"] = str(args.evsel_late_us)
    if args.ulogio is not None:
        machine_env["ULOGIO"] = args.ulogio
    if args.submicro_frames is not None:
        machine_env["SUBMICRO_FRAMES"] = str(args.submicro_frames)
    if args.submicro_frames_sha is not None:
        machine_env["SUBMICRO_FRAMES_SHA"] = args.submicro_frames_sha

    run_script = PROJECT_ROOT / "emulators/qemu-a6400" / "run.sh"
    qemu_bin = default_qemu_dir() / "build" / "qemu-system-arm"
    with tempfile.TemporaryDirectory(prefix="a6400-snap-create-") as tmp:
        tmp_path = Path(tmp)
        run_dir = tmp_path / "run"
        qmp_sock = tmp_path / "qmp.sock"
        serial_sock = tmp_path / "serial.sock"
        boot_log = bundle_dir / "boot.log"
        env = _build_env(
            inputs=args.inputs.resolve() if args.inputs else None,
            run_dir=run_dir,
            qmp_sock=qmp_sock,
            serial_sock=serial_sock,
            machine_env=machine_env,
        )
        t0 = time.monotonic()
        with boot_log.open("wb") as log_f:
            proc = subprocess.Popen(
                ["bash", str(run_script)],
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
            )
            qmp = None
            try:
                deadline = time.monotonic() + args.seconds
                qmp = _connect_qmp(qmp_sock, proc, deadline)
                serial_text, statuses = _run_serial_commands(
                    serial_sock,
                    args.guest_command,
                    deadline,
                    wait_for=args.wait_for,
                )
                save_info = save_live(qmp, tag)
                if args.screendump:
                    ppm = bundle_dir / "reference-lcd.ppm"
                    qmp.request("screendump", {"filename": str(ppm), "device": "vph", "head": 0})
                qmp.quit(proc, timeout=15)
            finally:
                if qmp is not None:
                    qmp.close()
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=10)
        elapsed = round(time.monotonic() - t0, 3)
        overlay = run_dir / "emmc-overlay.qcow2"
        if not overlay.is_file():
            raise RuntimeError(f"Expected overlay missing after savevm: {overlay}")
        dst_qcow = bundle_dir / "snapshot.qcow2"
        shutil.copy2(overlay, dst_qcow)
        (bundle_dir / "serial.log").write_text(serial_text, encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "tag": tag,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "boot_elapsed_s": elapsed,
        "save_seconds": save_info["seconds"],
        "inputs_dir": str(args.inputs.resolve()) if args.inputs else "",
        "qemu_binary": str(qemu_bin),
        "qemu_binary_sha256": sha256_file(qemu_bin) if qemu_bin.is_file() else "",
        "machine_env": machine_env,
        "wait_for": args.wait_for,
        "guest_statuses": statuses,
        "snapshot_qcow2": "snapshot.qcow2",
        "snapshot_qcow2_sha256": sha256_file(dst_qcow),
        "snapshot_qcow2_bytes": dst_qcow.stat().st_size,
    }
    (bundle_dir / "snapshot-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def resume_snapshot_bundle(args: argparse.Namespace) -> dict:
    """Resume QEMU from an existing snapshot bundle in <1s and run optional commands."""
    report_dir = args.report_dir.resolve()
    if report_dir.exists() and any(report_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty report dir: {report_dir}")
    report_dir.mkdir(parents=True, exist_ok=True)

    manifest, qcow_path, tag = check_manifest(
        args.bundle_dir.resolve(),
        tag=args.tag,
        skip_hash_check=args.skip_hash_check,
    )
    machine_env = dict(manifest.get("machine_env", {}))
    inputs_str = manifest.get("inputs_dir") or ""
    inputs_path = Path(inputs_str) if inputs_str else None
    run_script = PROJECT_ROOT / "emulators/qemu-a6400" / "run.sh"

    with tempfile.TemporaryDirectory(prefix="a6400-snap-resume-") as tmp:
        tmp_path = Path(tmp)
        run_dir = tmp_path / "run"
        qmp_sock = tmp_path / "qmp.sock"
        serial_sock = tmp_path / "serial.sock"
        resume_log = report_dir / "qemu.log"
        env = _build_env(
            inputs=inputs_path,
            run_dir=run_dir,
            qmp_sock=qmp_sock,
            serial_sock=serial_sock,
            machine_env=machine_env,
            snapshot_qcow=qcow_path,
            loadvm_tag=tag,
        )
        t0 = time.monotonic()
        with resume_log.open("wb") as log_f:
            proc = subprocess.Popen(
                ["bash", str(run_script)],
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
            )
            qmp = None
            try:
                deadline = time.monotonic() + args.seconds
                qmp = _connect_qmp(qmp_sock, proc, deadline)
                qmp_ready_s = round(time.monotonic() - t0, 3)
                serial_text, statuses = _run_serial_commands(
                    serial_sock,
                    args.guest_command,
                    deadline,
                    need_shell=False,
                )
                if args.screendump:
                    ppm = report_dir / "resumed-lcd.ppm"
                    qmp.request("screendump", {"filename": str(ppm), "device": "vph", "head": 0})
                qmp.quit(proc, timeout=15)
            finally:
                if qmp is not None:
                    qmp.close()
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=10)
        total_s = round(time.monotonic() - t0, 3)

    (report_dir / "serial.log").write_text(serial_text, encoding="utf-8")
    report = {
        "bundle_dir": str(args.bundle_dir.resolve()),
        "tag": tag,
        "qmp_ready_seconds": qmp_ready_s,
        "total_seconds": total_s,
        "guest_statuses": statuses,
        "serial_bytes": len(serial_text.encode("utf-8")),
    }
    (report_dir / "resume-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Boot QEMU to a checkpoint and capture a snapshot bundle")
    c.add_argument("--inputs", type=Path, default=None, help="Prepared inputs directory")
    c.add_argument("--bundle-dir", type=Path, required=True, help="New output directory")
    c.add_argument("--tag", default="ready", help="Snapshot tag (default: ready)")
    c.add_argument("--wait-for", default=None, help="Serial marker to wait for before saving")
    c.add_argument("--seconds", type=float, default=180.0, help="Timeout in seconds")
    c.add_argument("--rear-controls", action="store_true")
    c.add_argument("--liveview-source", default="")
    c.add_argument("--evsel-late-us", type=int, default=0)
    c.add_argument("--ulogio", default=None)
    c.add_argument("--submicro-frames", default=None)
    c.add_argument("--submicro-frames-sha", default=None)
    c.add_argument("--guest-command", action="append", default=[])
    c.add_argument("--screendump", action="store_true")

    r = sub.add_parser("resume", help="Instant-resume from a snapshot bundle")
    r.add_argument("--bundle-dir", type=Path, required=True)
    r.add_argument("--report-dir", type=Path, required=True)
    r.add_argument("--tag", default=None)
    r.add_argument("--seconds", type=float, default=60.0)
    r.add_argument("--guest-command", action="append", default=[])
    r.add_argument("--screendump", action="store_true")
    r.add_argument("--skip-hash-check", action="store_true")

    sl = sub.add_parser("save-live", help="Save a snapshot on a live QMP socket")
    sl.add_argument("--qmp", type=Path, required=True)
    sl.add_argument("--tag", default="quick")

    ll = sub.add_parser("load-live", help="Restore a snapshot on a live QMP socket")
    ll.add_argument("--qmp", type=Path, required=True)
    ll.add_argument("--tag", default="quick")

    args = parser.parse_args(argv)
    if args.cmd == "create":
        out = create_snapshot_bundle(args)
    elif args.cmd == "resume":
        out = resume_snapshot_bundle(args)
    elif args.cmd == "save-live":
        qmp = Qmp(args.qmp, time.monotonic() + 30.0)
        try:
            out = save_live(qmp, args.tag)
        finally:
            qmp.close()
    elif args.cmd == "load-live":
        qmp = Qmp(args.qmp, time.monotonic() + 30.0)
        try:
            out = load_live(qmp, args.tag)
        finally:
            qmp.close()
    else:
        raise SystemExit(2)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
