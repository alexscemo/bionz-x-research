#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 voxivoid
# From voxivoid/a6000-qemu (commit 62cf3bc) tools/menutree.py.
"""Dump a camera menu node tree (0x28-byte nodes) from an ARM32 ELF; see docs/a6000/MENU_INSERT.md section 1.

usage: menutree.py <elf> <hex vaddr of root node>
"""
import sys,struct
f=sys.argv[1]; d=open(f,'rb').read()
e_phoff=struct.unpack_from('<I',d,0x1c)[0]; n=struct.unpack_from('<H',d,0x2c)[0]
segs=[]
for i in range(n):
    t,off,va,pa,fs,ms,fl,al=struct.unpack_from('<8I',d,e_phoff+i*32)
    if t==1: segs.append((va,off,fs))
def fo(a):
    for va,off,fs in segs:
        if va<=a<va+fs: return a-va+off
def u32(a): return struct.unpack_from('<I',d,fo(a))[0]
def dump(a,depth=0,seen=None):
    o=fo(a)
    if o is None: print(' '*depth,'??',hex(a)); return
    raw=d[o:o+0x28]
    w=struct.unpack('<10I',raw)
    ptr,cnt=w[0],raw[4]
    print('%s@%x ptr=%x cnt=%d id=%d exec=%d opts=%d  raw=%s'%(' '*depth,a,ptr,cnt,w[2],raw[0x1d],w[8],' '.join('%x'%x for x in w)))
    if cnt and ptr and depth<6 and fo(ptr) is not None:
        for i in range(cnt): dump(ptr+0x28*i,depth+2)
if __name__=='__main__':
    dump(int(sys.argv[2],16))
