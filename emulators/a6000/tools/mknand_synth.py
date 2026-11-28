#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Camera-free NAND image for the sony-a6000 emulator (not part of
# voxivoid/a6000-qemu, which the scripts around it come from).
"""Build a logical NAND image from the official updater only, no camera dump.

    tools/mknand_synth.py state/synth.bin [--fw DIR] [--wbi DIR] [--region CX77500_UC2]
    ./provision.sh state/synth.bin state/synth.nand
    python3 tools/inputs.py init --nand state/synth.nand --fw firmware
    ./run.sh

The partition table, the partition images, the factory-default Backup.bin and
the setting and lens files come from your copy of Sony's updater (--fw, default
$A6K_FW or firmware/). /setting/DmmConfig.bin (the dmm.ko memory map) and
/setting/ulogio.bin (osal_ulogio setting data) are not in the updater as files;
they are rebuilt from what dmm.ko and osal_ulogio.ko hold in the updater's
factory warm-boot image (--wbi, default data/wbi from tools/mkdata.py). The
per-unit data a real camera holds (calibration, updater core, personal data) is
left out. The output holds Sony data: keep it local. Needs mkfs.vfat.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

EMULATOR = Path(__file__).resolve().parent.parent
TREE = "unpacked/firmware.tar_unpacked"
TABLE_MAGIC = b"82461.00"
CLUSTER_SECTORS = 32            # mount.conf: 16 KiB clusters


def partitions(tree: Path) -> list[tuple[int, int, int, int]]:
    """(start, size, type, flags) rows of partinf.conf, in partition order."""
    text = (tree / "0300_partconf/partinf.conf").read_text(encoding="latin-1")
    rows = [line.strip() for line in text.splitlines()]
    return [tuple(int(value, 0) for value in row.split(","))
            for row in rows if row and not row.startswith("#")]


def table_sector(rows: list[tuple[int, int, int, int]]) -> bytes:
    sector = bytearray(512)
    sector[:8] = TABLE_MAGIC
    struct.pack_into("<I", sector, 8, len(rows))
    for index, row in enumerate(rows):
        struct.pack_into("<4I", sector, 0x20 + 16 * index, *row)
    return bytes(sector)


class Fat:
    """Add files to a freshly formatted FAT12/16 volume (long names kept)."""

    def __init__(self, image: bytearray):
        self.image = image
        bps, spc, reserved, fats, roots, total16 = struct.unpack_from("<HBHBHH", image, 11)
        fat_sectors = struct.unpack_from("<H", image, 22)[0]
        total = total16 or struct.unpack_from("<I", image, 32)[0]
        self.cluster = bps * spc
        self.fat_offsets = [(reserved + i * fat_sectors) * bps for i in range(fats)]
        root = (reserved + fats * fat_sectors) * bps
        self.root = [(root, roots * 32)]
        self.data = root + roots * 32
        self.bits = 12 if (total - self.data // bps) // spc < 4085 else 16
        self.next = 2
        self.serial = 0

    def _link(self, cluster: int, value: int) -> None:
        for base in self.fat_offsets:
            if self.bits == 16:
                struct.pack_into("<H", self.image, base + 2 * cluster, value & 0xFFFF)
                continue
            at = base + cluster * 3 // 2
            old = struct.unpack_from("<H", self.image, at)[0]
            new = (old & 0x000F) | (value & 0xFFF) << 4 if cluster & 1 \
                else (old & 0xF000) | (value & 0xFFF)
            struct.pack_into("<H", self.image, at, new)

    def _allocate(self, data: bytes) -> int:
        count = max(1, -(-len(data) // self.cluster))
        first = self.next
        self.next += count
        start = self.data + (first - 2) * self.cluster
        if start + count * self.cluster > len(self.image):
            raise ValueError("partition full")
        self.image[start:start + len(data)] = data
        for cluster in range(first, first + count):
            self._link(cluster, 0xFFFF if cluster == first + count - 1 else cluster + 1)
        return first

    def _slot(self, directory: list[tuple[int, int]], count: int) -> int:
        for offset, size in directory:
            run = 0
            for at in range(offset, offset + size, 32):
                run = run + 1 if self.image[at] == 0 else 0
                if run == count:
                    return at - 32 * (count - 1)
        raise ValueError("directory full")

    def _entry(self, directory, name: str, attribute: int, cluster: int, size: int) -> None:
        self.serial += 1
        short = f"F{self.serial:05d}~1BIN".encode()
        checksum = 0
        for byte in short:
            checksum = (((checksum & 1) << 7) + (checksum >> 1) + byte) & 0xFF
        units = name.encode("utf-16-le")
        if len(units) % 26:
            units += b"\0\0"
        units += b"\xff" * (-len(units) % 26)
        pieces = [units[i:i + 26] for i in range(0, len(units), 26)]
        at = self._slot(directory, len(pieces) + 1)
        for order in range(len(pieces), 0, -1):
            piece = pieces[order - 1]
            flag = order | (0x40 if order == len(pieces) else 0)
            self.image[at:at + 32] = (bytes([flag]) + piece[:10] + bytes([0x0F, 0, checksum])
                                      + piece[10:22] + b"\0\0" + piece[22:])
            at += 32
        self.image[at:at + 32] = struct.pack("<11sB10xHHHI", short, attribute,
                                             0, 0x21, cluster, size)

    def add_file(self, name: str, data: bytes, directory=None) -> None:
        cluster = self._allocate(data) if data else 0
        self._entry(directory or self.root, name, 0x20, cluster, len(data))

    def add_dir(self, name: str) -> list[tuple[int, int]]:
        cluster = self._allocate(bytes(self.cluster))
        start = self.data + (cluster - 2) * self.cluster
        for slot, (dots, target) in enumerate(((b".", cluster), (b"..", 0))):
            self.image[start + 32 * slot:start + 32 * slot + 32] = struct.pack(
                "<11sB10xHHHI", dots.ljust(11), 0x10, 0, 0x21, target, 0)
        self._entry(self.root, name, 0x10, cluster, 0)
        return [(start, self.cluster)]


def blank_fat(size: int) -> Fat:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "fat.img"
        with path.open("wb") as stream:
            stream.truncate(size)
        subprocess.run(["mkfs.vfat", "-s", str(CLUSTER_SECTORS), "-i", "a6000000", str(path)],
                       check=True, stdout=subprocess.DEVNULL)
        fat = Fat(bytearray(path.read_bytes()))
    return fat


class Wbi:
    """Kernel virtual memory of the updater's warm-boot image (tools/mkdata.py output)."""

    TTB = 0x80034000

    def __init__(self, directory: Path):
        self.ram = (directory / "ram.bin").read_bytes()
        self.sections = [tuple(int(field, 16) for field in line.split())
                         for line in (directory / "map.tsv").read_text().splitlines() if line]

    def _physical(self, address: int, length: int) -> bytes:
        for offset, base, _, size in self.sections:
            if base <= address and address + length <= base + size:
                start = offset + address - base
                return self.ram[start:start + length]
        raise ValueError(f"PA {address:#x} not in the WBI")

    def _translate(self, va: int) -> int:
        """ARMv7 short-descriptor walk through the snapshot's page tables."""
        l1 = struct.unpack("<I", self._physical(self.TTB + (va >> 20) * 4, 4))[0]
        if l1 & 3 == 2:
            return (l1 & 0xFFF00000) | (va & 0xFFFFF)
        if l1 & 3 == 1:
            l2 = struct.unpack("<I", self._physical((l1 & 0xFFFFFC00) + ((va >> 12) & 0xFF) * 4, 4))[0]
            if l2 & 2:
                return (l2 & 0xFFFFF000) | (va & 0xFFF)
            if l2 & 3 == 1:
                return (l2 & 0xFFFF0000) | (va & 0xFFFF)
        raise ValueError(f"VA {va:#x} not mapped in the WBI")

    def read(self, va: int, length: int) -> bytes:
        out = bytearray()
        while length:
            step = min(length, 0x1000 - (va & 0xFFF))
            out += self._physical(self._translate(va), step)
            va += step
            length -= step
        return bytes(out)


