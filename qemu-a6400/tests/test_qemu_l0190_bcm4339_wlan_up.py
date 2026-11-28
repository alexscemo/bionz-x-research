#!/usr/bin/env python3
"""L0190 qtest: the BCM4339 control replies wl_cfg80211_up reads.

bcmdhd's wl_cfg80211_up calls wl_cfg80211_get_ioctl_version, which sends
WLC_GET_VERSION (ioctl 1).  It accepts the dongle's ioctl interface version
only if it is WLC_IOCTL_VERSION or the old value 1; the source answers 12, so
the driver logs
    CFG80211-ERROR) wl_cfg80211_up : Version mismatch ... Got 12, expected 2 or 1
and dhd_open fails.  The reply has to be WLC_IOCTL_VERSION (2), the interface
version from Broadcom's wlioctl.h, not the driver version.

wl_update_wiphybands then reads WLC_GET_BANDLIST (ioctl 140) into a 12-byte
buffer and walks it as { uint32 count; uint32 band[count] }, comparing each
band to WLC_BAND_2G (1) and WLC_BAND_5G (2).  The source returns zeros, so the
wiphy has no bands; the WiFiSync AP script's `wl bands` is empty and it reports
5g_unavailable.  The model now answers {2, 1, 2} (both bands).

This qtest drives the SDIO host at 0xF0301000 through qtest only: CMD53 PIO
writes of a control frame, then a CMD53 PIO read of the reply.  Checks:
  P.version      WLC_GET_VERSION payload == 2
  P.cdc_echo_1   the reply echoes command 1
  P.bandlist     WLC_GET_BANDLIST payload == [2, 1, 2]
  P.cdc_echo_140 the reply echoes command 140

Negative control: the same file against a build of the source before this
change answers 12 for the version and [0, 0, 0] for the band list, so both
value checks fail; the test is not a tautology.  This is a runtime control,
not a source grep.

Run from the repository root:
  A6400_QEMU_DIR=<build> python3 qemu-a6400/tests/test_qemu_l0190_bcm4339_wlan_up.py
"""
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0190_OUT", tempfile.mkdtemp(prefix="l0190-wlanup-")))

BASE = 0xF0301000
FN2_ADDR = 0x8000


class QTest:
    def __init__(self, machine, log_path):
        OUT.mkdir(parents=True, exist_ok=True)
        self.log_path = log_path
        self.log = open(log_path, "wb")
        self.proc = subprocess.Popen(
            [str(QEMU), "-M", machine, "-accel", "qtest", "-display", "none",
             "-monitor", "none", "-serial", "none", "-net", "none",
             "-d", "unimp", "-qtest", "stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, bufsize=0)
        self.pending = b""

    def cmd(self, text):
        self.proc.stdin.write((text + "\n").encode("ascii"))
        deadline = time.monotonic() + 5
        while b"\n" not in self.pending:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.proc.stdout], [], [], left)[0]:
                raise RuntimeError("qtest timeout: " + text)
            chunk = os.read(self.proc.stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError("qtest exited: " + text)
            self.pending += chunk
        line, self.pending = self.pending.split(b"\n", 1)
        if not line.startswith(b"OK"):
            raise RuntimeError(text + ": " + repr(line))
        return line

    def wr16(self, off, value):
        self.cmd("writew %#x %#x" % (BASE + off, value))

    def wr32(self, off, value):
        self.cmd("writel %#x %#x" % (BASE + off, value))

    def rd32(self, off):
        return int(self.cmd("readl %#x" % (BASE + off)).decode().split()[1], 16)

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.log.close()
        return Path(self.log_path).read_text(errors="replace")


def control_frame(cmd, pay_len, seq):
    length = 64
    buf = bytearray(length)
    buf[0] = length & 0xFF
    buf[1] = (length >> 8) & 0xFF
    inv = (~length) & 0xFFFF
    buf[2] = inv & 0xFF
    buf[3] = (inv >> 8) & 0xFF
    buf[4] = seq & 0xFF
    buf[5] = 0x00
    buf[6] = 0x00
    buf[7] = 12
    buf[8] = 0x00
    buf[9] = 0x00
    buf[12] = cmd & 0xFF              # BCDC command
    buf[16] = pay_len & 0xFF          # BCDC output length
    buf[17] = (pay_len >> 8) & 0xFF
    buf[18] = (pay_len >> 16) & 0xFF
    buf[19] = (pay_len >> 24) & 0xFF
    return bytes(buf)


def cmd53(arg):
    q.wr16(0x0C, 0)
    q.wr32(0x08, arg)
    q.wr16(0x0E, (53 << 8) | 0x20)


def exchange(cmd, pay_len, seq):
    data = control_frame(cmd, pay_len, seq)
    cmd53(0x80000000 | (2 << 28) | (1 << 26) | (FN2_ADDR << 9) | len(data))
    for i in range(0, len(data), 4):
        q.wr32(0x20, int.from_bytes(data[i:i + 4], "little"))
    cmd53((2 << 28) | (1 << 26) | (FN2_ADDR << 9))
    raw = bytearray()
    for _ in range(16):
        raw += q.rd32(0x20).to_bytes(4, "little")
    return raw


checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print("%s %s: got %r want %r" % ("PASS" if ok else "FAIL", name, got, want))


try:
    q = QTest("sony-a6400,bcm4339=on", OUT / "qtest-l0190-wlanup.log")
    try:
        reply = exchange(1, 4, 0x00)
        check("P.cdc_echo_1", int.from_bytes(reply[12:16], "little"), 1)
        check("P.version", int.from_bytes(reply[28:32], "little"), 2)

        reply = exchange(140, 12, 0x01)
        check("P.cdc_echo_140", int.from_bytes(reply[12:16], "little"), 140)
        bl = [int.from_bytes(reply[28 + 4 * i:32 + 4 * i], "little")
              for i in range(3)]
        check("P.bandlist", bl, [2, 1, 2])
    finally:
        q.close()
except Exception as exc:  # noqa: BLE001 - report the qtest failure plainly
    print("FAIL qtest: %s" % exc)
    checks.append(False)

print("summary: %d checks, %d failed" % (len(checks), checks.count(False)))
sys.exit(0 if checks and all(checks) else 1)
