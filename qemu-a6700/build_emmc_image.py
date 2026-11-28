#!/usr/bin/env python3
"""Build a local eMMC user-area image for the sony-a6700 QEMU machine from partition images.

Layout (derived from the camera's emmc.ko disassembly, E9202 section 4c):
  sector 0   : Sony partition table: magic "8246", version "2.00", n_partition, then one
               16-byte entry per partition at +0x20 {start, size, type, flag} (sectors),
               taken from partinf.conf; fields +12..+31 are left zero (purpose unknown).
  start..    : each partition image at its start sector; a missing image stays zero.

Inputs are camera-derived and stay local (git-ignored artifacts/). The output is a sparse file.
usage: build_emmc_image.py <partinf.conf> <images-dir> <output.img> [size-MiB, default 2048]
"""
import re
import struct
import sys

MAGIC = b"8246"
VERSION = b"2.00"
PART_MAX = 30  # emmc.ko rejects n_partition above this


def parse_partinf(path):
    entries = []
    with open(path, "r", encoding="latin-1") as handle:
        for line in handle:
            match = re.fullmatch(r"\s*(0x[0-9A-Fa-f]+),(0x[0-9A-Fa-f]+),(0x[0-9A-Fa-f]+),(0x[0-9A-Fa-f]+)\s*", line)
            if match:
                entries.append(tuple(int(group, 16) for group in match.groups()))
    return entries


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    conf, images, out = sys.argv[1:4]
    size_mib = int(sys.argv[4]) if len(sys.argv) > 4 else 2048
    entries = parse_partinf(conf)
    if not 0 < len(entries) <= PART_MAX:
        sys.exit("unexpected partition count: %d" % len(entries))
    end = max(start + size for start, size, _, _ in entries) * 512
    if end > size_mib * 1024 * 1024:
        sys.exit("image too small: need %d MiB" % (end >> 20 or 1))
    table = bytearray(512)
    table[0:4] = MAGIC
    table[4:8] = VERSION
    struct.pack_into("<I", table, 8, len(entries))
    for index, (start, size, kind, flag) in enumerate(entries):
        struct.pack_into("<IIII", table, 32 + 16 * index, start, size, kind, flag & 0xFFFFFFFF)
    with open(out, "wb") as image:
        image.truncate(size_mib * 1024 * 1024)
        image.seek(0)
        image.write(table)
        for index, (start, size, _, _) in enumerate(entries, 1):
            if size == 0:
                continue
            try:
                part = open("%s/nflasha%d.bin" % (images, index), "rb")
            except FileNotFoundError:
                print("nflasha%d: no image, left zero" % index)
                continue
            with part:
                data = part.read()
            if len(data) > size * 512:
                sys.exit("nflasha%d image larger than its partition" % index)
            image.seek(start * 512)
            image.write(data)
            print("nflasha%d: %d bytes at sector 0x%X" % (index, len(data), start))


if __name__ == "__main__":
    main()
