"""Live 4-core QEMU sony-a6400 end-to-end verification of the reversible EmuNAND prototype.

Runs strictly inside WSL/Linux against QEMU (never accesses USB or physical hardware):
  1. Verifies base emmc.img SHA-256 before and after the session.
  2. Inserts a 16 MiB exFAT virtual SD card containing CFW_VDISK.IMG (ext2) at reset
     via QMP, boots the captured 4-core ILCE-6400 Linux 3.0.27_nl-rt106+ firmware
     with SUBMICRO_SYNTHETIC=1 and MISCREG=1 so Sony's mmc_drv.ko registers
     mmca10 / mmca10p1 (/dev/mmca1, 250:11).
  3. Activates emunand_session.sh start: loads emunand_guard.ko, verifies Backup_save=0,
     attaches CFW_VDISK.IMG zero-copy read-only over /dev/loop0 without mounting
     /dev/mmca1 in Linux VFS, and bind-mounts /setting/kemco.txt from the SD VDisk.
  4. Snapshots QMP query-blockstats BEFORE and AFTER writing to /setting (vfat),
     /tmp (vfat), raw /dev/nflasha4 (MPR), and raw /dev/nflashaB0 (boot0), proving
     all reads return the new CoW bytes while 0 physical write operations/bytes reach
     either emulated eMMC or the SD card.
  5. Executes emunand_session.sh stop (live 4-core SMP reversibility): verifies
     bind-mounts are removed, stock /setting/kemco.txt and EXBL on nflashaB0 are intact,
     Backup_save is restored to 1, and emunand_guard.ko unloads cleanly via rmmod.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tarfile
import time
import uuid


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class SimpleQmp:
    def __init__(self, sock_path: Path) -> None:
        for attempt in range(50):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(10.0)
            try:
                self.sock.connect(str(sock_path))
                break
            except (ConnectionRefusedError, FileNotFoundError):
                self.sock.close()
                if attempt == 49:
                    raise
                time.sleep(0.1)
        self.fp = self.sock.makefile("rwb", buffering=0)
        self.ident = 0
        greeting = self._read_json()
        if "QMP" not in greeting:
            raise RuntimeError(f"Unexpected QMP greeting: {greeting}")
        self.greeting = greeting
        self.request("qmp_capabilities")

    def _read_json(self) -> dict:
        while True:
            line = self.fp.readline(65536)
            if not line:
                raise RuntimeError("QMP connection closed unexpectedly")
            msg = json.loads(line.decode("utf-8"))
            if "event" in msg:
                continue
            return msg

    def request(self, execute: str, arguments: dict | None = None) -> object:
        self.ident += 1
        req: dict[str, object] = {"execute": execute, "id": self.ident}
        if arguments is not None:
            req["arguments"] = arguments
        self.fp.write((json.dumps(req) + "\n").encode("utf-8"))
        while True:
            msg = self._read_json()
            if msg.get("id") != self.ident:
                continue
            if "error" in msg:
                raise RuntimeError(f"QMP {execute} failed: {msg['error']}")
            return msg["return"]

    def close(self) -> None:
        try:
            self.fp.close()
        finally:
            self.sock.close()


def insert_sd_via_qmp(qmp: SimpleQmp) -> None:
    drives = [d for d in qmp.request("query-block") if d.get("device") == "a6400-sd"]
    if len(drives) != 1:
        raise RuntimeError(f"Expected 1 a6400-sd block device, got {drives}")
    qdev = drives[0]["qdev"]
    qmp.request("blockdev-insert-medium", {"id": qdev, "node-name": "a6400-sd-image"})


def block_write_totals(qmp: SimpleQmp) -> dict[str, dict[str, int]]:
    stats = qmp.request("query-blockstats")
    out: dict[str, dict[str, int]] = {}
    for entry in stats:
        dev = entry.get("device") or entry.get("node-name") or "unknown"
        s = entry.get("stats", {})
        out[str(dev)] = {
            "wr_operations": int(s.get("wr_operations", 0)),
            "wr_bytes": int(s.get("wr_bytes", 0)),
            "rd_operations": int(s.get("rd_operations", 0)),
            "rd_bytes": int(s.get("rd_bytes", 0)),
        }
    return out


def _resolve_submicro_fixture(repo_root: Path) -> Path:
    env_submicro = os.environ.get("SUBMICRO_FRAMES")
    if env_submicro:
        return Path(os.path.expandvars(env_submicro)).expanduser()
    candidates = [
        repo_root / "emulators/qemu-a6400" / "fixtures" / "powered-submicro.bin",
        *sorted(Path.home().glob("a6400-integrated-liveview*/fixtures/powered-submicro.bin")),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Set SUBMICRO_FRAMES to powered-submicro.bin")


def make_bundle_tar(bundle_dir: Path, out_tar: Path, extra_scripts: dict[str, str] | None = None) -> None:
    buf = io.BytesIO()
    files = [
        ("emunand_guard.ko", 0o644, (bundle_dir / "emunand_guard.ko").read_bytes()),
        ("emunand_vdisk_ctl.elf", 0o755, (bundle_dir / "emunand_vdisk_ctl.elf").read_bytes()),
        ("emunand_session.sh", 0o755, (bundle_dir / "emunand_session.sh").read_bytes()),
    ]
    if extra_scripts:
        for sname, body in sorted(extra_scripts.items()):
            files.append((sname, 0o755, body.replace("\r\n", "\n").encode("utf-8")))
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, mode, data in files:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = mode
            tf.addfile(ti, io.BytesIO(data))
    out_tar.write_bytes(buf.getvalue())


def run_e2e(
    *,
    repo_root: Path,
    bundle_dir: Path,
    sd_src_path: Path,
    vdisk_sha256: str,
    inputs_dir: Path,
    qemu_dir: Path,
    report_dir: Path,
    timeout_sec: int = 150,
) -> dict[str, object]:
    emmc_path = inputs_dir / "emmc.img"
    emmc_sha_before = sha256_file(emmc_path)

    work_tmp = Path(f"/tmp/emunand-qemu-{uuid.uuid4().hex[:12]}")
    work_tmp.mkdir(parents=True, exist_ok=False)
    sd_run_path = work_tmp / "sd_exfat.img"
    shutil.copyfile(sd_src_path, sd_run_path)
    sd_sha_before = sha256_file(sd_run_path)

    pkg_tar = work_tmp / "emunand.tar"
    make_bundle_tar(bundle_dir, pkg_tar)

    session_dir = Path.home() / "a6400-runs" / f"emunand-{uuid.uuid4().hex[:12]}"
    session_dir.mkdir(parents=True, exist_ok=False)
    run_dir = session_dir / "machine"
    qmp_path = run_dir / "qmp.sock"
    console_log = session_dir / "console.log"

    submicro_path = _resolve_submicro_fixture(repo_root)
    submicro_sha = sha256_file(submicro_path)

    env = dict(
        os.environ,
        A6400_QEMU_DIR=str(qemu_dir),
        A6400_QEMU_DATA_DIR=str(inputs_dir),
        RUN_DIR=str(run_dir),
        TIMEOUT="0",
        ULOGIO="",
        SUBMICRO_FRAMES=str(submicro_path),
        SUBMICRO_FRAMES_SHA=submicro_sha,
        SUBMICRO_LENS_POWER="1",
        CLKRST_MODEL="1",
        MISCREG="1",
        SD_IMAGE=str(sd_run_path),
        SD_SHA256=sd_sha_before,
        SETTING_FILES=f"{pkg_tar}:emunand.tar",
        QEMU_EXTRA=f"-icount shift=0,sleep=on -net none -no-reboot -S -qmp unix:{qmp_path},server=on,wait=off",
    )

    proc: subprocess.Popen[bytes] | None = None
    qmp: SimpleQmp | None = None
    deadline = time.monotonic() + timeout_sec

    def read_console() -> str:
        if not console_log.exists():
            return ""
        return console_log.read_bytes().decode("utf-8", errors="replace")

    def run_guest_cmd(tag: str, cmd: str, cmd_timeout: float = 45.0) -> tuple[int, str]:
        assert proc is not None and proc.stdin is not None
        begin_m = f"__BEGIN_{tag}__"
        end_m = f"__END_{tag}__:"
        full = f"echo {begin_m}\n{cmd}\necho {end_m}$?\n"
        for b in full.encode("utf-8"):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Global deadline exceeded sending {tag}")
            proc.stdin.write(bytes([b]))
            proc.stdin.flush()
            time.sleep(0.005)
        t_end = min(deadline, time.monotonic() + cmd_timeout)
        pat = re.compile(rf"(?<!echo ){re.escape(begin_m)}[^\n]*\n(.*?){re.escape(end_m)}(\d+)\r?\n", re.DOTALL)
        while time.monotonic() < t_end:
            if proc.poll() is not None:
                raise RuntimeError(f"QEMU exited prematurely ({proc.returncode}) during {tag}:\n{read_console()[-2000:]}")
            txt = read_console()
            m = pat.search(txt)
            if m:
                return int(m.group(2)), m.group(1)
            time.sleep(0.15)
        raise TimeoutError(f"Timed out waiting for guest command {tag}:\n{read_console()[-2500:]}")

    try:
        with console_log.open("wb") as log_fh:
            proc = subprocess.Popen(
                ["bash", str(repo_root / "emulators/qemu-a6400/run.sh")],
                cwd=str(repo_root),
                env=env,
                stdin=subprocess.PIPE,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

            while not qmp_path.exists():
                if proc.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"QMP socket failed to appear (rc={proc.poll()}):\n{read_console()}")
                time.sleep(0.1)

            try:
                qmp = SimpleQmp(qmp_path)
            except Exception as exc:
                raise RuntimeError(f"QMP connect failed (rc={proc.poll()}): {exc}\n{read_console()}") from exc
            insert_sd_via_qmp(qmp)
            qmp.request("cont")
            assert proc.stdin is not None
            proc.stdin.write(b"\n")
            proc.stdin.flush()

            # Wait for /bin/ash shell readiness AND im.elf HeapAllocator startup
            while time.monotonic() < deadline:
                txt = read_console()
                if ("built-in shell (ash)" in txt.lower() or "/ # " in txt) and "Created HeapAllocator:" in txt:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(f"QEMU exited during boot:\n{txt[-2000:]}")
                time.sleep(0.25)
            else:
                raise TimeoutError(f"Guest did not reach ash + Created HeapAllocator:\n{read_console()[-2000:]}")

            # Silence kernel printk spam on ttyAM0 so command outputs are never split mid-token
            run_guest_cmd("QUIET_PRINTK", "echo 0 > /proc/sys/kernel/printk", cmd_timeout=20.0)

            # Wait for mmc_drv.ko to register mmca10p1 (/dev/mmca1 250:11)
            rc_wait, out_wait = run_guest_cmd(
                "WAIT_SD",
                "i=0; while ! /bin/busybox grep -q 'mmca10p1' /proc/partitions; do "
                "i=$((i+1)); [ $i -lt 40 ] || exit 9; /bin/busybox sleep 1; done; "
                "/bin/busybox cat /proc/partitions",
                cmd_timeout=55.0,
            )
            if rc_wait != 0:
                raise RuntimeError(f"mmca10p1 was not registered by mmc_drv.ko (rc={rc_wait}):\n{out_wait}")

            # Phase 1: Verify stock state, unpack bundle to tmpfs, and activate EmuNAND
            rc_p1, out_p1 = run_guest_cmd(
                "PHASE1_START",
                "echo KEMCO_BEFORE=$(/bin/busybox dd if=/setting/kemco.txt bs=5 count=1 2>/dev/null); "
                "echo BSAVE_BEFORE=$(/bin/busybox cat /sys/module/backup/parameters/Backup_save); "
                "/bin/busybox mount -t tmpfs -o size=8192k tmpfs /tmp_bt 2>/dev/null || true; "
                "/bin/busybox mkdir -p /tmp_bt/pkg; "
                "/bin/busybox tar -xf /setting/emunand.tar -C /tmp_bt/pkg; "
                "/bin/sh /tmp_bt/pkg/emunand_session.sh start /tmp_bt/pkg --dev /dev/mmca1 --sha256 " + vdisk_sha256 + "; "
                "echo KEMCO_NOW=$(/bin/busybox cat /setting/kemco.txt); "
                "echo PAYLOAD_NOW=$(/bin/busybox cat /tmp_bt/emunand/mnt/usr_payload.txt); "
                "echo BSAVE_NOW=$(/bin/busybox cat /sys/module/backup/parameters/Backup_save); "
                "/bin/busybox sync",
                cmd_timeout=45.0,
            )
            if rc_p1 != 0:
                raise RuntimeError(f"Phase 1 activation failed (rc={rc_p1}):\n{out_p1}")

            # Snapshot QMP hardware block write stats BEFORE internal flash writes
            stats_before = block_write_totals(qmp)

            # Phase 2: Perform internal flash writes across /setting (nflasha2), /tmp (nflasha10),
            # raw /dev/nflasha4 (MPR), and raw /dev/nflashaB0 (boot0), then sync and read back!
            rc_p2, out_p2 = run_guest_cmd(
                "PHASE2_COW_WRITES",
                "(echo COW_MPR_RAW_OK; /bin/busybox dd if=/dev/zero bs=512 count=1 2>/dev/null) | "
                "/bin/busybox dd of=/tmp_bt/mpr512.bin bs=512 count=1 2>/dev/null; "
                "(echo FAKE_B0_HEADER; /bin/busybox dd if=/dev/zero bs=512 count=1 2>/dev/null) | "
                "/bin/busybox dd of=/tmp_bt/b0_512.bin bs=512 count=1 2>/dev/null; "
                "echo COW_SETTING_OK > /setting/cow_probe.txt; "
                "echo COW_TMP_OK > /tmp/cow_tmp.txt; "
                "/bin/busybox dd if=/tmp_bt/mpr512.bin of=/dev/nflasha4 bs=512 count=1; "
                "/bin/busybox dd if=/tmp_bt/b0_512.bin of=/dev/nflashaB0 bs=512 count=1; "
                "/bin/busybox rm -f /tmp_bt/mpr512.bin /tmp_bt/b0_512.bin; "
                "/bin/busybox sync; "
                "echo READ_SETTING=$(/bin/busybox cat /setting/cow_probe.txt); "
                "echo READ_TMP=$(/bin/busybox cat /tmp/cow_tmp.txt); "
                "echo READ_MPR=$(/bin/busybox dd if=/dev/nflasha4 bs=14 count=1 2>/dev/null); "
                "echo READ_B0=$(/bin/busybox dd if=/dev/nflashaB0 bs=14 count=1 2>/dev/null); "
                "/bin/busybox cat /proc/emunand_status",
                cmd_timeout=45.0,
            )
            if rc_p2 != 0:
                raise RuntimeError(f"Phase 2 CoW write test failed (rc={rc_p2}):\n{out_p2}")

            # Snapshot QMP hardware block write stats AFTER internal flash writes + sync
            stats_after = block_write_totals(qmp)

            # Phase 3: Clear CoW, drop clean/dirty page cache, and execute full reversible stop
            rc_p3, out_p3 = run_guest_cmd(
                "PHASE3_REVERSIBLE_STOP",
                "echo clear_cow > /proc/emunand_status; "
                "/bin/busybox sync; "
                "echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true; "
                "/bin/sh /tmp_bt/pkg/emunand_session.sh stop; "
                "echo KEMCO_AFTER=$(/bin/busybox dd if=/setting/kemco.txt bs=5 count=1 2>/dev/null); "
                "echo MPR_AFTER=$(/bin/busybox dd if=/dev/nflasha4 bs=5 count=1 2>/dev/null); "
                "echo B0_AFTER=$(/bin/busybox dd if=/dev/nflashaB0 bs=4 count=1 2>/dev/null); "
                "echo BSAVE_AFTER=$(/bin/busybox cat /sys/module/backup/parameters/Backup_save); "
                "if /bin/busybox grep -q '^emunand_guard ' /proc/modules; then echo MOD_STILL_LOADED=1; else echo MOD_STILL_LOADED=0; fi; "
                "if [ -e /tmp_bt/emunand ]; then echo WORK_LEFT=1; else echo WORK_LEFT=0; fi",
                cmd_timeout=45.0,
            )
            if rc_p3 != 0:
                raise RuntimeError(f"Phase 3 reversible stop failed (rc={rc_p3}):\n{out_p3}")

            qmp.request("quit")
            proc.wait(timeout=10)
    finally:
        if qmp is not None:
            try:
                qmp.close()
            except Exception:
                pass
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    emmc_sha_after = sha256_file(emmc_path)
    sd_sha_after = sha256_file(sd_run_path)

    report_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(console_log, report_dir / "console.log")

    # Compute exact QMP block write deltas across Phase 2
    deltas: dict[str, dict[str, int]] = {}
    for dev, after_s in stats_after.items():
        before_s = stats_before.get(dev, {"wr_operations": 0, "wr_bytes": 0, "rd_operations": 0, "rd_bytes": 0})
        deltas[dev] = {
            k: after_s[k] - before_s[k]
            for k in ("wr_operations", "wr_bytes", "rd_operations", "rd_bytes")
        }

    # Cleanup temporary WSL directories
    shutil.rmtree(work_tmp, ignore_errors=True)
    shutil.rmtree(session_dir, ignore_errors=True)

    assertions = {
        "phase1_zero_copy_ro": "ATTACHED mode=zero_copy_ro fstype=ext2" in out_p1,
        "phase1_active": "EMUNAND_ACTIVE" in out_p1,
        "phase1_bind_override": "KEMCO_NOW=EMUNAND_VDISK_KEMCO_OVERRIDE_V1" in out_p1,
        "phase1_ext2_payload": "PAYLOAD_NOW=EMUNAND_EXT2_PAYLOAD_OK" in out_p1,
        "phase1_backup_save_zeroed": "BSAVE_BEFORE=1" in out_p1 and "BSAVE_NOW=0" in out_p1,
        "phase2_cow_setting_readback": "READ_SETTING=COW_SETTING_OK" in out_p2,
        "phase2_cow_tmp_readback": "READ_TMP=COW_TMP_OK" in out_p2,
        "phase2_cow_mpr_readback": "READ_MPR=COW_MPR_RAW_OK" in out_p2,
        "phase2_cow_b0_readback": "READ_B0=FAKE_B0_HEADER" in out_p2,
        "phase2_boot_writes_blocked": "boot_writes_blocked=0" not in out_p2 and "boot_writes_blocked=" in out_p2,
        "phase2_zero_physical_writes_all_drives": all(v["wr_operations"] == 0 and v["wr_bytes"] == 0 for v in deltas.values()),
        "phase3_stopped": "EMUNAND_STOPPED" in out_p3,
        "phase3_kemco_restored": "KEMCO_BEFORE=kemco" in out_p1 and "KEMCO_AFTER=kemco" in out_p3,
        "phase3_mpr_cmme_intact": "MPR_AFTER=CMMeX" in out_p3,
        "phase3_b0_exbl_intact": "B0_AFTER=EXBL" in out_p3,
        "phase3_backup_save_restored": "BSAVE_AFTER=1" in out_p3,
        "phase3_module_unloaded": "MOD_STILL_LOADED=0" in out_p3,
        "phase3_workspace_cleaned": "WORK_LEFT=0" in out_p3,
        "emmc_base_sha256_unchanged": emmc_sha_before == emmc_sha_after,
        "sd_card_sha256_unchanged": sd_sha_before == sd_sha_after,
    }
    failed = [k for k, ok in assertions.items() if not ok]

    receipt = {
        "status": "PASS" if not failed else "FAIL",
        "failed_assertions": failed,
        "assertions": assertions,
        "emmc_sha256_before": emmc_sha_before,
        "emmc_sha256_after": emmc_sha_after,
        "sd_exfat_sha256_before": sd_sha_before,
        "sd_exfat_sha256_after": sd_sha_after,
        "vdisk_sha256": vdisk_sha256,
        "qmp_blockstats_before_cow": stats_before,
        "qmp_blockstats_after_cow": stats_after,
        "qmp_blockstats_phase2_delta": deltas,
        "phase1_output": out_p1,
        "phase2_output": out_p2,
        "phase3_output": out_p3,
    }
    (report_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if failed:
        raise RuntimeError(f"QEMU E2E verification failed assertions {failed}:\n{json.dumps(receipt, indent=2)}")
    return receipt


def run_packages_e2e(
    *,
    repo_root: Path,
    bundle_dir: Path,
    sd_src_path: Path,
    vdisk_sha256: str,
    inputs_dir: Path,
    qemu_dir: Path,
    report_dir: Path,
    timeout_sec: int = 180,
) -> dict[str, object]:
    """Verify mounting flashable kernel (nflasha3/nflasha1) and Custom-OS (ext2/nflasha7) packages from SD."""
    emmc_path = inputs_dir / "emmc.img"
    emmc_sha_before = sha256_file(emmc_path)

    work_tmp = Path(f"/tmp/emunand-pkg-{uuid.uuid4().hex[:12]}")
    work_tmp.mkdir(parents=True, exist_ok=False)
    sd_run_path = work_tmp / "sd_flashpkg_64m.img"
    shutil.copyfile(sd_src_path, sd_run_path)
    sd_sha_before = sha256_file(sd_run_path)

    p1_sh = f"""#!/bin/sh