# dmm.ko (firmware 3.21) keeps the tables it parsed from /setting/DmmConfig.bin
# in its .bss, which sits at this VA in the factory WBI. Offsets are from
# dmm_ext_tbl_Init, dmm_unit_tbl_Init and dmm_config_SetVerInfo.
DMM_BSS = 0x3F0F4078
DMM_EXT_TABLES = ((0x78620, 0x78120), (0x84EC0, 0x786C0))   # (80 B groups, 20 B entries)
DMM_UNITS, DMM_UNIT_IDS, DMM_VERSION = 0x539E8, 0x509E8, 0x30FFA
END = 0xFFFFFFFF


def dmm_config(wbi: Wbi) -> bytes:
    """Serialize dmm's in-memory tables back into the DmmConfig.bin file format.

    The file is the record stream dmm reads in order: two group tables (80-byte
    group headers, each followed by 20-byte entries ending in one whose word 2 is
    -1, the table ending in a header whose word 2 is -1), the unit table (8-byte
    records each followed by u32 ids ending in -1, ending in a record whose
    word 0 is -1) and a 32-byte version string. dmm overwrites header word 0 and
    unit word 1 with pointers; the file holds 0 there.
    """
    words = lambda data: list(struct.unpack(f"<{len(data) // 4}I", data))
    pack = lambda values: struct.pack(f"<{len(values)}I", *values)
    out = bytearray()
    for groups, entries in DMM_EXT_TABLES:
        group, entry = DMM_BSS + groups, DMM_BSS + entries
        while True:
            header = words(wbi.read(group, 80))
            group += 80
            header[0] = 0
            out += pack(header)
            if header[2] == END:
                break
            while True:
                record = wbi.read(entry, 20)
                entry += 20
                out += record
                if words(record)[2] == END:
                    break
    unit, ids = DMM_BSS + DMM_UNITS, DMM_BSS + DMM_UNIT_IDS
    while True:
        record = words(wbi.read(unit, 8))
        unit += 8
        record[1] = 0
        out += pack(record)
        if record[0] == END:
            break
        while True:
            value = wbi.read(ids, 4)
            ids += 4
            out += value
            if words(value)[0] == END:
                break
    version = wbi.read(DMM_BSS + DMM_VERSION, 32)
    if not version.startswith(b"for "):
        raise ValueError("WBI has no dmm config tables at the 3.21 address")
    return bytes(out + version)


