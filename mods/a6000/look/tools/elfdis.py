#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 voxivoid
# From voxivoid/a6000-qemu (commit 62cf3bc) tools/elfdis.py.
"""Thumb-disassemble ranges of an ARM32 ELF by virtual address (needs capstone).

usage: elfdis.py <elf> <hex vaddr>:<len bytes> ...
"""
import sys,struct,capstone
f=sys.argv[1]; d=open(f,'rb').read()
# parse PT_LOAD
e_phoff=struct.unpack_from('<I',d,0x1c)[0]; n=struct.unpack_from('<H',d,0x2c)[0]
segs=[]
for i in range(n):
    t,off,va,pa,fs,ms,fl,al=struct.unpack_from('<8I',d,e_phoff+i*32)
    if t==1: segs.append((va,off,fs))
def fo(a):
    for va,off,fs in segs:
        if va<=a<va+fs: return a-va+off
md=capstone.Cs(capstone.CS_ARCH_ARM,capstone.CS_MODE_THUMB)
for arg in sys.argv[2:]:
    a,n=arg.split(':'); a=int(a,16)&~1; n=int(n)
    print('--',hex(a))
    for i in md.disasm(d[fo(a):fo(a)+n],a): print("%x: %s %s"%(i.address,i.mnemonic,i.op_str))
