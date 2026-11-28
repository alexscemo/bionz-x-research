#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# From voxivoid/a6000-qemu (commit 62cf3bc) tools/mon.py, which adapts Oein/a5000-emulator (GPL-2.0, base commit 6ad36bd) to the ILCE-6000.
# docs/reference/ paths in comments are files in voxivoid/a6000-qemu.
"""Send HMP commands to a running emulator: mon.py <port|sock> 'cmd' ['cmd'...]"""
import socket, sys, time
a = sys.argv[1]
if a.isdigit(): s = socket.create_connection(("127.0.0.1", int(a)))
else: s = socket.socket(socket.AF_UNIX); s.connect(a)
s.settimeout(3)
def rd():
    b = b''
    try:
        while not b.endswith(b'(qemu) '):
            c = s.recv(65536)
            if not c: break
            b += c
    except socket.timeout: pass
    return b.decode(errors='replace')
rd()
for c in sys.argv[2:]:
    s.sendall(c.encode() + b'\n'); out = rd()
    print('\n'.join(out.splitlines()[1:-1]) if out.count('\n') else out)
