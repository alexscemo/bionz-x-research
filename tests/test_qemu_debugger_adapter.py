"""Tests for the QEMU cxd90045-dwc3 unix-socket camera_debugger adapter."""
import socket
import struct
import threading
import unittest

from tools.camera_debugger import (
    Debugger,
    FEATURE,
    FRAME_SIZE,
    MAGIC,
    OP_INFO,
    OP_PING,
    encode,
)
from tools.research.qemu_debugger_adapter import (
    PTP_OC_SONY_DI_EXTCMD_READ,
    PTP_OC_SONY_DI_EXTCMD_WRITE,
    PTP_OC_SONY_REQ_RECONNECT,
    PTP_RC_DEVICE_BUSY,
    open_qemu_exchange,
)
from tools.research.qemu_usb_connections import (
    CBW_STRUCT,
    CSW_SIGNATURE,
    CSW_STRUCT,
    PTP_CONTAINER_COMMAND,
    PTP_CONTAINER_DATA,
    PTP_CONTAINER_RESPONSE,
    PTP_OC_OPEN_SESSION,
    PTP_RC_OK,
)


def _mock_dwc3_server(mode: str, ready: threading.Event, stop: threading.Event):
    srv, cli = socket.socketpair()
    srv.settimeout(0.2)
    ready.set()

    def serve():
        buf = b''
        ep0_stage = 'setup'
        last_setup = b''
        in_queue = []
        bulk_out_expect = 0
        pending_cbw = None
        ptp_expect_data = False
        ptp_tx_id = 0
        ptp_op = 0
        last_frame = b'\x00' * FRAME_SIZE
        busy_once = True

        dev_msc = bytes.fromhex('12010002000000404c05a80c000101020301')
        dev_ptp = bytes.fromhex('12010002000000404c05aa0c000101020301')
        cfg_msc = bytes.fromhex('09022000010100c0fa0904000002080650000705810200020007050202000200')
        cfg_ptp = bytes.fromhex('0902270001010080fa090400000306010100070581020002000705020200020007058303200007')
        dev_desc = dev_msc if mode == 'msc' else dev_ptp
        cfg_desc = cfg_msc if mode == 'msc' else cfg_ptp

        def send_line(line: str):
            srv.sendall((line + '\n').encode('ascii'))

        def build_reply(frame: bytes) -> bytes:
            magic, ver, op, req_id, _, length, _, arg0, arg1 = struct.unpack_from('<IHHIiIIII', frame)
            assert (magic, ver) == (MAGIC, 1)
            payload = b'pong' if op == OP_PING else b'qemu-a6400-debugger'
            hdr = struct.pack('<IHHIiIIII', MAGIC, 1, op, req_id, 0, len(payload), 0, arg0, arg1)
            return (hdr + payload).ljust(FRAME_SIZE, b'\x00')

        def handle_ptp_container(raw: bytes):
            nonlocal ptp_expect_data, ptp_tx_id, ptp_op, last_frame, busy_once
            clen, ctype, code, tx_id = struct.unpack_from('<IHHI', raw, 0)
            if ctype == PTP_CONTAINER_COMMAND:
                ptp_tx_id = tx_id
                ptp_op = code
                if code == PTP_OC_OPEN_SESSION:
                    in_queue.append(struct.pack('<IHHI', 12, PTP_CONTAINER_RESPONSE, PTP_RC_OK, tx_id))
                elif code == PTP_OC_SONY_DI_EXTCMD_WRITE:
                    ptp_expect_data = True
                elif code == PTP_OC_SONY_DI_EXTCMD_READ:
                    if busy_once:
                        busy_once = False
                        in_queue.append(struct.pack('<IHHI', 12, PTP_CONTAINER_RESPONSE, PTP_RC_DEVICE_BUSY, tx_id))
                    else:
                        reply = build_reply(last_frame)
                        in_queue.append(struct.pack('<IHHI', 12 + len(reply), PTP_CONTAINER_DATA, code, tx_id) + reply)
                        in_queue.append(struct.pack('<IHHI', 12, PTP_CONTAINER_RESPONSE, PTP_RC_OK, tx_id))
                elif code == PTP_OC_SONY_REQ_RECONNECT:
                    in_queue.append(struct.pack('<IHHI', 12, PTP_CONTAINER_RESPONSE, PTP_RC_OK, tx_id))
            elif ctype == PTP_CONTAINER_DATA and ptp_expect_data:
                ptp_expect_data = False
                last_frame = raw[12:]
                in_queue.append(struct.pack('<IHHI', 12, PTP_CONTAINER_RESPONSE, PTP_RC_OK, ptp_tx_id))

        bulk_buf = bytearray()
        try:
            while not stop.is_set():
                while b'\n' not in buf:
                    try:
                        chunk = srv.recv(65536)
                    except socket.timeout:
                        if stop.is_set():
                            return
                        continue
                    if not chunk:
                        return
                    buf += chunk
                line_b, buf = buf.split(b'\n', 1)
                line = line_b.decode('ascii')
                if line in ('CONN', 'RESET', 'DISC'):
                    continue
                if line.startswith('SETUP '):
                    last_setup = bytes.fromhex(line.split()[1])
                    ep0_stage = 'data_in' if (last_setup[0] & 0x80) else 'status_in'
                    send_line('ACK 0 8')
                elif line == 'IN 1':
                    if ep0_stage == 'data_in':
                        b_req = last_setup[1]
                        w_val = int.from_bytes(last_setup[2:4], 'little')
                        w_len = int.from_bytes(last_setup[6:8], 'little')
                        if b_req == 6 and (w_val >> 8) == 1:
                            send_line('IN 1 ' + dev_desc[:w_len].hex())
                        elif b_req == 6 and (w_val >> 8) == 2:
                            send_line('IN 1 ' + cfg_desc[:w_len].hex())
                        elif b_req == 8:
                            send_line('IN 1 01')
                    else:
                        send_line('IN 1 ')
                elif line == 'OUT 0 -':
                    send_line('ACK 0 0')
                elif line.startswith('OUT 4 '):
                    data = bytes.fromhex(line.split()[2])
                    send_line(f'ACK 4 {len(data)}')
                    if mode == 'msc':
                        if bulk_out_expect > 0:
                            bulk_buf.extend(data)
                            if len(bulk_buf) >= bulk_out_expect:
                                last_frame = bytes(bulk_buf[:FRAME_SIZE])
                                bulk_buf.clear()
                                bulk_out_expect = 0
                                in_queue.append(CSW_STRUCT.pack(CSW_SIGNATURE, pending_cbw[1], 0, 0))
                        else:
                            sig, tag, xfer_len, flags, lun, cb_len, cdb = CBW_STRUCT.unpack(data)
                            if cdb[0] == 0x7A and not (flags & 0x80):
                                pending_cbw = (sig, tag, xfer_len)
                                bulk_out_expect = xfer_len
                                bulk_buf.clear()
                            elif cdb[0] == 0x7A and (flags & 0x80):
                                reply = build_reply(last_frame)
                                for off in range(0, len(reply), 512):
                                    in_queue.append(reply[off:off + 512])
                                in_queue.append(CSW_STRUCT.pack(CSW_SIGNATURE, tag, 0, 0))
                    else:
                        bulk_buf.extend(data)
                        if len(bulk_buf) >= 12:
                            clen = struct.unpack_from('<I', bulk_buf, 0)[0]
                            if len(bulk_buf) >= clen:
                                pkt = bytes(bulk_buf[:clen])
                                del bulk_buf[:clen]
                                handle_ptp_container(pkt)
                elif line == 'IN 3':
                    if in_queue:
                        pkt = in_queue.pop(0)
                        send_line('IN 3 ' + pkt.hex())
                    else:
                        send_line('NAK 3')
        finally:
            srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return cli, t


