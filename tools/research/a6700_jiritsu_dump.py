"""ILCE-6700 (CXD90057) Jiritsu RAM-chunk partition acquisition.

Opt-in live tool. It enters Senser service mode through the pinned Sony-PMCA-RE
protocol, uploads a tiny worker script and an AArch64 loader through the
Jiritsu SET_FILE_PATH/SET_FILE2 commands, and then reads allowlisted
``/dev/nflasha*`` partitions with ``busybox dd`` into a single reused chunk
file on the camera's ``/tmp_wbshd`` tmpfs, fetching each chunk with GET_FILE.

Boundaries enforced here (see docs/evidence for the supporting record):
  * Only the two files ``/tmp/jiritsu/run.sh`` and ``/tmp/jiritsu/libJit.so`` and
    the control files in the nonce RAM directory are ever written. On this
    body ``/tmp/jiritsu`` is the ``/dev/nflasha6`` (Jiritsu, vfat) mount, so those two
    files are written to ``nflasha6`` for the duration of the session; both are removed.
  * Partition reads are read-only ``dd`` calls bounded to 4096-byte multiples.
    ``nflasha23`` is excluded; no raw write, format, firmware or factory
    command is ever sent.
  * One session, no retries of uncertain transfers, cleanup on every exit.
Nothing here is imported by the emulator; it needs a camera, WinUSB bindings
for 054C:0E76 and 054C:0336, and ``--confirm-helper-upload``.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import uuid

CHUNK_BYTES = 512 * 1024
MIN_MEMFREE_KIB = 8 * 1024  # the repository's documented camera memory floor
RUN_SH = "/tmp/jiritsu/run.sh"
LOADER = "/tmp/jiritsu/libJit.so"
RAM_PARENT = "/tmp_wbshd"
EXCLUDED = frozenset({"nflasha23"})
# Partition sizes in bytes from the ILCE-6700 2.00 partinf.conf (512-byte sectors).
PARTITIONS = {
    "nflasha1": 0xBF80 * 512, "nflasha2": 0xA000 * 512, "nflasha4": 0x6000 * 512,
    "nflasha6": 0xC800 * 512, "nflasha10": 0x6000 * 512, "nflasha11": 0x96000 * 512,
    "nflasha12": 0x14000 * 512, "nflasha13": 0x2000 * 512, "nflasha18": 0xA000 * 512,
    "nflasha22": 0x20800 * 512,
    # Present in the update; read only to cross-check the channel against the update image.
    "nflasha7": 0x8000 * 512,
}
# One launch per power cycle: libJiritsu dlopens the uploaded loader and the library stays
# mapped, so a second upload in the same boot does not run the constructor again.
CROSSCHECK_CHUNKS = {"nflasha7": 4}  # 2 MiB compared against the update image
DEFAULT_PARTITIONS = ("nflasha7", "nflasha1", "nflasha2", "nflasha4", "nflasha6", "nflasha10",
                      "nflasha11", "nflasha12", "nflasha13", "nflasha18", "nflasha22")
ZERO_SHA1 = hashlib.sha1(bytes(CHUNK_BYTES)).hexdigest()


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class JiritsuStatus(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Jiritsu status {status:#010x}")


def worker_script(ram_dir: str) -> bytes:
    if re.fullmatch(RAM_PARENT + r"/a6700-[0-9a-f]{32}", ram_dir) is None:
        raise ValueError("RAM directory must be a fresh nonce directory below /tmp_wbshd")
    return (
        "#!/bin/sh\n"
        f"R={ram_dir}\n"
        'exec >"$R.log" 2>&1\n'
        'busybox mkdir "$R" || exit 30\n'
        'printf "ready\\n" >"$R/ready.tmp" && busybox mv "$R/ready.tmp" "$R/ready" || exit 31\n'
        "while :; do\n"
        ' if [ -f "$R/stop" ]; then\n'
        f'  busybox rm -rf "$R" "$R.log"; busybox rm -f {LOADER} {RUN_SH}; exit 0\n'
        " fi\n"
        ' if [ -f "$R/input" ] && [ -f "$R/go" ]; then\n'
        '  busybox mv "$R/input" "$R/current" && busybox rm "$R/go" || exit 32\n'
        '  busybox rm -f "$R/output"\n'
        '  . "$R/current" >"$R/output.tmp" 2>&1\n'
        '  echo "__RC__:$?" >>"$R/output.tmp"\n'
        '  echo "__JOB__:$JOBID" >>"$R/output.tmp"\n'
        '  busybox mv "$R/output.tmp" "$R/output" && busybox rm "$R/current" || exit 33\n'
        " else\n"
        '  busybox sleep 0.1 || busybox sleep 1\n'
        " fi\n"
        "done\n"
    ).encode("ascii")


def chunk_job(partition: str, ram_dir: str, offset: int, count: int) -> bytes:
    if partition not in PARTITIONS or partition in EXCLUDED:
        raise ValueError(f"Partition not allowed: {partition}")
    if offset < 0 or count <= 0 or offset % 4096 or count % 4096 or count > CHUNK_BYTES:
        raise ValueError("Chunk range must be a positive 4096-byte multiple up to 512 KiB")
    if offset + count > PARTITIONS[partition]:
        raise ValueError("Chunk exceeds the partition")
    return (
        f"busybox rm -f {ram_dir}/chunk; "
        f"busybox dd if=/dev/{partition} of={ram_dir}/chunk bs=4096 "
        f"skip={offset // 4096} count={count // 4096} 2>&1 && "
        f"busybox sha1sum {ram_dir}/chunk && printf END >>{ram_dir}/chunk\n"
    ).encode("ascii")


INFO_JOB = (
    b"busybox cat /proc/uptime /proc/meminfo /proc/partitions /proc/mounts; "
    b"for p in /sys/class/block/nflasha*; do n=${p##*/}; printf '%s %s\\n' $n "
    b"$(busybox cat $p/size); done; busybox ls -l /dev/nflasha*; busybox df\n"
)


class Channel:
    """Senser Jiritsu exchanges over an authenticated 054C:0336 handle."""

    def __init__(self, senser_device, log):
        self.dev = senser_device
        self.log = log
        self.count = 0

    def exchange(self, code: int, body: bytes) -> bytes:
        if code not in (8, 15, 16):
            raise ValueError("Jiritsu command outside the allowlisted read/upload set")
        self.count += 1
        res, data = self.dev.sendSenserPacket(0x40, struct.pack("<HH", 0x1001, code) + body)
        data = bytes(data)
        if res != 0 or len(data) < 4:
            raise RuntimeError(f"Senser envelope refused request: res={res!r} len={len(data)}")
        status = struct.unpack_from("<I", data)[0]
        self.log.append({"t": now(), "code": code, "request": len(body),
                         "status": status, "reply": len(data) - 4})
        if status:
            raise JiritsuStatus(status)
        return data[4:]

    def read(self, path: str) -> bytes:
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path) or ".." in path:
            raise ValueError("Unsafe read path")
        return self.exchange(8, b"\x01\x00\x00/.." + path.encode("ascii") + b"\0")

    TRANSIENT = (0x10000006, 0x10000008, 0x1000000A)

    def read_retry(self, path: str, attempts: int = 8, delay: float = 0.25) -> bytes:
        """Idempotent read; these statuses are also returned for transient conditions."""
        for attempt in range(attempts):
            try:
                return self.read(path)
            except JiritsuStatus as error:
                if error.status not in self.TRANSIENT or attempt == attempts - 1:
                    raise
                time.sleep(delay * (attempt + 1))
        raise AssertionError("unreachable")

    def exists(self, path: str) -> bool:
        try:
            self.read(path)
            return True
        except JiritsuStatus as error:
            if error.status != 0x10000006:
                raise
            return False

    def write(self, path: str, data: bytes, ram_dir: str | None) -> None:
        owned = {RUN_SH, LOADER}
        if ram_dir:
            owned |= {f"{ram_dir}/input", f"{ram_dir}/go", f"{ram_dir}/stop"}
        if path not in owned or not data or len(data) > 16000:
            raise ValueError("Upload outside the owned control files")
        target = "/tmp/jiritsu/../.." + path if path.startswith(RAM_PARENT + "/") else path
        self.exchange(15, b"\0" + target.encode("ascii") + b"\0")
        self.exchange(16, b"\x00\xff\x00" + data)


def usb_modules(source: Path):
    sys.path.insert(0, str(source))
    import libusb_package  # noqa: PLC0415
    import usb.backend.libusb1 as libusb1  # noqa: PLC0415
    import usb.core  # noqa: PLC0415
    import usb.util  # noqa: PLC0415
    backend = libusb1.get_backend(find_library=libusb_package.find_library)
    return usb, backend


def enter_and_open(source: Path, events: list):
    usb, backend = usb_modules(source)
    from pmca.usb.driver.generic import GenericUsbDriver  # noqa: PLC0415
    from pmca.usb.driver.generic.libusb import UsbBackend  # noqa: PLC0415
    from pmca.usb.sony import SonySenserAuthDevice, SonySenserDevice  # noqa: PLC0415

    def pids():
        return {d.idProduct: d for d in usb.core.find(find_all=True, idVendor=0x054C, backend=backend)}

    def claim(device):
        try:
            device.set_configuration()
        except Exception as error:  # noqa: BLE001 - recorded, not fatal
            events.append({"t": now(), "event": f"set_configuration: {error!r}"})
        usb.util.claim_interface(device, 0)

    current = pids()
    events.append({"t": now(), "event": "start", "pids": sorted(f"{p:04X}" for p in current)})
    if 0x0E76 in current:
        device = current[0x0E76]
        claim(device)
        auth = SonySenserAuthDevice(GenericUsbDriver(UsbBackend(device)))
        auth.start()
        auth.authenticate()
        auth.stop()
        usb.util.dispose_resources(device)
        events.append({"t": now(), "event": "entry authentication OK"})
        for _ in range(60):
            time.sleep(0.5)
            if 0x0336 in pids():
                break
        time.sleep(2)
    device = usb.core.find(idVendor=0x054C, idProduct=0x0336, backend=backend)
    if device is None:
        raise RuntimeError("054C:0336 did not appear")
    claim(device)
    auth = SonySenserAuthDevice(GenericUsbDriver(UsbBackend(device)))
    auth.start()
    auth.authenticate()
    events.append({"t": now(), "event": "service authentication OK"})
    return usb, backend, device, auth, SonySenserDevice(auth.driver)


def parse_info(text: str) -> dict:
    meminfo = re.search(r"^MemFree:\s+(\d+) kB", text, re.M)
    # /proc/partitions lists 1-KiB blocks under the physical names pnflashaN.
    sizes = {"nflasha" + m.group(2): int(m.group(1)) * 1024
             for m in re.finditer(r"^\s*\d+\s+\d+\s+(\d+) pnflasha(\d+)$", text, re.M)}
    return {"memfree_kib": int(meminfo.group(1)) if meminfo else None, "sizes": sizes}


JOB_COUNTER = [0]


def run_job(channel: Channel, ram_dir: str, job: bytes, *, timeout: float = 90.0) -> str:
    """Run one shell fragment on the camera; the worker tags its output with the job id."""
    JOB_COUNTER[0] += 1
    job_id = JOB_COUNTER[0]
    channel.write(f"{ram_dir}/input", f"JOBID={job_id}\n".encode("ascii") + job, ram_dir)
    channel.write(f"{ram_dir}/go", b"1\n", ram_dir)
    deadline = time.monotonic() + timeout
    marker = f"__JOB__:{job_id}"
    while time.monotonic() < deadline:
        try:
            text = channel.read(f"{ram_dir}/output").decode("utf-8", "replace")
        except JiritsuStatus as error:
            if error.status != 0x10000006:
                raise
            text = ""
        if marker in text.split("\n"):
            return text
        time.sleep(0.1)
    raise TimeoutError("Camera job did not finish")


DIAG_TESTS = (
    ("txt", 4095), ("txt", 4097), ("txt", 65535), ("txt", 524291),
    ("txt", 4096), ("txt", 65536), ("txt", 524288),
    ("zero", 4096), ("zero", 65536),
    ("rand", 4096), ("rand", 65536),
    ("part0", 4096), ("part0", 65536), ("part100", 4096), ("part100", 65536),
)


def diagnose_reads(channel, ram_dir) -> list:
    """Create small known files in the RAM directory and record which GET_FILE reads succeed."""
    results = []
    for kind, size in DIAG_TESTS:
        name = f"{ram_dir}/t_{kind}_{size}"
        if kind == "txt":
            make = f"busybox dd if=/dev/zero bs={size} count=1 2>/dev/null | busybox tr '\\000' 'A' >{name}"
        elif kind == "zero":
            make = f"busybox dd if=/dev/zero of={name} bs={size} count=1 2>&1"
        elif kind == "rand":
            make = f"busybox dd if=/dev/urandom of={name} bs={size} count=1 2>&1"
        else:
            skip = int(kind[4:])
            make = f"busybox dd if=/dev/nflasha7 of={name} bs=4096 skip={skip} count={size // 4096} 2>&1"
        text = run_job(channel, ram_dir, (make + f"; busybox ls -l {name}\n").encode("ascii"))
        row = {"kind": kind, "size": size, "make": text[-160:]}
        try:
            data = channel.read_retry(name)
            row["read"] = {"bytes": len(data), "sha1": hashlib.sha1(data).hexdigest()}
        except JiritsuStatus as error:
            row["read"] = {"status": hex(error.status)}
        results.append(row)
    return results


def acquire(args) -> int:
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    events: list = []
    channel_log: list = []
    ram_dir = f"{RAM_PARENT}/a6700-{uuid.uuid4().hex}"
    report = {"tool": "a6700_jiritsu_dump", "started": now(), "events": events,
              "partitions": {}, "ram_dir": ram_dir}
    usb = device = auth = None
    uploaded = False
    channel = None
    try:
        usb, backend, device, auth, senser = enter_and_open(Path(args.pmca_source), events)
        channel = Channel(senser, channel_log)
        for path in (RUN_SH, LOADER):
            if channel.exists(path):
                raise RuntimeError(f"Pre-existing {path}; refusing to overwrite")
        script = worker_script(ram_dir)
        loader = Path(args.loader).read_bytes()
        if loader[:4] != b"\x7fELF" or loader[18:20] != b"\xb7\x00" or len(loader) > 16000:
            raise ValueError("Loader must be a small AArch64 ELF")
        uploaded = True
        channel.write(RUN_SH, script, ram_dir)
        if channel.read(RUN_SH) != script:
            raise RuntimeError("run.sh readback mismatch")
        channel.write(LOADER, loader, ram_dir)  # SET_FILE2 dlopens it, which starts run.sh
        events.append({"t": now(), "event": "loader uploaded", "loader_sha256": hashlib.sha256(loader).hexdigest()})
        deadline = time.monotonic() + 30
        while True:
            try:
                channel.read(f"{ram_dir}/ready")
                break
            except JiritsuStatus as error:
                if error.status != 0x10000006 or time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        events.append({"t": now(), "event": "worker ready"})

        info_text = run_job(channel, ram_dir, INFO_JOB)
        (out / "info.txt").write_text(info_text, encoding="utf-8")
        info = parse_info(info_text)
        report["info"] = info
        if info["memfree_kib"] is None or info["memfree_kib"] < MIN_MEMFREE_KIB:
            raise RuntimeError(f"MemFree below guard: {info['memfree_kib']}")
        if args.diagnose or args.diagnose_first:
            report["diagnose"] = diagnose_reads(channel, ram_dir)
            if args.diagnose:
                args.partitions = []
        for name in args.partitions:
            if name in EXCLUDED or name not in PARTITIONS:
                raise ValueError(f"Partition not allowed: {name}")
            size = info["sizes"].get(name)
            if size != PARTITIONS[name]:
                raise RuntimeError(f"{name}: live size {size} differs from partinf {PARTITIONS[name]}")
            limit = CROSSCHECK_CHUNKS.get(name, args.max_chunks)
            capture_partition(channel, ram_dir, name, size, out, report, limit)
    except Exception as error:  # noqa: BLE001 - recorded, cleanup follows
        report["failure"] = repr(error)
        events.append({"t": now(), "event": "failure", "error": repr(error)})
    finally:
        report["cleanup"] = cleanup(channel, ram_dir, uploaded)
        try:
            if auth is not None:
                auth.stop()
        except Exception as error:  # noqa: BLE001
            report["stop_error"] = repr(error)
        if usb is not None and device is not None:
            usb.util.dispose_resources(device)
        report["finished"] = now()
        report["channel_exchanges"] = channel_log
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 1 if "failure" in report else 0


STEP_STATE = {"bytes": CHUNK_BYTES}
MIN_STEP = 8 * 1024


def capture_partition(channel, ram_dir, name, size, out, report, max_chunks):
    target = out / f"{name}.bin"
    partial = out / f"{name}.bin.partial"
    digest = hashlib.sha256()
    entry = {"bytes": size, "zero_chunks": 0, "fetched_chunks": 0}
    report["partitions"][name] = entry
    offset = index = 0
    with partial.open("wb") as handle:
        while offset < size:
            if max_chunks is not None and index >= max_chunks:
                entry["truncated_by_max_chunks"] = True
                break
            count = min(STEP_STATE["bytes"], size - offset)
            text = run_job(channel, ram_dir, chunk_job(name, ram_dir, offset, count))
            match = re.search(r"^([0-9a-f]{40})  \S+/chunk$", text, re.M)
            records = re.search(r"^(\d+)\+(\d+) records out$", text, re.M)
            if (not match or "__RC__:0" not in text or not records
                    or (int(records.group(1)), int(records.group(2))) != (count // 4096, 0)):
                raise RuntimeError(f"{name}@{offset}: job failed: {text[-200:]!r}")
            if match.group(1) == hashlib.sha1(bytes(count)).hexdigest():
                data = bytes(count)
                entry["zero_chunks"] += 1
            else:
                try:
                    # The camera file carries a 3-byte "END" pad: every observed GET_FILE failure
                    # concerned a file whose size is a multiple of 4096.
                    padded = channel.read_retry(f"{ram_dir}/chunk")
                except JiritsuStatus as error:
                    if error.status not in Channel.TRANSIENT or STEP_STATE["bytes"] <= MIN_STEP:
                        raise
                    STEP_STATE["bytes"] //= 2
                    report.setdefault("step_reductions", []).append(
                        {"partition": name, "offset": offset, "failed_bytes": count,
                         "new_bytes": STEP_STATE["bytes"]})
                    continue  # same offset again; bounded by MIN_STEP halvings
                if padded[-3:] != b"END":
                    raise RuntimeError(f"{name}@{offset}: chunk pad missing")
                data = padded[:-3]
                entry["fetched_chunks"] += 1
                if len(data) != count or hashlib.sha1(data).hexdigest() != match.group(1):
                    raise RuntimeError(f"{name}@{offset}: transferred chunk differs from camera SHA-1")
            handle.write(data)
            digest.update(data)
            offset += count
            index += 1
    entry["sha256"] = digest.hexdigest()
    entry["chunk_bytes_at_end"] = STEP_STATE["bytes"]
    if not entry.get("truncated_by_max_chunks"):
        os.replace(partial, target)
        entry["file"] = target.name


def cleanup(channel, ram_dir, uploaded) -> dict:
    result = {"attempted": uploaded}
    if channel is None or not uploaded:
        return result
    try:
        channel.write(f"{ram_dir}/stop", b"1\n", ram_dir)
        time.sleep(2)
        result["run_sh_absent"] = not channel.exists(RUN_SH)
        result["loader_absent"] = not channel.exists(LOADER)
        result["ram_dir_absent"] = not channel.exists(f"{ram_dir}/ready")
    except Exception as error:  # noqa: BLE001
        result["error"] = repr(error)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--loader", required=True, help="AArch64 libJit.so loader")
    parser.add_argument("--pmca-source", required=True, help="Sony-PMCA-RE checkout at the pinned commit")
    parser.add_argument("--partitions", nargs="+", default=list(DEFAULT_PARTITIONS))
    parser.add_argument("--max-chunks", type=int, default=None,
                        help="Stop each partition after N chunks (staged qualification)")
    parser.add_argument("--diagnose-first", action="store_true",
                        help="Run the read diagnostics, then continue with the partitions")
    parser.add_argument("--diagnose", action="store_true",
                        help="Only test which GET_FILE reads of small RAM files succeed")
    parser.add_argument("--confirm-helper-upload", action="store_true",
                        help="Required: acknowledges the owned run.sh/libJit.so upload")
    args = parser.parse_args(argv)
    if not args.confirm_helper_upload:
        print("Refusing to contact the camera without --confirm-helper-upload", file=sys.stderr)
        return 2
    return acquire(args)


if __name__ == "__main__":
    raise SystemExit(main())
