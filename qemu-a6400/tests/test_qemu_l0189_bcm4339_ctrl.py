#!/usr/bin/env python3
"""L0189 qtest: the BCM4339 Fn2 SDPCM reply carries a moving transmit credit.

bcmdhd's dhd_bus_txctl queues a control frame, sets ctrl_frame_stat and waits
for the worker to send it.  The worker sends only while
    0 < (uint8_t)(tx_max - tx_seq) <= 0x70
where tx_max is header byte 9 (flowcontrol) of the last received SDPCM frame
and tx_seq is the sequence byte the host wrote.  The fixed model advertised the
constant 8, so after the frames with host seq 0xFF..0x07 the tenth frame
(seq 0x08) had tx_max == tx_seq and stayed queued: `ctrl_frame_stat == TRUE
txcnt_timeout`, `dhd_bus_start failed`.

This qtest drives the SDIO host at 0xF0301000 through qtest only: CMD53 PIO
writes of a 64-byte WLC_GET_VERSION control frame with seq 0xFF..0x0A, then a
CMD53 PIO read of the reply.  Checks:
  P.fc_seq_<nn>     reply byte 9 == (seq + window) mod 256 for every frame
  P.cdc_echo_<nn>   the reply echoes the command (1)
  P.window          the loop covered 12 frames

Negative control: run the same file against a build of the old source, where
byte 9 is the constant 8; P.fc_seq_ff fails (got 8, want 7), so the test is not
a tautology.  This is a runtime control, not a source grep.

Run from the repository root:
  A6400_QEMU_DIR=<build> python3 qemu-a6400/tests/test_qemu_l0189_bcm4339_ctrl.py
"""
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QEMU = Path(os.environ["A6400_QEMU_DIR"]) / "build/qemu-system-arm"
OUT = Path(os.environ.get("L0189_OUT", tempfile.mkdtemp(prefix="l0189-ctrl-")))

BASE = 0xF0301000
FN2_ADDR = 0x8000
WINDOW = 8
SEQS = [0xFF, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06,
        0x07, 0x08, 0x09, 0x0A]


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


def control_frame(seq):
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
    buf[12] = 0x01
    return bytes(buf)


def cmd53(arg):
    q.wr16(0x0C, 0)
    q.wr32(0x08, arg)
    q.wr16(0x0E, (53 << 8) | 0x20)


def send_ctrl(seq):
    data = control_frame(seq)
    cmd53(0x80000000 | (2 << 28) | (1 << 26) | (FN2_ADDR << 9) | len(data))
    for i in range(0, len(data), 4):
        q.wr32(0x20, int.from_bytes(data[i:i + 4], "little"))


def read_reply():
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
    q = QTest("sony-a6400,bcm4339=on", OUT / "qtest-l0189-ctrl.log")
    try:
        for seq in SEQS:
            send_ctrl(seq)
            reply = read_reply()
            check("P.cdc_echo_%02x" % seq,
                  int.from_bytes(reply[12:16], "little"), 1)
            check("P.fc_seq_%02x" % seq, reply[9], (seq + WINDOW) & 0xFF)
        check("P.window", len(SEQS), 12)
    finally:
        q.close()
except Exception as exc:  # noqa: BLE001 - report the qtest failure plainly
    print("FAIL qtest: %s" % exc)
    checks.append(False)

print("summary: %d checks, %d failed" % (len(checks), checks.count(False)))
sys.exit(0 if checks and all(checks) else 1)