set -eu
echo STOCK_KVER=$(/bin/busybox dd if=/system/vmlinux.bin bs=4096 skip=704 count=1 2>/dev/null | /bin/busybox dd bs=1 skip=40 count=140 2>/dev/null)
echo STOCK_ROOTVER=$(/bin/busybox cat /version.txt)
wbi_rc=0
/tmp_bt/pkg/emunand_vdisk_ctl.elf inspect --fast --dev /dev/mmca1 --image WBI_RAW.IMG || wbi_rc=$?
echo WBI_REJECT_RC=$wbi_rc
/bin/sh /tmp_bt/pkg/emunand_session.sh start /tmp_bt/pkg --dev /dev/mmca1 --sha256 {vdisk_sha256} \\
    --extra-pkg NFLASH3.IMG:boot_pkg:1 \\
    --extra-pkg NFLASH1.IMG:upd_pkg:2 \\
    --extra-pkg NFLASH7.IMG:rootfs_pkg:5
echo MOUNTED_SYS_KVER=$(/bin/busybox dd if=/system/vmlinux.bin bs=4096 skip=713 count=1 2>/dev/null | /bin/busybox dd bs=1 skip=40 count=140 2>/dev/null)
echo MOUNTED_SYS_FILES=$(/bin/busybox ls /system)
echo MOUNTED_ROOTFS_VER=$(/bin/busybox cat /tmp_bt/emunand/rootfs_pkg/version.txt)
echo CHROOT_OUT=$(/bin/busybox chroot /tmp_bt/emunand/mnt /sbin/custom_os_probe.elf)
"""
    p2_sh = """#!/bin/sh
