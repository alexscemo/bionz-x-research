# SPDX-License-Identifier: MIT
# Copyright (c) 2026 voxivoid
# From voxivoid/a6000-qemu (commit 62cf3bc) tools/port/port_scan.py.
"""Find the two ISP setters (gamma table, colour matrix) in a CXD90014 av-cam.bin, to port the look mod to other bodies.

usage: port_scan.py <av-cam.bin> <model> <fw>          e.g. port_scan.py av-cam.bin ILCE-6000 3.21

av-cam is position independent and its load address lives in the camera's own Backup.bin, not in the updater, so this
reports *image offsets*. The PA hint assumes the A6000's load PA 0x83541000; confirm the base on the camera
(mods/a6000/look/check_map.sh) before writing. Prints one C row, or "NO ROW: <why>" and exit 1 when anything is missing or ambiguous.

Gamma: the leaf `add.w r0,r0,#0x15000; mov.w r2,#0x180; add.w r0,r0,#0x300; b.w memcpy` (bytes unique per image).
Matrix: the function that calls the register accessors of every colour-matrix word 0x1550c..0x1552c (found by what it
calls, never by its prologue, which is the commonest one in the image).
"""
import capstone, re, sys

VB = 0x43541000                  # nominal VA used for disassembly; BL targets are relative, so any base works
A6000_PA = 0x83541000
GAMMA_LEAF = bytes.fromhex('00f5a8304ff4c07200f54070')
MATRIX_WORDS = list(range(0x1550c, 0x15530, 4))
SIG = 32


def fail(why):
    print('NO ROW: ' + why)
    sys.exit(1)


def accessors(ins):
    """VA -> (offset, 'R'|'W') for leaf accessors `add.w r0,r0,#X [add.w ...] ldr/str rN,[r0{,#Y}]`."""
    acc = {}
    i = 0
    while i < len(ins) - 1:
        a = ins[i]
        m = re.match(r'r0, r0, #(0x[0-9a-f]+)$', a.op_str) if a.mnemonic == 'add.w' else None
        if m:
            off = int(m.group(1), 16); j = i + 1
            while j < len(ins) and ins[j].mnemonic == 'add.w' and re.match(r'r0, r0, #', ins[j].op_str):
                off += int(ins[j].op_str.split('#')[1], 16); j += 1
            if j < len(ins) and ins[j].mnemonic in ('ldr', 'str', 'ldrh', 'strh', 'ldrb', 'strb'):
                mm = re.search(r'\[r0(, #(0x[0-9a-f]+))?\]', ins[j].op_str)
                if mm:
                    off += int(mm.group(2), 16) if mm.group(2) else 0
                    acc[a.address] = (off, 'R' if ins[j].mnemonic.startswith('ldr') else 'W')
                    i = j; continue
        i += 1
    return acc


def main():
    if len(sys.argv) != 4:
        print(__doc__); sys.exit(64)
    path, model, fw = sys.argv[1:]
    d = open(path, 'rb').read()

    hits = [m.start() for m in re.finditer(re.escape(GAMMA_LEAF), d)]
    if len(hits) != 1:
        fail('gamma leaf found %d times' % len(hits))
    g_off = hits[0]

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB); md.skipdata = True
    ins = list(md.disasm(d, VB))
    acc = accessors(ins)

    # the gamma leaf must tail-call the same memcpy the rest of the image uses for MMIO windows
    gi = next(i for i in md.disasm(d[g_off + 12:g_off + 16], VB + g_off + 12))
    if gi.mnemonic != 'b.w':
        fail('gamma leaf does not end in b.w (%s %s)' % (gi.mnemonic, gi.op_str))

    # functions (push ..lr .. next push) and the matrix-word writers each one calls
    fns = []; cur = None
    for i in ins:
        if i.mnemonic in ('push', 'push.w') and 'lr' in i.op_str:
            cur = [i.address, set()]; fns.append(cur)
        if cur and i.mnemonic in ('bl', 'b.w') and i.op_str.startswith('#'):
            t = int(i.op_str[1:], 16)
            if t in acc and acc[t][1] == 'W':
                cur[1].add(acc[t][0])
    want = set(MATRIX_WORDS)
    cands = [f for f, offs in fns if want <= offs]
    if len(cands) != 1:
        fail('matrix setter candidates: %s' % [hex(c) for c in cands])
    m_off = cands[0] - VB

    for name, off in (('gamma', g_off), ('matrix', m_off)):
        sig = d[off:off + SIG]
        n = len([x for x in re.finditer(re.escape(sig), d)])
        if n != 1:
            fail('%s signature occurs %d times' % (name, n))

    # other code that copies into the gamma window (an ISP-init path the setter patch does not stop)
    memcpy = int(gi.op_str[1:], 16)
    win = 0
    for k, i in enumerate(ins):
        if i.mnemonic in ('b.w', 'bl') and i.op_str == '#%#x' % memcpy:
            back = ins[max(0, k - 6):k]
            if any(p.op_str.endswith('#0x15000') for p in back) and any(p.op_str.endswith('#0x300') for p in back):
                win += 1

    print('/* %s %s: av-cam %d B; gamma leaf +%#x, matrix setter +%#x; %d gamma-window copies; PA hint for base %#x */'
          % (model, fw, len(d), g_off, m_off, win, A6000_PA))
    def sig(b):                          # C initialiser: two lines of 16 bytes
        return ('{ ' + ', '.join('0x%02x' % x for x in b[:16]) + ',\n          '
                + ', '.join('0x%02x' % x for x in b[16:]) + ' }')
    print('    { "%s", "%s", { %#x, %#x },\n      { %s,\n        %s },\n      0, 0xF3215300UL, 0xF321550CUL, 0 },'
          % (model, fw, g_off, m_off, sig(d[g_off:g_off + SIG]), sig(d[m_off:m_off + SIG])))


if __name__ == '__main__':
    main()