class QemuDebuggerAdapterTests(unittest.TestCase):
    def _run_mode(self, mode: str):
        ready = threading.Event()
        stop = threading.Event()
        cli, thread = _mock_dwc3_server(mode, ready, stop)
        ready.wait(timeout=2.0)

        # Monkeypatch socket.socket.connect in open_qemu_exchange via socketpair
        orig_socket = socket.socket

        class _PairWrapper:
            def __init__(self, *args, **kwargs):
                self._s = cli
            def settimeout(self, t):
                self._s.settimeout(t)
            def connect(self, path):
                pass
            def sendall(self, data):
                self._s.sendall(data)
            def recv(self, n):
                return self._s.recv(n)
            def close(self):
                self._s.close()

        socket.socket = _PairWrapper
        try:
            with open_qemu_exchange('/tmp/mock-dwc3.sock', mode='auto') as (ex, details):
                self.assertEqual(details['mode'], mode)
                dbg = Debugger(ex)
                self.assertEqual(dbg.ping(), 'pong')
                info = dbg.request(OP_INFO)['payload']
                self.assertEqual(info, b'qemu-a6400-debugger')
                if mode == 'ptp':
                    sw = ex.switch_to_msc()
                    self.assertEqual(sw['response_code'], PTP_RC_OK)
        finally:
            socket.socket = orig_socket
            stop.set()
            cli.close()
            thread.join(timeout=2.0)

    def test_msc_debugger_exchange(self):
        self._run_mode('msc')

    def test_ptp_debugger_exchange_with_busy_retry_and_switch(self):
        self._run_mode('ptp')


if __name__ == '__main__':
    unittest.main()