set -eu
/bin/busybox mkdir -p /tmp_bt/emunand/nested_main /tmp_bt/emunand/nested_upd
/tmp_bt/emunand/emunand_vdisk_ctl.elf attach-file --file /system/initrd.img --loop /tmp_bt/emunand/loop3
/bin/busybox mount -t ext2 -o ro /tmp_bt/emunand/loop3 /tmp_bt/emunand/nested_main
/tmp_bt/emunand/emunand_vdisk_ctl.elf attach-file --file /tmp_bt/emunand/upd_pkg/boot/initrd.img --loop /tmp_bt/emunand/loop4
/bin/busybox mount -t cramfs -o ro /tmp_bt/emunand/loop4 /tmp_bt/emunand/nested_upd
if [ -x /tmp_bt/emunand/nested_main/sbin/init ] && [ -f /tmp_bt/emunand/nested_main/kmod/emmc.ko ]; then
    echo NESTED_MAIN_INIT=OK
else
    echo NESTED_MAIN_INIT=MISSING
fi
echo NESTED_UPD_LIST=$(/bin/busybox ls /tmp_bt/emunand/nested_upd)
echo RUNNING_UNAME=$(/bin/busybox cat /proc/version)
echo PKG_COW_TEST > /setting/pkg_cow.txt
/bin/busybox sync
echo COW_READ=$(/bin/busybox cat /setting/pkg_cow.txt)
/bin/busybox umount /tmp_bt/emunand/nested_upd
/tmp_bt/emunand/emunand_vdisk_ctl.elf detach --loop /tmp_bt/emunand/loop4 >/dev/null
/bin/busybox umount /tmp_bt/emunand/nested_main
/tmp_bt/emunand/emunand_vdisk_ctl.elf detach --loop /tmp_bt/emunand/loop3 >/dev/null
/bin/busybox rmdir /tmp_bt/emunand/nested_upd /tmp_bt/emunand/nested_main
/bin/busybox cat /proc/emunand_status
"""
    p3_sh = """#!/bin/sh
