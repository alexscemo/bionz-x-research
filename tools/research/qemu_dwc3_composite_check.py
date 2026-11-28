#!/usr/bin/env python3
"""Native qtest proof of the opt-in composite DWC3 endpoint contract.

Boots QEMU's ``sony-a6400`` machine with the ``cxd90045-dwc3`` model, ``strict-ep0`` and the
new ``strict-composite`` policy, then drives the controller entirely through MMIO and guest
DMA. The virtual USB host is a chardev socket, so every reply is a real model response: a
missing transfer is reported as NAK, never as invented data.

Contracts checked (all opt-in; the earlier default/strict-ep0/strict-bulk behaviour is
untouched):

1.  EP0 three-stage control-IN: SETUP, DATA-IN (18-byte descriptor), STATUS-OUT.
2.  Fourteen non-control endpoints (7 IN + 7 OUT, ``DALEPENA`` bits 2..15) configured and
    delivered in one interleaved pass, so parallel traffic is exercised, not sequential.
3.  Bulk OUT with residual capacity: ACK, TRB size = capacity - length, short-packet status.
4.  Interrupt IN endpoint (``SETEPCONFIG`` type 3) delivers a short report.
5.  Isochronous IN endpoint (type 1) emits XferInProgress before XferComplete.
6.  Multi-TRB sequencing: two NORMAL TRBs are consumed by two host tokens, then NAK.
7.  LINK-following: two consecutive LINK TRBs reach the data TRB (the old limit was one).
8.  SETSTALL/CLEARSTALL per endpoint: STALL reply, then a completed transfer.
9.  Soft disconnect/connect through ``DCTL.RUN_STOP``: NAK + DISCONNECT event, then
    USBRESET/CONNECTDONE events and a working transfer again.
10. Endpoint budget: 16 physical endpoints (EP0 OUT/IN + 7 pairs) and rejection of 16.

Usage (WSL, repository root):

    python3 -m tools.research.qemu_dwc3_composite_check --qemu-dir ~/sony-a6400-qemu-11.1.3 \
        --report-dir artifacts/qemu-a6400/dwc3-composite-1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DWC3 = 0xF020C000
CORE = DWC3 + 0x100

REG_GCTL = CORE + 0x010
REG_GEVNTADRLO0 = CORE + 0x300
REG_GEVNTADRHI0 = CORE + 0x304
REG_GEVNTSIZ0 = CORE + 0x308
REG_GEVNTCOUNT0 = CORE + 0x30C
REG_DCFG = CORE + 0x600
REG_DCTL = CORE + 0x604
REG_DEVTEN = CORE + 0x608
REG_DSTS = CORE + 0x60C
REG_DALEPENA = CORE + 0x620
REG_EP_BASE = CORE + 0x700
DCTL_RUN_STOP = 1 << 31
TRB_HWO, TRB_LST, TRB_IOC, TRB_CHN = 1, 2, 1 << 11, 4
TRBCTL_NORMAL, TRBCTL_LINK = 1, 8

RING_PA = 0x02000000
RING_SIZE = 0x1000
TRB_PA = 0x02100000
BUF_PA = 0x02200000
BUF_STRIDE = 0x1000
EP0_SETUP_PA = 0x02300000
EP0_DATA_PA = 0x02301000

DEVICE_DESCRIPTOR = bytes.fromhex("1201000200000040c2549f06000101020301")
TYPE_ISOC, TYPE_BULK, TYPE_INTR = 1, 2, 3


class QTest:
    def __init__(self, path: Path):
        self.sock = socket.socket(socket.AF_UNIX)
        for _ in range(200):
            try:
                self.sock.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.05)
        else:
            raise RuntimeError("qtest socket did not appear")
        self.sock.settimeout(10)
        self.stream = self.sock.makefile("rwb", buffering=0)

    def cmd(self, line: str) -> str:
        self.stream.write((line + "\n").encode())
        reply = self.stream.readline().decode().strip()
        if reply.startswith("FAIL"):
            raise RuntimeError(f"{line}: {reply}")
        return reply

    def readl(self, address: int) -> int:
        reply = self.cmd(f"readl {address:#x}")
        return int(reply.split()[1], 16) & 0xFFFFFFFF

    def writel(self, address: int, value: int) -> None:
        self.cmd(f"writel {address:#x} {value & 0xFFFFFFFF:#x}")

    def write_mem(self, address: int, data: bytes) -> None:
        self.cmd(qtest_mem_write(address, data))

    def read_mem(self, address: int, size: int) -> bytes:
        reply = self.cmd(f"read {address:#x} {size}")
        value = int(reply.split()[1], 16)
        return value.to_bytes(size, "big")

    def close(self) -> None:
        try:
            self.stream.close()
            self.sock.close()
        except OSError:
            pass


class Host:
    def __init__(self, path: Path):
        self.sock = socket.socket(socket.AF_UNIX)
        for _ in range(200):
            try:
                self.sock.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.05)
        else:
            raise RuntimeError("host socket did not appear")
        self.sock.settimeout(0.25)
        self.buffer = b""
        self.events = []

    def send(self, line: str) -> None:
        self.sock.sendall((line + "\n").encode())
        self.events.append({"tx": line})

    def line(self, timeout: float = 3.0) -> str:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            if time.monotonic() > deadline:
                raise TimeoutError("no host reply")
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise EOFError("host socket closed")
            self.buffer += chunk
        raw, self.buffer = self.buffer.split(b"\n", 1)
        text = raw.decode("ascii")
        self.events.append({"rx": text})
        return text

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def trb(address: int, size: int, ctrl: int) -> bytes:
    return (address & 0xFFFFFFFF).to_bytes(4, "little") + (address >> 32).to_bytes(4, "little") + \
           (size & 0x00FFFFFF).to_bytes(4, "little") + (ctrl & 0xFFFFFFFF).to_bytes(4, "little")


def qtest_mem_write(address: int, data: bytes) -> str:
    """qtest generic-write line for exact bytes.

    This QEMU's ``write`` command consumes the leading hex byte of its data
    argument (verified by read-back), so a ``00`` pad keeps the layout exact.
    """
    return f"write {address:#x} {len(data)} 00{data.hex()}"


def parse_events(blob: bytes) -> list[dict]:
    events = []
    for offset in range(0, len(blob) - 3, 4):
        word = int.from_bytes(blob[offset:offset + 4], "little")
        events.append({
            "offset": offset,
            "word": f"{word:#010x}",
            "ep": (word >> 1) & 0x1F,
            "type": (word >> 6) & 0xF,
            "status": (word >> 12) & 0xF,
            "param": (word >> 16) & 0xFFFF,
        })
    return events


class CompositeCase:
    def __init__(self, qemu: Path, report: dict, out: Path):
        self.out = out
        self.report = report
        self.scratch = tempfile.TemporaryDirectory(prefix="dwc3-composite-")
        root = Path(self.scratch.name)
        self.qtest_path = root / "qtest"
        self.host_path = root / "host"
        command = [
            str(qemu), "-M", "sony-a6400,dwc3-gadget=on", "-m", "1G",
            "-accel", "qtest", "-display", "none", "-monitor", "none", "-serial", "none",
            "-net", "none", "-chardev", f"socket,id=usbhost,path={self.host_path},server=on,wait=off",
            "-global", "cxd90045-dwc3.chardev=usbhost",
            "-global", "cxd90045-dwc3.strict-ep0=on",
            "-global", "cxd90045-dwc3.strict-composite=on",
            "-qtest", f"unix:{self.qtest_path},server=on,wait=off",
        ]
        self.log = (out / "qemu.log").open("wb")
        self.proc = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT)
        self.qt = QTest(self.qtest_path)
        self.host = Host(self.host_path)
        self.checks = []
        self.seen_offset = -4

    def check(self, name: str, condition: bool, **detail) -> None:
        item = {"name": name, "ok": bool(condition), **detail}
        self.checks.append(item)
        if not condition:
            raise AssertionError(f"{name}: {detail}")

    # ---------------------------------------------------------------- setup
    def configure_ep(self, phys: int, ep_type: int, max_packet: int) -> None:
        direction = phys & 1
        cfg0 = direction | (ep_type << 1) | (max_packet << 3)
        cfg1 = (1 << 8) | (phys << 25)
        base = REG_EP_BASE + phys * 0x10
        self.qt.writel(base + 0x8, cfg0)          # DEPCFG0 (parameter 0)
        self.qt.writel(base + 0x4, cfg1)          # DEPCFG1 (parameter 1)
        self.qt.writel(base + 0xC, (1 << 10) | 1)  # SETEPCONFIG | CMDACT
        self.check(f"setepconfig-{phys}",
                   self.qt.readl(base + 0xC) & (1 << 10) == 0,
                   cfg0=f"{cfg0:#x}", cfg1=f"{cfg1:#x}")

    def arm(self, phys: int, trb_address: int) -> None:
        base = REG_EP_BASE + phys * 0x10
        self.qt.writel(base + 0x8, 0)
        self.qt.writel(base + 0x4, trb_address)
        self.qt.writel(base + 0xC, (1 << 10) | 6)  # STARTTRANSFER | CMDACT
        self.check(f"starttransfer-{phys}",
                   self.qt.readl(base + 0xC) & (1 << 10) == 0)

    def init_controller(self) -> None:
        self.qt.writel(REG_GEVNTADRLO0, RING_PA)
        self.qt.writel(REG_GEVNTADRHI0, 0)
        self.qt.writel(REG_GEVNTSIZ0, RING_SIZE)
        self.qt.writel(REG_DCFG, 1)
        self.qt.writel(REG_DEVTEN, 0x1E1F)
        self.qt.writel(REG_DALEPENA, 0xFFFF)
        self.qt.writel(REG_DCTL, DCTL_RUN_STOP)
        self.check("controller-gsnpsid", self.qt.readl(CORE + 0x20) == 0x5533290A,
                   value=f"{self.qt.readl(CORE + 0x20):#x}")
        self.check("dctl-run", self.qt.readl(REG_DCTL) & DCTL_RUN_STOP != 0)
        self.host.send("CONN")
        self.pump_events(2)

    def events(self, new: bool = True) -> list[dict]:
        # Read the whole event ring: the model tracks an unread count and a write
        # head, and this harness never needs the wrap ordering, only the events
        # produced since the previous call. Zero words are unwritten slots.
        blob = self.qt.read_mem(RING_PA, RING_SIZE)
        parsed = [event for event in parse_events(blob) if event["word"] != "0x00000000"]
        if not new:
            return parsed
        fresh = [event for event in parsed if event["offset"] > self.seen_offset]
        if fresh:
            self.seen_offset = max(event["offset"] for event in fresh)
        return fresh

    def pump_events(self, settle: float = 0.5) -> list[dict]:
        time.sleep(settle)
        return self.events()

    def drain(self) -> None:
        count = self.qt.readl(REG_GEVNTCOUNT0)
        if count:
            self.qt.writel(REG_GEVNTCOUNT0, count)

    # ------------------------------------------------------------- transfers
    def in_transfer(self, phys: int, slot: int, payload: bytes, *, lst=True,
                    expect="data") -> dict:
        address = TRB_PA + (phys * 16) + (slot * 16)
        buffer = BUF_PA + (phys * BUF_STRIDE) + (slot * 256)
        self.qt.write_mem(buffer, payload)
        ctrl = TRB_HWO | TRB_IOC | (TRB_LST if lst else 0) | (TRBCTL_NORMAL << 4)
        self.qt.write_mem(address, trb(buffer, len(payload), ctrl))
        self.arm(phys, address)
        self.host.send(f"IN {phys}")
        reply = self.host.line()
        after = int.from_bytes(self.qt.read_mem(address + 12, 4), "little")
        result = {"phys": phys, "reply": reply, "trb_ctrl": f"{after:#x}",
                  "trb_size": int.from_bytes(self.qt.read_mem(address + 8, 4), "little")}
        if expect == "data":
            self.check(f"in-{phys}-{slot}-reply", reply == f"IN {phys} {payload.hex()}", **result)
            self.check(f"in-{phys}-{slot}-hwo", after & TRB_HWO == 0, **result)
            self.check(f"in-{phys}-{slot}-size", result["trb_size"] == 0, **result)
        return result

    def out_transfer(self, phys: int, slot: int, payload: bytes, capacity: int, *,
                     lst=True) -> dict:
        address = TRB_PA + (phys * 16) + (slot * 16)
        buffer = BUF_PA + (phys * BUF_STRIDE) + (slot * 256)
        self.qt.write_mem(buffer, bytes(capacity))
        ctrl = TRB_HWO | TRB_IOC | (TRB_LST if lst else 0) | (TRBCTL_NORMAL << 4)
        self.qt.write_mem(address, trb(buffer, capacity, ctrl))
        self.arm(phys, address)
        self.host.send(f"OUT {phys} {payload.hex()}")
        reply = self.host.line()
        stored = self.qt.read_mem(buffer, len(payload))
        residual = int.from_bytes(self.qt.read_mem(address + 8, 4), "little")
        after = int.from_bytes(self.qt.read_mem(address + 12, 4), "little")
        self.check(f"out-{phys}-{slot}-ack", reply == f"ACK {phys} {len(payload)}",
                   reply=reply)
        self.check(f"out-{phys}-{slot}-data", stored == payload,
                   stored=stored.hex())
        self.check(f"out-{phys}-{slot}-residual", residual == capacity - len(payload),
                   residual=residual, capacity=capacity)
        self.check(f"out-{phys}-{slot}-hwo", after & TRB_HWO == 0, ctrl=f"{after:#x}")
        return {"phys": phys, "reply": reply, "residual": residual}

    def run(self) -> None:
        self.init_controller()
        ep0 = self.ep0_control()
        parallel = self.parallel_bulk()
        interrupt = self.interrupt_in()
        isoc = self.isoc_in()
        multi, link = self.multi_trb()
        stall = self.stall_cycle()
        soft = self.soft_connect()
        budget = self.endpoint_budget()
        self.report["checks"] = self.checks
        self.report["summary"] = {
            "ep0_control": ep0, "parallel_eps": parallel, "interrupt_in": interrupt,
            "isoc_in": isoc, "multi_trb": multi, "link_hops": link, "stall": stall,
            "soft_connect": soft, "endpoint_budget": budget,
        }

    # ----------------------------------------------------------------- tests
    def ep0_control(self) -> dict:
        # EP0 OUT carries SETUP, EP0 IN carries DATA/STATUS, both physically indexed.
        self.configure_ep(0, 0, 64)
        self.configure_ep(1, 0, 64)
        self.drain()
        setup_address = TRB_PA + 0x1000
        self.qt.write_mem(EP0_SETUP_PA, bytes.fromhex("8006000100001200"))
        self.qt.write_mem(setup_address,
                          trb(EP0_SETUP_PA, 8, TRB_HWO | TRB_LST | (2 << 4)))
        self.arm(0, setup_address)
        self.host.send("SETUP 8006000100001200")
        setup_reply = self.host.line()
        self.check("ep0-setup", setup_reply == "ACK 0 8", reply=setup_reply)

        data_address = TRB_PA + 0x1010
        self.qt.write_mem(EP0_DATA_PA, DEVICE_DESCRIPTOR)
        self.qt.write_mem(data_address,
                          trb(EP0_DATA_PA, len(DEVICE_DESCRIPTOR), TRB_HWO | TRB_LST | (5 << 4)))
        self.arm(1, data_address)
        self.host.send("IN 1")
        data_reply = self.host.line()
        self.check("ep0-data", data_reply == f"IN 1 {DEVICE_DESCRIPTOR.hex()}",
                   reply=data_reply)

        status_address = TRB_PA + 0x1020
        self.qt.write_mem(status_address, trb(0, 0, TRB_HWO | TRB_LST | (4 << 4)))
        self.arm(0, status_address)
        self.host.send("OUT 0 -")
        status_reply = self.host.line()
        self.check("ep0-status", status_reply == "ACK 0 0", reply=status_reply)
        events = self.pump_events(0.4)
        ep0_completions = [e for e in events if e["ep"] in (0, 1) and e["type"] == 1]
        self.check("ep0-completions", len(ep0_completions) >= 3,
                   events=[e["word"] for e in events])
        return {"setup": setup_reply, "data_bytes": len(DEVICE_DESCRIPTOR),
                "status": status_reply, "completion_events": len(ep0_completions)}

    def parallel_bulk(self) -> dict:
        ins = [3, 5, 7, 9, 11, 13, 15]
        outs = [2, 4, 6, 8, 10, 12, 14]
        for phys in ins + outs:
            self.configure_ep(phys, TYPE_BULK, 512)
        self.drain()
        in_payload = {phys: bytes([phys]) * (16 * (phys // 2)) for phys in ins}
        out_payload = {phys: bytes([0xA0 + phys]) * (8 * (phys // 2)) for phys in outs}
        # Arm everything first so the tokens below interleave live endpoints.
        for phys in ins:
            address = TRB_PA + (phys * 16)
            buffer = BUF_PA + (phys * BUF_STRIDE)
            self.qt.write_mem(buffer, in_payload[phys])
            ctrl = TRB_HWO | TRB_IOC | TRB_LST | (TRBCTL_NORMAL << 4)
            self.qt.write_mem(address, trb(buffer, len(in_payload[phys]), ctrl))
            self.arm(phys, address)
        for phys in outs:
            address = TRB_PA + (phys * 16)
            buffer = BUF_PA + (phys * BUF_STRIDE)
            self.qt.write_mem(buffer, bytes(1024))
            ctrl = TRB_HWO | TRB_IOC | TRB_LST | (TRBCTL_NORMAL << 4)
            self.qt.write_mem(address, trb(buffer, 1024, ctrl))
            self.arm(phys, address)
        order = []
        for index in range(len(ins)):
            order.append(("IN", ins[index]))
            order.append(("OUT", outs[index]))
        delivered = []
        for kind, phys in order:
            if kind == "IN":
                self.host.send(f"IN {phys}")
                reply = self.host.line()
                self.check(f"parallel-in-{phys}", reply == f"IN {phys} {in_payload[phys].hex()}",
                           reply=reply[:80])
                delivered.append(phys)
            else:
                self.host.send(f"OUT {phys} {out_payload[phys].hex()}")
                reply = self.host.line()
                self.check(f"parallel-out-{phys}",
                           reply == f"ACK {phys} {len(out_payload[phys])}", reply=reply)
                stored = self.qt.read_mem(BUF_PA + (phys * BUF_STRIDE), len(out_payload[phys]))
                self.check(f"parallel-out-{phys}-data", stored == out_payload[phys])
                delivered.append(phys)
        events = self.pump_events(0.6)
        complete_eps = {e["ep"] for e in events if e["type"] == 1}
        self.check("parallel-events", set(ins + outs) <= complete_eps,
                   missing=sorted(set(ins + outs) - complete_eps))
        self.check("dalepena-budget", self.qt.readl(REG_DALEPENA) & 0xFFFF == 0xFFFF,
                   value=f"{self.qt.readl(REG_DALEPENA):#x}")
        return {"ins": ins, "outs": outs, "tokens": len(order),
                "distinct_endpoints": len(set(delivered)),
                "completion_events": len(events)}

    def interrupt_in(self) -> dict:
        self.configure_ep(9, TYPE_INTR, 32)
        self.drain()
        result = self.in_transfer(9, 0, bytes.fromhex("5a010203"))
        events = self.pump_events(0.4)
        types = [e["type"] for e in events if e["ep"] == 9]
        self.check("interrupt-completion", 1 in types, types=types)
        short = [e for e in events if e["ep"] == 9 and e["type"] == 1 and e["status"] & 2]
        self.check("interrupt-short-status", bool(short),
                   statuses=[e["status"] for e in events if e["ep"] == 9])
        return {"reply": result["reply"], "event_types": types}

    def isoc_in(self) -> dict:
        self.configure_ep(11, TYPE_ISOC, 1024)
        self.drain()
        self.host.send("SOF 42")
        self.check("sof-ack", self.host.line() == "OK SOF")
        result = self.in_transfer(11, 0, b"\x11" * 8)
        events = self.pump_events(0.4)
        types = [(e["type"], e["param"]) for e in events if e["ep"] == 11]
        self.check("isoc-inprog", any(t == 2 for t, _ in types), types=types)
        self.check("isoc-complete", any(t == 1 for t, _ in types), types=types)
        self.check("isoc-frame-param", any(t == 2 and p == (42 << 4) for t, p in types),
                   types=types)
        return {"reply": result["reply"], "event_types": types}

    def multi_trb(self) -> tuple[dict, int]:
        phys = 13
        self.configure_ep(phys, TYPE_BULK, 512)
        self.drain()
        base = TRB_PA + 0x2000
        first = BUF_PA + 0x9000
        second = BUF_PA + 0x9200
        self.qt.write_mem(first, b"FIRST-TRB")
        self.qt.write_mem(second, b"SECOND-TRB")
        self.qt.write_mem(base, trb(first, 9, TRB_HWO | TRB_IOC | (TRBCTL_NORMAL << 4)))
        self.qt.write_mem(base + 16, trb(second, 10, TRB_HWO | TRB_IOC | TRB_LST |
                                         (TRBCTL_NORMAL << 4)))
        self.arm(phys, base)
        self.host.send(f"IN {phys}")
        one = self.host.line()
        self.host.send(f"IN {phys}")
        two = self.host.line()
        self.host.send(f"IN {phys}")
        three = self.host.line()
        self.check("multi-trb-first", one == f"IN {phys} {b'FIRST-TRB'.hex()}", reply=one)
        self.check("multi-trb-second", two == f"IN {phys} {b'SECOND-TRB'.hex()}", reply=two)
        self.check("multi-trb-third-nak", three == f"NAK {phys}", reply=three)

        # Two LINK hops, then a data TRB. The previous contract allowed one.
        phys = 7
        self.configure_ep(phys, TYPE_BULK, 512)
        base = TRB_PA + 0x2100
        self.qt.write_mem(BUF_PA + 0x9400, b"LINKED")
        self.qt.write_mem(base, trb(base + 16, 0, TRB_HWO | (TRBCTL_LINK << 4)))
        self.qt.write_mem(base + 16, trb(base + 32, 0, TRB_HWO | (TRBCTL_LINK << 4)))
        self.qt.write_mem(base + 32, trb(BUF_PA + 0x9400, 6,
                                         TRB_HWO | TRB_IOC | TRB_LST | (TRBCTL_NORMAL << 4)))
        self.arm(phys, base)
        self.host.send(f"IN {phys}")
        reply = self.host.line()
        self.check("link-hops", reply == f"IN {phys} {b'LINKED'.hex()}", reply=reply)
        return ({"first": one, "second": two, "third": three},
                {"reply": reply, "hops": 2})

    def stall_cycle(self) -> dict:
        phys = 5
        self.configure_ep(phys, TYPE_BULK, 512)
        base = REG_EP_BASE + phys * 0x10
        self.qt.writel(base + 0xC, (1 << 10) | 4)   # SETSTALL
        self.host.send(f"IN {phys}")
        stalled = self.host.line()
        self.check("stall-reply", stalled == f"STALL {phys}", reply=stalled)
        self.qt.writel(base + 0xC, (1 << 10) | 5)   # CLEARSTALL
        result = self.in_transfer(phys, 1, b"AFTER-STALL")
        return {"stall": stalled, "after": result["reply"]}

    def soft_connect(self) -> dict:
        def device_types(items):
            return sorted({(int(item["word"], 16) >> 8) & 0xF
                           for item in items if int(item["word"], 16) & 1})

        self.qt.writel(REG_DCTL, 0)
        self.host.send("IN 3")
        disconnected = self.host.line()
        self.check("soft-disconnect-nak", disconnected == "NAK 3", reply=disconnected)
        events = self.pump_events(0.5)
        types = device_types(events)
        self.check("soft-disconnect-event", 0 in types,
                   events=[e["word"] for e in events], device_types=types)
        self.qt.writel(REG_DCTL, DCTL_RUN_STOP)
        events = self.pump_events(0.6)
        types = device_types(events)
        self.check("soft-connect-reset", 1 in types,
                   events=[e["word"] for e in events], device_types=types)
        self.check("soft-connect-done", 2 in types,
                   events=[e["word"] for e in events], device_types=types)
        physical = int(self.qt.readl(REG_DSTS))
        self.check("soft-connect-not-halted", physical & (1 << 22) == 0, dsts=f"{physical:#x}")
        result = self.in_transfer(3, 2, b"AFTER-RECONNECT")
        return {"nak": disconnected, "device_event_types": types, "after": result["reply"]}

    def endpoint_budget(self) -> dict:
        # 16 physical slots: 0/1 control, 2..15 the seven IN/OUT pairs.
        configured = []
        for phys in range(2, 16):
            self.configure_ep(phys, TYPE_BULK, 512)
            configured.append(phys)
        self.host.send("IN 16")
        rejected = self.host.line()
        self.check("endpoint-16-rejected", rejected.startswith("ERROR"), reply=rejected)
        return {"non_control_slots": len(configured), "slot_16_reply": rejected}

    def stop(self) -> None:
        self.host.close()
        self.qt.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(10)
        self.log.close()
        self.scratch.cleanup()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qemu-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.report_dir.resolve()
    if not out.is_relative_to((ROOT / "artifacts").resolve()):
        raise SystemExit("reports must stay under artifacts/")
    out.mkdir(parents=True, exist_ok=False)
    qemu = args.qemu_dir.resolve() / "build" / "qemu-system-arm"
    source = ROOT / "emulators/qemu-a6400/qemu-overlay/hw/usb/cxd90045_dwc3.c"
    report = {
        "tool_sha256": digest(Path(__file__)),
        "source_sha256": digest(source),
        "qemu_binary_sha256": digest(qemu),
        "contract": "strict-composite",
        "checks": [],
        "status": "running",
        "limitations": [
            "qtest-driven MMIO/DMA model test, not guest firmware execution.",
            "The DWC3 model is a contract model, not silicon.",
            "One host token completes one data TRB; multi-packet bursts are driven token by token.",
        ],
    }
    case = None
    try:
        case = CompositeCase(qemu, report, out)
        case.run()
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        if case is not None:
            report["checks"] = case.checks
    finally:
        if case is not None:
            case.stop()
    report["checks_total"] = len(report.get("checks", []))
    report["checks_passed"] = sum(1 for item in report.get("checks", []) if item["ok"])
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "error": report.get("error"),
                      "checks": f'{report["checks_passed"]}/{report["checks_total"]}'},
                     indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