def ulogio_config(wbi: Wbi) -> bytes:
    """/setting/ulogio.bin: the osal_ulogio setting data, kept verbatim in the WBI.

    A chain of <u32 length, 4-byte tag, data> records starting with the LGNM
    (log name) record and ending before a zero length. Without the file the
    first request of dmm.ko at load returns no shared area and the kernel oopses.
    """
    found = []
    at = wbi.ram.find(b"LGNM")
    while at >= 0:
        start = at - 4
        position, length = start, None
        while start >= 0 and position + 4 <= len(wbi.ram):
            length = struct.unpack_from("<I", wbi.ram, position)[0]
            if not 8 <= length <= 0x1000:
                break
            position += length
        if position > start and length == 0:
            found.append(wbi.ram[start:position])
        at = wbi.ram.find(b"LGNM", at + 1)
    if len(found) != 1:
        raise ValueError(f"WBI: expected one ulogio setting block, found {len(found)}")
    return found[0]


def setting_partition(tree: Path, size: int, region: str, wbi: Wbi) -> bytes:
    matches = sorted((tree / "0110_backup").rglob(f"{region}.bin"))
    if not matches:
        raise ValueError(f"no default Backup for region {region}")
    backup = matches[0].read_bytes()
    fat = blank_fat(size)
    fat.add_file("Backup.bin", backup)
    fat.add_file("Backup.bak", backup)
    fat.add_file("DmmConfig.bin", dmm_config(wbi))
    fat.add_file("ulogio.bin", ulogio_config(wbi))
    dirs = {"appfw_infra", "gps", "mode", "updater"}
    for path in sorted((tree / "0800_appli/setting").iterdir()):
        if path.is_file():       # directories here are fwtool unpack output
            fat.add_file(path.name, path.read_bytes())
    for name in sorted(dirs):
        fat.add_dir(name)
    return bytes(fat.image)


def default_region(tree: Path) -> str:
    last = (tree / "0110_backup/MainDest.txt").read_text(encoding="latin-1").split()[-1]
    return last.removesuffix(".bin")


def build(tree: Path, out: Path, region: str, wbi: Wbi) -> None:
    rows = partitions(tree)
    images = tree / "0700_part_image/dev"
    end = max(start + size for start, size, _, _ in rows)
    with out.open("wb") as stream:
        stream.truncate(end)
        stream.write(table_sector(rows))
        for number, (start, size, _, _) in enumerate(rows, 1):
            if not size:
                continue
            shipped = images / f"nflasha{number}"
            if shipped.is_file():
                data = shipped.read_bytes()
            elif number == 2:
                data = setting_partition(tree, size, region, wbi)
            elif number == 4:
                # MPR Flash Backup: records libmpr writes at run time.
                # endupdate.sh erases it (ud_lerase) after every update,
                # so erased flash is the state a new camera starts from.
                data = b"\xff" * size
            else:
                fat = blank_fat(size)
                if number == 10:
                    fat.add_file("config.spec",
                                 (tree / "0800_appli/tmp/config.spec").read_bytes())
                elif number == 18:
                    for path in sorted((tree / "0800_appli/lens").iterdir()):
                        fat.add_file(path.name, path.read_bytes())
                data = bytes(fat.image)
            if len(data) > size:
                raise ValueError(f"nflasha{number} image exceeds its partition")
            stream.seek(start)
            stream.write(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fw", type=Path,
                        default=Path(os.environ.get("A6K_FW") or EMULATOR / "firmware"),
                        help="unpacked updater (default: $A6K_FW or firmware/)")
    parser.add_argument("--wbi", type=Path, default=EMULATOR / "data/wbi",
                        help="warm-boot image from tools/mkdata.py (default: data/wbi)")
    parser.add_argument("--region", help="default Backup name (default: MainDest.txt)")
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    tree = args.fw / TREE
    try:
        build(tree, args.out, args.region or default_region(tree), Wbi(args.wbi))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"mknand_synth: {error}", file=sys.stderr)
        return 1
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