set -eu
/bin/sh /tmp_bt/pkg/emunand_session.sh stop
echo RESTORED_SYS_KVER=$(/bin/busybox dd if=/system/vmlinux.bin bs=4096 skip=704 count=1 2>/dev/null | /bin/busybox dd bs=1 skip=40 count=140 2>/dev/null)
echo BSAVE_AFTER=$(/bin/busybox cat /sys/module/backup/parameters/Backup_save)
if /bin/busybox grep -q '^emunand_guard ' /proc/modules; then echo MOD_LOADED=1; else echo MOD_LOADED=0; fi
if [ -e /tmp_bt/emunand ]; then echo WORK_LEFT=1; else echo WORK_LEFT=0; fi
"""

    pkg_tar = work_tmp / "emunand.tar"
    make_bundle_tar(bundle_dir, pkg_tar, extra_scripts={"p1.sh": p1_sh, "p2.sh": p2_sh, "p3.sh": p3_sh})

    submicro_path = _resolve_submicro_fixture(repo_root)
    submicro_sha = sha256_file(submicro_path)

    session_dir = Path.home() / "a6400-runs" / f"emunand-pkg-{uuid.uuid4().hex[:12]}"
    session_dir.mkdir(parents=True, exist_ok=False)
    run_dir = session_dir / "machine"
    qmp_path = run_dir / "qmp.sock"
    console_log = session_dir / "console.log"

    env = dict(
        os.environ,
        A6400_QEMU_DIR=str(qemu_dir),
        A6400_QEMU_DATA_DIR=str(inputs_dir),
        RUN_DIR=str(run_dir),
        TIMEOUT="0",
        ULOGIO="",
        SUBMICRO_FRAMES=str(submicro_path),
        SUBMICRO_FRAMES_SHA=submicro_sha,
        SUBMICRO_LENS_POWER="1",
        CLKRST_MODEL="1",
        MISCREG="1",
        SD_IMAGE=str(sd_run_path),
        SD_SHA256=sd_sha_before,
        SETTING_FILES=f"{pkg_tar}:emunand.tar",
        QEMU_EXTRA=f"-icount shift=0,sleep=on -net none -no-reboot -S -qmp unix:{qmp_path},server=on,wait=off",
    )

    proc: subprocess.Popen[bytes] | None = None
    qmp: SimpleQmp | None = None
    deadline = time.monotonic() + timeout_sec

    def read_console() -> str:
        if not console_log.exists():
            return ""
        return console_log.read_bytes().decode("utf-8", errors="replace")

    def run_guest_cmd(tag: str, cmd: str, cmd_timeout: float = 55.0) -> tuple[int, str]:
        assert proc is not None and proc.stdin is not None
        begin_m = f"__BEGIN_{tag}__"
        end_m = f"__END_{tag}__:"
        full = f"echo {begin_m}\n{cmd}\necho {end_m}$?\n"
        for b in full.encode("utf-8"):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Global deadline exceeded sending {tag}")
            proc.stdin.write(bytes([b]))
            proc.stdin.flush()
            time.sleep(0.005)
        t_end = min(deadline, time.monotonic() + cmd_timeout)
        pat = re.compile(rf"(?<!echo ){re.escape(begin_m)}[^\n]*\n(.*?){re.escape(end_m)}(\d+)\r?\n", re.DOTALL)
        while time.monotonic() < t_end:
            if proc.poll() is not None:
                raise RuntimeError(f"QEMU exited prematurely ({proc.returncode}) during {tag}:\n{read_console()[-2000:]}")
            txt = read_console()
            m = pat.search(txt)
            if m:
                return int(m.group(2)), m.group(1)
            time.sleep(0.15)
        raise TimeoutError(f"Timed out waiting for guest command {tag}:\n{read_console()[-2500:]}")

    try:
        with console_log.open("wb") as log_fh:
            proc = subprocess.Popen(
                ["bash", str(repo_root / "emulators/qemu-a6400/run.sh")],
                cwd=str(repo_root),
                env=env,
                stdin=subprocess.PIPE,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

            while not qmp_path.exists():
                if proc.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"QMP socket failed to appear (rc={proc.poll()}):\n{read_console()}")
                time.sleep(0.1)

            qmp = SimpleQmp(qmp_path)
            insert_sd_via_qmp(qmp)
            qmp.request("cont")
            assert proc.stdin is not None
            proc.stdin.write(b"\n")
            proc.stdin.flush()

            while time.monotonic() < deadline:
                txt = read_console()
                if ("built-in shell (ash)" in txt.lower() or "/ # " in txt) and "Created HeapAllocator:" in txt:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(f"QEMU exited during boot:\n{txt[-2000:]}")
                time.sleep(0.25)
            else:
                raise TimeoutError(f"Guest did not reach ash + Created HeapAllocator:\n{read_console()[-2000:]}")

            run_guest_cmd("QUIET_PRINTK", "echo 0 > /proc/sys/kernel/printk", cmd_timeout=20.0)
            rc_wait, out_wait = run_guest_cmd(
                "WAIT_SD",
                "i=0; while ! /bin/busybox grep -q 'mmca10p1' /proc/partitions; do "
                "i=$((i+1)); [ $i -lt 45 ] || exit 9; /bin/busybox sleep 1; done; "
                "/bin/busybox cat /proc/partitions",
                cmd_timeout=60.0,
            )
            if rc_wait != 0:
                raise RuntimeError(f"mmca10p1 not registered (rc={rc_wait}):\n{out_wait}")

            # Snapshot QMP blockstats BEFORE mounting all flashable packages & running Custom-OS chroot
            stats_before = block_write_totals(qmp)

            # Phase 1: Unpack bundle to tmpfs and run p1.sh via clean output file
            rc_p1, out_p1 = run_guest_cmd(
                "PHASE1_MOUNT_PACKAGES",
                "/bin/busybox mount -t tmpfs -o size=8192k tmpfs /tmp_bt 2>/dev/null || true; "
                "/bin/busybox mkdir -p /tmp_bt/pkg; "
                "/bin/busybox tar -xf /setting/emunand.tar -C /tmp_bt/pkg; "
                "/bin/sh /tmp_bt/pkg/p1.sh > /tmp_bt/p1.out; /bin/busybox cat /tmp_bt/p1.out; /bin/busybox rm -f /tmp_bt/p1.out",
                cmd_timeout=75.0,
            )
            if rc_p1 != 0:
                raise RuntimeError(f"Phase 1 package mount failed (rc={rc_p1}):\n{out_p1}")

            # Phase 2: Nested ext2 + cramfs initrd mounts & CoW write check
            rc_p2, out_p2 = run_guest_cmd(
                "PHASE2_NESTED_INITRDS_AND_COW",
                "/bin/sh /tmp_bt/pkg/p2.sh > /tmp_bt/p2.out; /bin/busybox cat /tmp_bt/p2.out; /bin/busybox rm -f /tmp_bt/p2.out",
                cmd_timeout=75.0,
            )
            if rc_p2 != 0:
                raise RuntimeError(f"Phase 2 nested initrd / CoW failed (rc={rc_p2}):\n{out_p2}")

            stats_after = block_write_totals(qmp)

            # Phase 3: Full reversible stop
            rc_p3, out_p3 = run_guest_cmd(
                "PHASE3_STOP_PACKAGES",
                "/bin/sh /tmp_bt/pkg/p3.sh > /tmp_bt/p3.out; /bin/busybox cat /tmp_bt/p3.out; /bin/busybox rm -f /tmp_bt/p3.out",
                cmd_timeout=60.0,
            )
            if rc_p3 != 0:
                raise RuntimeError(f"Phase 3 reversible stop failed (rc={rc_p3}):\n{out_p3}")

            qmp.request("quit")
            proc.wait(timeout=10)
    finally:
        if qmp is not None:
            try:
                qmp.close()
            except Exception:
                pass
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    emmc_sha_after = sha256_file(emmc_path)
    sd_sha_after = sha256_file(sd_run_path)

    report_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(console_log, report_dir / "console.log")

    deltas: dict[str, dict[str, int]] = {}
    for dev, after_s in stats_after.items():
        before_s = stats_before.get(dev, {"wr_operations": 0, "wr_bytes": 0, "rd_operations": 0, "rd_bytes": 0})
        deltas[dev] = {
            k: after_s[k] - before_s[k]
            for k in ("wr_operations", "wr_bytes", "rd_operations", "rd_bytes")
        }

    shutil.rmtree(work_tmp, ignore_errors=True)
    shutil.rmtree(session_dir, ignore_errors=True)

    assertions = {
        "stock_system_kernel_8_2_6_0": "Sony CE Linux 8.2.6.0" in out_p1,
        "wbi_non_fs_blob_rejected_rc6": "WBI_REJECT_RC=6" in out_p1,
        "emunand_active": "EMUNAND_ACTIVE" in out_p1,
        "flashable_nflasha3_overmounted_on_system_8_2_7_6": "Sony CE Linux 8.2.7.6" in out_p1 and "av-cam.bin" in out_p1,
        "flashable_nflasha7_ext2_rootfs_mounted": "MOUNTED_ROOTFS_VER=38117_03.2026041003" in out_p1,
        "custom_os_ext2_chroot_arm_exec": "CHROOT_OUT=CUSTOM_OS_CHROOT_EXEC_OK NAME=BionzX-CustomOS-SD" in out_p1,
        "nested_nflasha3_ext2_initrd_mounted": "ATTACHED mode=vfs_file fstype=ext2" in out_p2 and "NESTED_MAIN_INIT=OK" in out_p2,
        "nested_nflasha1_cramfs_initrd_mounted": "ATTACHED mode=vfs_file fstype=cramfs" in out_p2,
        "running_kernel_still_8_2_6_0_without_kexec": "Sony CE Linux 8.2.6.0" in out_p2,
        "cow_guard_intercepted_writes": "COW_READ=PKG_COW_TEST" in out_p2 and "writes_intercepted=0" not in out_p2,
        "zero_physical_writes_emmc_and_sd": all(v["wr_operations"] == 0 and v["wr_bytes"] == 0 for v in deltas.values()),
        "reversible_stop_restores_stock_system_8_2_6_0": "EMUNAND_STOPPED" in out_p3 and "Sony CE Linux 8.2.6.0" in out_p3,
        "reversible_stop_unloads_ko_and_restores_bsave": "MOD_LOADED=0" in out_p3 and "BSAVE_AFTER=1" in out_p3 and "WORK_LEFT=0" in out_p3,
        "emmc_and_sd_sha256_unchanged": emmc_sha_before == emmc_sha_after and sd_sha_before == sd_sha_after,
    }
    failed = [k for k, ok in assertions.items() if not ok]
    receipt = {
        "status": "PASS" if not failed else "FAIL",
        "failed_assertions": failed,
        "assertions": assertions,
        "emmc_sha256_before": emmc_sha_before,
        "emmc_sha256_after": emmc_sha_after,
        "sd_exfat_sha256_before": sd_sha_before,
        "sd_exfat_sha256_after": sd_sha_after,
        "vdisk_sha256": vdisk_sha256,
        "qmp_blockstats_delta": deltas,
        "phase1_output": out_p1,
        "phase2_output": out_p2,
        "phase3_output": out_p3,
    }
    (report_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if failed:
        raise RuntimeError(f"QEMU package verification failed {failed}:\n{json.dumps(receipt, indent=2)}")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="WSL QEMU sony-a6400 E2E verifier for EmuNAND prototype.")
    parser.add_argument("--mode", choices=("base", "packages"), default="base")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--sd-image", type=Path, required=True)
    parser.add_argument("--vdisk-sha256", required=True)
    parser.add_argument("--inputs-dir", type=Path, default=Path.home() / "a6400-inputs/inputs-pr-preflight-20261116-1555")
    parser.add_argument("--qemu-dir", type=Path, default=Path.home() / "sony-a6400-qemu-11.1.3")
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=160)
    args = parser.parse_args()

    runner = run_packages_e2e if args.mode == "packages" else run_e2e
    receipt = runner(
        repo_root=args.repo_root.resolve(),
        bundle_dir=args.bundle_dir.resolve(),
        sd_src_path=args.sd_image.resolve(),
        vdisk_sha256=args.vdisk_sha256,
        inputs_dir=Path(os.path.expandvars(str(args.inputs_dir))).expanduser().resolve(),
        qemu_dir=Path(os.path.expandvars(str(args.qemu_dir))).expanduser().resolve(),
        report_dir=args.report_dir.resolve(),
        timeout_sec=args.timeout,
    )
    print(json.dumps({
        "status": receipt["status"],
        "assertions": receipt["assertions"],
        "qmp_blockstats_delta": receipt.get("qmp_blockstats_phase2_delta") or receipt.get("qmp_blockstats_delta"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
