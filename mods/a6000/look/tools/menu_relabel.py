#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 voxivoid
# From voxivoid/a6000-qemu (commit 62cf3bc) tools/menu_relabel.py.
"""menu_relabel.py - RAM-only relabel of one Creative Style row to "PP1" (A6000 fw 3.21).

Writes 4 bytes into the running im.elf (through /proc/<pid>/mem) at the label word of row 12
("Sepia") of the Creative Style picker table in viewUnified2.so (vaddr 0x4bc82c).  Only
/android (tmpfs) is written; nothing touches flash, /setting or NAND.  Reboot / battery
pull = stock.  See docs/a6000/MENU_INSERT.md.

    mods/a6000/look/tools/menu_relabel.py check   [shell opts]   read only: pid, base, table header, label word
    mods/a6000/look/tools/menu_relabel.py apply   [shell opts]   Sepia -> PP1   (refuses if the table differs)
    mods/a6000/look/tools/menu_relabel.py restore [shell opts]   PP1 -> Sepia

shell opts are passed to emulators/a6000/tools/a6kshell.py: --port N (emulator, 127.0.0.1) or, for a real
camera over the OpenMemories-Tweak telnet shell, --host H --port 23.
Needs busybox dd on the guest (no conv=notrunc; no od/hexdump: results are pulled to the host).
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SH = [sys.executable, os.path.join(HERE, 'a6kshell.py')]

TABLE = 0x4bc938                    # Creative Style picker root node (vaddr in viewUnified2.so)
CHILDREN = 0x4bc640                 # its child array
ROW = 12                            # "Sepia"
LABEL = CHILDREN + ROW * 0x28 + 0xc  # 0x4bc82c
SEPIA, PP1 = 0x0204e06b, 0x0204f6d5  # string resids (0x02000000 | string id)
EXPECT_ROOT = (CHILDREN, 19, 97)     # child ptr, count, id (static values; vaddr)


def sh(opts, cmd, check=True):
    r = subprocess.run(SH + opts + ['run', cmd], capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        sys.exit('guest command failed: %s\n%s%s' % (cmd, r.stdout, r.stderr))
    return r.stdout


def pull_bytes(opts, base, vaddr, n):
    sh(opts, 'busybox dd if=/proc/$(pidof im.elf)/mem bs=1 skip=$((%d+%d)) count=%d '
             '2>/dev/null > /android/a6k/rd.bin' % (base, vaddr, n))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'rd.bin')
        r = subprocess.run(SH + opts + ['pull', '/android/a6k/rd.bin', p],
                           capture_output=True, text=True, timeout=120)
        if r.returncode or not os.path.exists(p):
            sys.exit('pull failed: ' + r.stdout + r.stderr)
        data = open(p, 'rb').read()
    if len(data) != n:
        sys.exit('short read at 0x%x: %d of %d bytes' % (vaddr, len(data), n))
    return data


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('check', 'apply', 'restore'):
        sys.exit(__doc__)
    mode, opts = sys.argv[1], sys.argv[2:]
    sh(opts, 'mkdir -p /android/a6k')
    out = sh(opts, 'P=$(pidof im.elf); echo PID $P; '
                   'grep viewUnified2.so /proc/$P/maps | grep r-xp')
    pid = base = None
    for ln in out.splitlines():
        if ln.startswith('PID '):
            pid = int(ln.split()[1])
        elif 'r-xp' in ln:
            base = int(ln.split('-')[0], 16)
    if not pid or not base:
        sys.exit('im.elf / viewUnified2.so not found (camera app not up yet?)\n' + out)
    print('im.elf pid %d, viewUnified2.so base 0x%x' % (pid, base))

    root = struct.unpack('<IBxxxI', pull_bytes(opts, base, TABLE, 12))
    row = struct.unpack('<I', pull_bytes(opts, base, LABEL, 4))[0]
    # child pointer is relocated at load (base + CHILDREN); count and id are static
    print('root: child ptr 0x%x count %d id %d | row %d label 0x%08x' % (root[0], root[1], root[2], ROW, row))
    if root[0] != base + CHILDREN or root[1] != EXPECT_ROOT[1] or root[2] != EXPECT_ROOT[2]:
        sys.exit('REFUSING: table header differs from the 3.21 layout (not fw 3.21, or other build)')
    if mode == 'check':
        print('ok: label word is %s' % ('Sepia' if row == SEPIA else 'PP1' if row == PP1 else 'UNKNOWN'))
        return
    want, need = (PP1, SEPIA) if mode == 'apply' else (SEPIA, PP1)
    if row == want:
        print('already %s, nothing to do' % mode)
        return
    if row != need:
        sys.exit('REFUSING: label word 0x%08x is neither Sepia nor PP1' % row)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'lbl.bin')
        open(p, 'wb').write(struct.pack('<I', want))
        r = subprocess.run(SH + opts + ['push', p, '/android/a6k/lbl.bin'],
                           capture_output=True, text=True, timeout=120)
        if r.returncode or 'ok' not in r.stdout:
            sys.exit('push failed (md5 not confirmed): ' + r.stdout + r.stderr)
    sh(opts, 'busybox dd if=/android/a6k/lbl.bin of=/proc/%d/mem bs=1 seek=$((%d+%d)) 2>&1'
             % (pid, base, LABEL))
    now = struct.unpack('<I', pull_bytes(opts, base, LABEL, 4))[0]
    print('label word now 0x%08x (%s)' % (now, 'OK' if now == want else 'MISMATCH'))
    if now != want:
        sys.exit(1)
    print('open MENU > Camera tab > Creative Style (or Fn menu) and look at the last named row.')


if __name__ == '__main__':
    main()
