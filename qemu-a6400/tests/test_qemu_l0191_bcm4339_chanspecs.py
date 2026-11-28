#!/usr/bin/env python3
"""L0191 qtest: the BCM4339 `chanspecs` reply and multi-read delivery.

bcmdhd's wl_construct_reginfo (inlined into wl_update_wiphybands) asks for the
`chanspecs` iovar with a 1024-byte WLC_GET_VAR (CDC command 262).  The value is
a wl_uint32_list { uint32 count; uint32 element[count]; } of 16-bit 11ac
chanspecs.  The driver accepts band 5G (0xc000), BW_20 (0x1000) and channels
above 33; a list of zeros leaves the wiphy with no channels, so the guest's AP
script sees no 5 GHz band and stops with `5g_unavailable`.

The driver reads a control reply in 32-byte F2 chunks.  The model must keep the
queued frame and return the next bytes on the following read; returning the
whole frame once and zeroing the queue loses the chanspec elements.  This test
sends the request through the SDIO host at 0xF0301000 and reads the reply in
two 32-byte chunks, exactly as the driver does.

Checks:
  P.cdc_echo_262  the reply echoes command 262
  P.count         the list count is 4
  P.chanspecs     the four elements are 5 GHz, 20 MHz channels 36/40/44/48

Negative control: the same file against a build before this change answers a
zero list (count 0 and no elements), so P.count and P.chanspecs fail.  This is
a runtime control, not a source grep.

Run from the repository root:
  A6400_QEMU_DIR=<build> python3 qemu-a6400/tests/test_qemu_l0191_bcm4339_chanspecs.py
"""
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0191_OUT", tempfile.mkdtemp(prefix="l0191-chanspecs-")))

BASE = 0xF0301000
FN2_ADDR = 0x8000
CHANSPECS = [0xD024, 0xD028, 0xD02C, 0xD030]


class QTest:
    def __init__(self, machine, log_path):
        OUT.mkdir(parents=True, exist_ok=True)
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


def control_frame(cmd, out_len, payload, seq):
    length = 12 + 16 + len(payload)
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
    buf[9] = 0x00
    buf[12] = cmd & 0xFF
    buf[13] = (cmd >> 8) & 0xFF
    buf[14] = (cmd >> 16) & 0xFF
    buf[15] = (cmd >> 24) & 0xFF
    buf[16] = out_len & 0xFF
    buf[17] = (out_len >> 8) & 0xFF
    buf[18] = (out_len >> 16) & 0xFF
    buf[19] = (out_len >> 24) & 0xFF
    buf[28:28 + len(payload)] = payload
    return bytes(buf)


def cmd53(arg):
    q.wr16(0x0C, 0)
    q.wr32(0x08, arg)
    q.wr16(0x0E, (53 << 8) | 0x20)


def write_frame(data):
    cmd53(0x80000000 | (2 << 28) | (1 << 26) | (FN2_ADDR << 9) | len(data))
    for i in range(0, len(data), 4):
        q.wr32(0x20, int.from_bytes(data[i:i + 4], "little"))


def read_bytes(n):
    # Byte mode, so the model sees the driver's 32-byte read size.
    cmd53((2 << 28) | (1 << 26) | (FN2_ADDR << 9) | n)
    raw = bytearray()
    for _ in range((n + 3) // 4):
        raw += q.rd32(0x20).to_bytes(4, "little")
    return bytes(raw[:n])


checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print("%s %s: got %r want %r" % ("PASS" if ok else "FAIL", name, got, want))


try:
    q = QTest("sony-a6400,bcm4339=on", OUT / "qtest-l0191-chanspecs.log")
    try:
        payload = b"chanspecs\x00" + bytes(55)
        write_frame(control_frame(262, 1024, payload, 0x00))
        first = read_bytes(32)
        second = read_bytes(32)
        reply = first + second
        check("P.cdc_echo_262", int.from_bytes(reply[12:16], "little"), 262)
        count = int.from_bytes(reply[28:32], "little")
        check("P.count", count, 4)
        got = [int.from_bytes(reply[32 + 4 * i:36 + 4 * i], "little")
               for i in range(4)]
        check("P.chanspecs", got, CHANSPECS)
    finally:
        q.close()
except Exception as exc:  # noqa: BLE001 - report the qtest failure plainly
    print("FAIL qtest: %s" % exc)
    checks.append(False)

print("summary: %d checks, %d failed" % (len(checks), checks.count(False)))
sys.exit(0 if checks and all(checks) else 1)
