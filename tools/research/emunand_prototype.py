"""Build, statically audit, and package the reversible CXD90045 EmuNAND prototype.

Strictly emulator/offline tooling:
  - Compiles emunand_guard.ko and freestanding emunand_vdisk_ctl.elf via WSL ARM GCC
    without embedding any proprietary firmware carrier bytes.
  - Audits emunand_guard.ko ELF relocations, vermagic, this_module layout, and
    undefined symbol imports against verified CXD90045 vmlinux exports.
  - Generates deterministic ext2 VDisk images (CFW_VDISK.IMG) and FAT32/exFAT
    virtual SD card images (raw partition or MBR-partitioned SDSC/SDHC) for
    Unicorn and QEMU sony-a6400 testing.
  - Never accesses USB, /dev/mem, or physical camera/SD media.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
from typing import Mapping, Sequence

from elftools.elf.elffile import ELFFile

from project_paths import PROJECT_ROOT


EMUNAND_SRC_DIR = PROJECT_ROOT / "tools" / "native_camera" / "emunand"
EXPECTED_VERMAGIC = "vermagic=3.0.27_nl-rt106+ SMP preempt mod_unload ARMv7 "
THIS_MODULE_SIZE = 316
THIS_MODULE_NAME_OFFSET = 12
THIS_MODULE_NAME_LEN = 60
THIS_MODULE_INIT_OFFSET = 0xBC
THIS_MODULE_EXIT_OFFSET = 0x134

ALLOWED_KO_IMPORTS = frozenset({
    "kallsyms_lookup_name",
    "stop_machine",
    "vmalloc",
    "vfree",
    "proc_create_data",
    "remove_proc_entry",
    "__copy_to_user",
    "__copy_from_user",
    "scnprintf",
    "strncmp",
    "memcpy",
    "memset",
    "__memzero",
    "printk",
    "msleep",
})


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def to_wsl_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return str(resolved)
    drive = resolved.drive.rstrip(":").lower()
    tail = resolved.as_posix().split(":/", 1)[1]
    return f"/mnt/{drive}/{tail}"


def audit_ko_module(ko_path: Path) -> dict[str, object]:
    """Fail-closed static audit of emunand_guard.ko."""
    raw = ko_path.read_bytes()
    with ko_path.open("rb") as fh:
        elf = ELFFile(fh)
        if elf["e_machine"] != "EM_ARM" or elf["e_type"] != "ET_REL":
            raise ValueError(f"Expected ARM ET_REL module, got {elf['e_machine']} / {elf['e_type']}")

        modinfo_sec = elf.get_section_by_name(".modinfo")
        if modinfo_sec is None:
            raise ValueError("Missing .modinfo section")
        modinfo_items = [
            x.decode("ascii", "strict")
            for x in modinfo_sec.data().split(b"\x00")
            if x
        ]
        if EXPECTED_VERMAGIC not in modinfo_items:
            raise ValueError(f"Missing exact vermagic {EXPECTED_VERMAGIC!r}; got {modinfo_items}")
        if "license=GPL" not in modinfo_items:
            raise ValueError(f"Missing GPL license tag in .modinfo: {modinfo_items}")

        tm_sec = elf.get_section_by_name(".gnu.linkonce.this_module")
        if tm_sec is None or tm_sec["sh_size"] != THIS_MODULE_SIZE:
            raise ValueError("Invalid or missing 316-byte .gnu.linkonce.this_module")
        tm_data = tm_sec.data()
        raw_name = tm_data[THIS_MODULE_NAME_OFFSET : THIS_MODULE_NAME_OFFSET + THIS_MODULE_NAME_LEN]
        mod_name = raw_name.split(b"\x00", 1)[0].decode("ascii", "strict")
        if mod_name != "emunand_guard":
            raise ValueError(f"Unexpected module name {mod_name!r}")

        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            raise ValueError("Missing .symtab section")
        syms = list(symtab.iter_symbols())

        rel_tm = elf.get_section_by_name(".rel.gnu.linkonce.this_module")
        if rel_tm is None:
            raise ValueError("Missing .rel.gnu.linkonce.this_module")
        tm_relocs = {
            r["r_offset"]: syms[r["r_info_sym"]].name
            for r in rel_tm.iter_relocations()
        }
        if tm_relocs.get(THIS_MODULE_INIT_OFFSET) != "init_module":
            raise ValueError(f"Missing init_module relocation at 0xbc: {tm_relocs}")
        if tm_relocs.get(THIS_MODULE_EXIT_OFFSET) != "cleanup_module":
            raise ValueError(f"Missing cleanup_module relocation at 0x134: {tm_relocs}")

        undefined = sorted({
            s.name for s in syms
            if s["st_shndx"] == "SHN_UNDEF" and s.name
        })
        disallowed = sorted(set(undefined) - ALLOWED_KO_IMPORTS)
        if disallowed:
            raise ValueError(f"Disallowed undefined symbols in emunand_guard.ko: {disallowed}")

        defined_globals = {
            s.name for s in syms
            if s["st_shndx"] != "SHN_UNDEF" and s["st_info"]["bind"] == "STB_GLOBAL"
        }
        for req in ("init_module", "cleanup_module", "emunand_hook_seq_read", "emunand_hook_seq_write"):
            if req not in defined_globals:
                raise ValueError(f"Missing required global symbol {req!r}")

    return {
        "module_name": mod_name,
        "bytes": len(raw),
        "sha256": sha256_bytes(raw),
        "vermagic": EXPECTED_VERMAGIC,
        "undefined_symbols": undefined,
        "defined_globals": sorted(defined_globals),
        "this_module_relocs": {hex(k): v for k, v in sorted(tm_relocs.items())},
    }


def _run_shell_build(script: str) -> None:
    if os.name == "nt":
        proc = subprocess.run(
            ["wsl", "-e", "bash", "-lc", script],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        proc = subprocess.run(
            ["bash", "-lc", script],
            capture_output=True,
            text=True,
            check=False,
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Native build failed (rc={proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


def build_native_bundle(out_dir: Path, *, build_host_ctl: bool = False) -> dict[str, object]:
    """Compile emunand_guard.ko, emunand_vdisk_ctl.elf, and stage emunand_session.sh."""
    out_dir.mkdir(parents=True, exist_ok=True)
    src_wsl = to_wsl_path(EMUNAND_SRC_DIR)
    out_wsl = to_wsl_path(out_dir)

    host_cmd = ""
    if build_host_ctl:
        host_cmd = f'gcc -O2 -Wall -Wextra -Werror "{src_wsl}/emunand_vdisk_ctl.c" -o "{out_wsl}/emunand_vdisk_ctl_host"'

    script = f"""
set -euo pipefail
export PATH="$HOME/a6400-rea-tools/arm-gcc-15.2.0-prepush/bin:$PATH"
BFLAG=""
if [ -d /usr/arm-linux-gnueabi/bin ]; then
    BFLAG="-B/usr/arm-linux-gnueabi/bin"
fi
CC="$(command -v arm-linux-gnueabi-gcc)"
LD="$(command -v arm-linux-gnueabi-ld)"
STRIP="$(command -v arm-linux-gnueabi-strip)"

"$CC" $BFLAG -marm -march=armv7-a -mfloat-abi=soft -O2 \\
    -ffreestanding -fno-builtin -fno-pic -fno-stack-protector \\
    -fno-omit-frame-pointer -mapcs-frame -fno-tree-loop-distribute-patterns \\
    -Wall -Wextra -Werror \\
    -c "{src_wsl}/emunand_guard.c" -o "{out_wsl}/emunand_guard.o"

"$CC" $BFLAG -marm -march=armv7-a -mfloat-abi=soft \\
    -c "{src_wsl}/emunand_guard_trampoline.S" -o "{out_wsl}/emunand_guard_trampoline.o"

"$LD" -r --build-id=none \\
    "{out_wsl}/emunand_guard.o" "{out_wsl}/emunand_guard_trampoline.o" \\
    -o "{out_wsl}/emunand_guard.ko"

rm -f "{out_wsl}/emunand_guard.o" "{out_wsl}/emunand_guard_trampoline.o"

"$CC" $BFLAG -DMLV2_BARE_ARM -nostdlib -static -O2 \\
    -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns \\
    -fno-stack-protector -Wall -Wextra -Werror \\
    -march=armv7-a -mfloat-abi=soft \\
    -Wl,-e,_start,--build-id=none,-z,max-page-size=4096 \\
    "{src_wsl}/emunand_vdisk_ctl.c" -o "{out_wsl}/emunand_vdisk_ctl.elf"
"$STRIP" -s "{out_wsl}/emunand_vdisk_ctl.elf"

"$CC" $BFLAG -nostdlib -static -O2 \\
    -ffreestanding -fno-builtin -fno-stack-protector -Wall -Wextra -Werror \\
    -march=armv7-a -mfloat-abi=soft \\
    -Wl,-e,_start,--build-id=none,-z,max-page-size=4096 \\
    "{src_wsl}/custom_os_probe.c" -o "{out_wsl}/custom_os_probe.elf"
"$STRIP" -s "{out_wsl}/custom_os_probe.elf"
{host_cmd}
"""
    _run_shell_build(script)

    sh_text = (EMUNAND_SRC_DIR / "emunand_session.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    sh_out = out_dir / "emunand_session.sh"
    sh_out.write_bytes(sh_text.encode("utf-8"))

    ko_path = out_dir / "emunand_guard.ko"
    ctl_path = out_dir / "emunand_vdisk_ctl.elf"
    audit = audit_ko_module(ko_path)

    manifest: dict[str, object] = {
        "emunand_guard_ko": audit,
        "emunand_vdisk_ctl_elf": {
            "bytes": ctl_path.stat().st_size,
            "sha256": sha256_file(ctl_path),
        },
        "emunand_session_sh": {
            "bytes": sh_out.stat().st_size,
            "sha256": sha256_file(sh_out),
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def build_ext2_vdisk(
    files: Mapping[str, bytes],
    *,
    size_bytes: int = 262_144,
    volume_name: str = "CFW_VDISK",
) -> bytes:
    """Build a deterministic 1 KiB-block ext2 rev-1 filesystem image in pure Python."""
    block_size = 1024
    if size_bytes < 64 * 1024 or size_bytes % block_size != 0:
        raise ValueError("ext2 VDisk size_bytes must be >= 64 KiB and a multiple of 1024")
    total_blocks = size_bytes // block_size
    if total_blocks > 8192:
        raise ValueError("Single-group deterministic ext2 builder supports up to 8 MiB (8192 blocks)")

    inode_size = 128
    inodes_count = 128
    inode_table_blocks = (inodes_count * inode_size) // block_size  # 16 blocks
    # Layout (1 KiB blocks):
    #   0: boot block (zero)
    #   1: superblock
    #   2: group descriptor table
    #   3: block bitmap
    #   4: inode bitmap
    #   5..20: inode table (16 blocks)
    #   21..: data blocks
    first_meta_free_block = 5 + inode_table_blocks
    img = bytearray(size_bytes)

    next_block = first_meta_free_block
    next_inode = 12  # Inodes 1..10 reserved (root=2), 11 = lost+found

    def alloc_block() -> int:
        nonlocal next_block
        if next_block >= total_blocks:
            raise ValueError("ext2 VDisk image out of data blocks; increase size_bytes")
        b = next_block
        next_block += 1
        return b

    def alloc_inode() -> int:
        nonlocal next_inode
        if next_inode > inodes_count:
            raise ValueError("ext2 VDisk image out of inodes")
        ino = next_inode
        next_inode += 1
        return ino

    # Build directory tree structure: node -> dict[name, child]
    dirs: dict[str, dict[str, tuple[str, str]]] = {"": {}}
    norm_files: dict[str, bytes] = {}
    for raw_path, payload in sorted(files.items()):
        p = PurePosixPath(raw_path.strip("/"))
        if not p.parts or any(part in ("", ".", "..") for part in p.parts):
            raise ValueError(f"Invalid relative VDisk path: {raw_path!r}")
        cur = ""
        for part in p.parts[:-1]:
            child = f"{cur}/{part}" if cur else part
            dirs.setdefault(cur, {})[part] = ("dir", child)
            dirs.setdefault(child, {})
            cur = child
        leaf = p.parts[-1]
        full = f"{cur}/{leaf}" if cur else leaf
        dirs.setdefault(cur, {})[leaf] = ("file", full)
        norm_files[full] = bytes(payload)

    dir_inodes: dict[str, int] = {"": 2, "lost+found": 11}
    dirs.setdefault("", {})["lost+found"] = ("dir", "lost+found")
    dirs.setdefault("lost+found", {})
    for dpath in sorted(k for k in dirs if k not in ("", "lost+found")):
        dir_inodes[dpath] = alloc_inode()

    file_inodes: dict[str, int] = {}
    for fpath in sorted(norm_files):
        file_inodes[fpath] = alloc_inode()

    def write_inode(
        ino: int,
        *,
        mode: int,
        size: int,
        links: int,
        blocks_1k: Sequence[int],
        indirect_block: int = 0,
    ) -> None:
        i_block = [0] * 15
        for idx, b in enumerate(blocks_1k[:12]):
            i_block[idx] = b
        if indirect_block:
            i_block[12] = indirect_block
        total_1k = len(blocks_1k) + (1 if indirect_block else 0)
        i_blocks_512 = total_1k * 2  # ext2 counts 512-byte sectors in i_blocks
        off = 5 * block_size + (ino - 1) * inode_size
        struct.pack_into(
            "<HHIIIIIHHII",
            img,
            off,
            mode,
            0,              # uid
            size,
            0,              # atime
            0,              # ctime
            0,              # mtime
            0,              # dtime
            0,              # gid
            links,
            i_blocks_512,
            0,              # i_flags
        )
        for idx, ptr in enumerate(i_block):
            struct.pack_into("<I", img, off + 40 + idx * 4, ptr)

    def pack_dirents(entries: Sequence[tuple[int, int, str]]) -> bytes:
        # entries: (inode, file_type, name); fits in single 1 KiB directory block
        out = bytearray(block_size)
        pos = 0
        for idx, (ino, ftype, name) in enumerate(entries):
            nb = name.encode("ascii")
            need = (8 + len(nb) + 3) & ~3
            if pos + need > block_size:
                raise ValueError("Directory exceeds 1 KiB block in minimal ext2 builder")
            rec_len = (block_size - pos) if idx == len(entries) - 1 else need
            struct.pack_into("<IHBB", out, pos, ino, rec_len, len(nb), ftype)
            out[pos + 8 : pos + 8 + len(nb)] = nb
            pos += rec_len
        return bytes(out)

    # Write regular files
    for fpath, data in sorted(norm_files.items()):
        n_data = (len(data) + block_size - 1) // block_size
        if n_data > 12 + (block_size // 4):
            raise ValueError(f"File {fpath!r} exceeds single-indirect capacity")
        dblocks = [alloc_block() for _ in range(n_data)]
        ind_blk = alloc_block() if n_data > 12 else 0
        for idx, b in enumerate(dblocks):
            chunk = data[idx * block_size : (idx + 1) * block_size]
            img[b * block_size : b * block_size + len(chunk)] = chunk
        if ind_blk:
            for idx, b in enumerate(dblocks[12:]):
                struct.pack_into("<I", img, ind_blk * block_size + idx * 4, b)
        is_exec = (
            fpath.startswith(("bin/", "sbin/", "usr/bin/", "usr/sbin/"))
            or fpath.endswith((".elf", ".sh"))
        )
        write_inode(
            file_inodes[fpath],
            mode=0x81ED if is_exec else 0x81A4,  # S_IFREG | 0755 for executables, else 0644
            size=len(data),
            links=1,
            blocks_1k=dblocks,
            indirect_block=ind_blk,
        )

    # Write directories
    used_dirs_count = len(dir_inodes)
    for dpath, children in sorted(dirs.items()):
        ino = dir_inodes[dpath]
        if dpath == "":
            parent_ino = 2
        else:
            parent_key = str(PurePosixPath(dpath).parent)
            if parent_key == ".":
                parent_key = ""
            parent_ino = dir_inodes[parent_key]

        sub_dirs = sum(1 for kind, _ in children.values() if kind == "dir")
        dirents: list[tuple[int, int, str]] = [
            (ino, 2, "."),
            (parent_ino, 2, ".."),
        ]
        for cname, (ckind, cfull) in sorted(children.items()):
            if ckind == "dir":
                dirents.append((dir_inodes[cfull], 2, cname))
            else:
                dirents.append((file_inodes[cfull], 1, cname))
        dblk = alloc_block()
        img[dblk * block_size : (dblk + 1) * block_size] = pack_dirents(dirents)
        write_inode(
            ino,
            mode=0x41ED,  # S_IFDIR | 0755
            size=block_size,
            links=2 + sub_dirs,
            blocks_1k=[dblk],
        )

    # Mark block bitmap (block 3): block 1 is bit 0 because s_first_data_block == 1!
    bmap_off = 3 * block_size
    for b in range(1, next_block):
        bit = b - 1
        img[bmap_off + (bit >> 3)] |= 1 << (bit & 7)
    # Mark trailing padding bits past total_blocks as allocated
    for bit in range(total_blocks - 1, block_size * 8):
        img[bmap_off + (bit >> 3)] |= 1 << (bit & 7)

    # Mark inode bitmap (block 4): inodes 1..10 reserved + 11..next_inode-1 used
    imap_off = 4 * block_size
    for ino in range(1, next_inode):
        bit = ino - 1
        img[imap_off + (bit >> 3)] |= 1 << (bit & 7)
    for bit in range(inodes_count, block_size * 8):
        img[imap_off + (bit >> 3)] |= 1 << (bit & 7)

    free_blocks = total_blocks - next_block
    free_inodes = inodes_count - (next_inode - 1)

    # Group descriptor at block 2
    struct.pack_into(
        "<IIIHHH",
        img,
        2 * block_size,
        3,  # bg_block_bitmap
        4,  # bg_inode_bitmap
        5,  # bg_inode_table
        free_blocks,
        free_inodes,
        used_dirs_count,
    )

    # Superblock at block 1 (byte 1024)
    sb = 1 * block_size
    struct.pack_into(
        "<IIIIIIII",
        img,
        sb + 0,
        inodes_count,
        total_blocks,
        0,                  # s_r_blocks_count
        free_blocks,
        free_inodes,
        1,                  # s_first_data_block
        0,                  # s_log_block_size (1024 << 0)
        0,                  # s_log_frag_size
    )
    struct.pack_into(
        "<III",
        img,
        sb + 32,
        8192,               # s_blocks_per_group
        8192,               # s_frags_per_group
        inodes_count,       # s_inodes_per_group
    )
    struct.pack_into(
        "<HHHHHHIIIIHH",
        img,
        sb + 52,
        0,                  # s_mnt_count
        20,                 # s_max_mnt_count
        0xEF53,             # s_magic
        1,                  # s_state = EXT2_VALID_FS
        1,                  # s_errors = EXT2_ERRORS_CONTINUE
        0,                  # s_minor_rev_level
        0,                  # s_lastcheck
        0,                  # s_checkinterval
        0,                  # s_creator_os = Linux
        1,                  # s_rev_level = EXT2_DYNAMIC_REV
        0,                  # s_def_resuid
        0,                  # s_def_resgid
    )
    struct.pack_into(
        "<IHHI",
        img,
        sb + 84,
        11,                 # s_first_ino
        inode_size,         # s_inode_size = 128
        0,                  # s_block_group_nr
        0,                  # s_feature_compat
    )
    struct.pack_into("<I", img, sb + 96, 0x0002)  # EXT2_FEATURE_INCOMPAT_FILETYPE
    struct.pack_into("<I", img, sb + 100, 0x0001)  # EXT2_FEATURE_RO_COMPAT_SPARSE_SUPER
    img[sb + 104 : sb + 120] = hashlib.md5(volume_name.encode("utf-8")).digest()
    vol_bytes = volume_name.encode("ascii")[:16]
    img[sb + 120 : sb + 120 + len(vol_bytes)] = vol_bytes
    return bytes(img)


def _fat_sfn_checksum(sfn11: bytes) -> int:
    s = 0
    for b in sfn11:
        s = (((s & 1) << 7) + (s >> 1) + b) & 0xFF
    return s


def _make_fat_dirents(name: str, attr: int, first_clu: int, size: int, idx: int) -> bytes:
    stem, dot, ext = name.partition(".")
    clean_stem = "".join(c for c in stem.upper() if c.isalnum() or c == "_")
    clean_ext = "".join(c for c in ext.upper() if c.isalnum() or c == "_")
    exact_83 = (
        name.upper() == name
        and len(stem) <= 8
        and len(ext) <= 3
        and clean_stem == stem
        and clean_ext == ext
        and (dot == "." or ext == "")
    )
    if exact_83:
        sfn11 = (stem.ljust(8)[:8] + ext.ljust(3)[:3]).encode("ascii")
        lfn_records = b""
    else:
        prefix = (clean_stem or "FILE")[:6]
        sfn11 = (f"{prefix}~{idx}".ljust(8)[:8] + clean_ext.ljust(3)[:3]).encode("ascii")
        chk = _fat_sfn_checksum(sfn11)
        utf16 = [ord(c) for c in name] + [0x0000]
        while len(utf16) % 13 != 0:
            utf16.append(0xFFFF)
        n_ord = len(utf16) // 13
        recs = []
        for ord_idx in range(n_ord, 0, -1):
            chunk = utf16[(ord_idx - 1) * 13 : ord_idx * 13]
            rec = bytearray(32)
            rec[0] = ord_idx | (0x40 if ord_idx == n_ord else 0)
            rec[11] = 0x0F
            rec[13] = chk
            pos = [1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30]
            for k, code in enumerate(chunk):
                struct.pack_into("<H", rec, pos[k], code)
            recs.append(bytes(rec))
        lfn_records = b"".join(recs)

    sfn_rec = bytearray(32)
    sfn_rec[0:11] = sfn11
    sfn_rec[11] = attr
    struct.pack_into("<H", sfn_rec, 20, (first_clu >> 16) & 0xFFFF)
    struct.pack_into("<H", sfn_rec, 26, first_clu & 0xFFFF)
    struct.pack_into("<I", sfn_rec, 28, 0 if (attr & 0x10) else size)
    return lfn_records + bytes(sfn_rec)


def _wrap_mbr_if_needed(part_bytes: bytes, *, total_card_bytes: int, partition_lba: int, part_type: int) -> bytes:
    if partition_lba == 0:
        return part_bytes
    part_secs = len(part_bytes) // 512
    if partition_lba * 512 + len(part_bytes) > total_card_bytes:
        raise ValueError("Partition exceeds total_card_bytes")
    card = bytearray(total_card_bytes)
    # Primary MBR entry 1 at 0x1BE
    struct.pack_into(
        "<BBBBBBBBII",
        card,
        0x1BE,
        0x00,
        0x20, 0x21, 0x00,
        part_type,
        0xFE, 0xFF, 0xFF,
        partition_lba,
        part_secs,
    )
    struct.pack_into("<H", card, 510, 0xAA55)
    card[partition_lba * 512 : partition_lba * 512 + len(part_bytes)] = part_bytes
    return bytes(card)


def build_fat32_sd_image(
    files: Sequence[tuple[str, bytes]],
    *,
    card_bytes: int = 16 * 1024 * 1024,
    partition_lba: int = 2048,
    spc: int = 8,
    fragmented_first_file: bool = False,
) -> bytes:
    """Build a deterministic FAT32 image (raw VBR if partition_lba==0, else MBR card)."""
    sector = 512
    part_bytes = card_bytes - partition_lba * sector
    tot_sec = part_bytes // sector
    rsvd = 32
    nfats = 2
    # Compute FAT size
    est_clusters = tot_sec // spc + 4
    fatsz32 = ((est_clusters * 4 + sector - 1) // sector + 7) & ~7
    data_lba = rsvd + nfats * fatsz32
    clu_cnt = (tot_sec - data_lba) // spc
    part = bytearray(part_bytes)

    # VBR
    part[0:3] = b"\xEB\x58\x90"
    part[3:11] = b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHII", part, 11, sector, spc, rsvd, nfats, 0, 0, 0xF8, 0, 63, 255, partition_lba, tot_sec)
    struct.pack_into("<IHHIHH", part, 36, fatsz32, 0, 0, 2, 1, 6)
    part[66] = 0x29
    struct.pack_into("<I", part, 67, 0xA6400CF0)
    part[71:82] = b"SONY_EMUND "
    part[82:90] = b"FAT32   "
    struct.pack_into("<H", part, 510, 0xAA55)

    # FSInfo at sector 1
    struct.pack_into("<I", part, sector + 0, 0x41615252)
    struct.pack_into("<I", part, sector + 484, 0x61417272)
    struct.pack_into("<II", part, sector + 488, 0xFFFFFFFF, 0xFFFFFFFF)
    struct.pack_into("<H", part, sector + 510, 0xAA55)
    # Backup boot sector at sector 6
    part[6 * sector : 7 * sector] = part[0:sector]

    def set_fat(clu: int, val: int) -> None:
        for fat_idx in range(nfats):
            struct.pack_into("<I", part, (rsvd + fat_idx * fatsz32) * sector + clu * 4, val & 0x0FFFFFFF)

    def clu_off(clu: int) -> int:
        return (data_lba + (clu - 2) * spc) * sector

    set_fat(0, 0x0FFFFFF8)
    set_fat(1, 0x0FFFFFFF)
    set_fat(2, 0x0FFFFFFF)  # Root dir cluster 2
    set_fat(3, 0x0FFFFFFF)  # DCIM dir cluster 3
    set_fat(4, 0x0FFFFFFF)  # 100MSDCF dir cluster 4

    clu_bytes = spc * sector
    root_dir = bytearray()
    root_dir += _make_fat_dirents("DCIM", 0x10, 3, 0, 1)
    # Populate minimal DCIM/100MSDCF
    dcim_dir = bytearray(clu_bytes)
    dcim_entries = (
        struct.pack("<11sB14xHI", b".          ", 0x10, 3, 0)
        + struct.pack("<11sB14xHI", b"..         ", 0x10, 0, 0)
        + _make_fat_dirents("100MSDCF", 0x10, 4, 0, 1)
    )
    dcim_dir[: len(dcim_entries)] = dcim_entries
    part[clu_off(3) : clu_off(3) + clu_bytes] = dcim_dir

    msdcf_dir = bytearray(clu_bytes)
    msdcf_entries = (
        struct.pack("<11sB14xHI", b".          ", 0x10, 4, 0)
        + struct.pack("<11sB14xHI", b"..         ", 0x10, 3, 0)
    )
    msdcf_dir[: len(msdcf_entries)] = msdcf_entries
    part[clu_off(4) : clu_off(4) + clu_bytes] = msdcf_dir

    next_clu = 5
    for idx, (fname, data) in enumerate(files, start=1):
        needed = max(1, (len(data) + clu_bytes - 1) // clu_bytes)
        if idx == 1 and fragmented_first_file and needed >= 2:
            # Interleave clusters with 1-cluster gaps (e.g. 5, 7, 9, ...)
            chain = [next_clu + k * 2 for k in range(needed)]
            next_clu = chain[-1] + 2
        else:
            chain = [next_clu + k for k in range(needed)]
            next_clu += needed
        if chain[-1] > clu_cnt + 1:
            raise ValueError("FAT32 image out of space for files")
        for c_i, c in enumerate(chain):
            chunk = data[c_i * clu_bytes : (c_i + 1) * clu_bytes]
            part[clu_off(c) : clu_off(c) + len(chunk)] = chunk
            set_fat(c, chain[c_i + 1] if c_i + 1 < len(chain) else 0x0FFFFFFF)
        root_dir += _make_fat_dirents(fname, 0x20, chain[0], len(data), idx)

    if len(root_dir) > clu_bytes:
        raise ValueError("Root directory exceeds 1 cluster")
    part[clu_off(2) : clu_off(2) + len(root_dir)] = root_dir
    return _wrap_mbr_if_needed(bytes(part), total_card_bytes=card_bytes, partition_lba=partition_lba, part_type=0x0C)


def build_exfat_sd_image(
    files: Sequence[tuple[str, bytes]],
    *,
    card_bytes: int = 16 * 1024 * 1024,
    partition_lba: int = 2048,
    spc_shift: int = 3,
    fragmented_first_file: bool = False,
) -> bytes:
    """Build a deterministic exFAT image (NoFatChain contiguous or FAT-chained fragmented)."""
    sector = 512
    spc = 1 << spc_shift
    clu_bytes = spc * sector
    part_bytes = card_bytes - partition_lba * sector
    vol_secs = part_bytes // sector
    fat_off = 32
    est_clu = vol_secs // spc
    fat_len = ((est_clu * 4 + sector - 1) // sector + 7) & ~7
    heap_off = fat_off + fat_len
    total_clu = (vol_secs - heap_off) // spc
    part = bytearray(part_bytes)

    part[0:3] = b"\xEB\x76\x90"
    part[3:11] = b"EXFAT   "
    struct.pack_into("<Q", part, 64, partition_lba)
    struct.pack_into("<Q", part, 72, vol_secs)
    struct.pack_into("<I", part, 80, fat_off)
    struct.pack_into("<I", part, 84, fat_len)
    struct.pack_into("<I", part, 88, heap_off)
    struct.pack_into("<I", part, 92, total_clu)
    struct.pack_into("<I", part, 96, 2)  # root_cluster = 2
    part[108] = 9
    part[109] = spc_shift
    part[110] = 1
    struct.pack_into("<H", part, 510, 0xAA55)

    def set_fat(clu: int, val: int) -> None:
        struct.pack_into("<I", part, fat_off * sector + clu * 4, val)

    def clu_off(clu: int) -> int:
        return (heap_off + (clu - 2) * spc) * sector

    set_fat(0, 0xFFFFFFF8)
    set_fat(1, 0xFFFFFFFF)
    set_fat(2, 0xFFFFFFFF)

    root_dir = bytearray()
    next_clu = 3
    for idx, (fname, data) in enumerate(files, start=1):
        needed = max(1, (len(data) + clu_bytes - 1) // clu_bytes)
        if idx == 1 and fragmented_first_file and needed >= 2:
            chain = [next_clu + k * 2 for k in range(needed)]
            next_clu = chain[-1] + 2
            for c_i, c in enumerate(chain):
                chunk = data[c_i * clu_bytes : (c_i + 1) * clu_bytes]
                part[clu_off(c) : clu_off(c) + len(chunk)] = chunk
                set_fat(c, chain[c_i + 1] if c_i + 1 < len(chain) else 0xFFFFFFFF)
            first_clu = chain[0]
            no_fat_chain = False
        else:
            first_clu = next_clu
            next_clu += needed
            part[clu_off(first_clu) : clu_off(first_clu) + len(data)] = data
            no_fat_chain = True

        name_recs = (len(fname) + 14) // 15
        sec_cnt = 1 + name_recs
        e85 = bytearray(32)
        e85[0] = 0x85
        e85[1] = sec_cnt
        struct.pack_into("<H", e85, 4, 0x20)
        ec0 = bytearray(32)
        ec0[0] = 0xC0
        ec0[1] = 0x03 if no_fat_chain else 0x01
        ec0[3] = len(fname)
        struct.pack_into("<Q", ec0, 8, len(data))
        struct.pack_into("<I", ec0, 20, first_clu)
        struct.pack_into("<Q", ec0, 24, len(data))
        root_dir += bytes(e85) + bytes(ec0)
        for r in range(name_recs):
            ec1 = bytearray(32)
            ec1[0] = 0xC1
            seg = fname[r * 15 : (r + 1) * 15]
            for k, ch in enumerate(seg):
                struct.pack_into("<H", ec1, 2 + k * 2, ord(ch))
            root_dir += bytes(ec1)

    part[clu_off(2) : clu_off(2) + len(root_dir)] = root_dir
    return _wrap_mbr_if_needed(bytes(part), total_card_bytes=card_bytes, partition_lba=partition_lba, part_type=0x07)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and package the reversible EmuNAND emulator prototype.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Compile ARM .ko + ELF bundle and run static audit")
    p_build.add_argument("--out", type=Path, required=True, help="Output directory under artifacts/")
    p_build.add_argument("--host-ctl", action="store_true", help="Also build WSL Linux host binary for tests")

    p_audit = sub.add_parser("audit", help="Run fail-closed static audit on an existing emunand_guard.ko")
    p_audit.add_argument("--ko", type=Path, required=True)

    p_vdisk = sub.add_parser("pack-sd", help="Build CFW_VDISK.IMG (ext2) and a FAT32/exFAT virtual SD card image")
    p_vdisk.add_argument("--overlay-dir", type=Path, required=True, help="Directory of files to pack into CFW_VDISK.IMG")
    p_vdisk.add_argument("--out-sd", type=Path, required=True, help="Output virtual SD card image path")
    p_vdisk.add_argument("--out-vdisk", type=Path, help="Optional path to also save standalone CFW_VDISK.IMG")
    p_vdisk.add_argument("--fs", choices=("fat32", "exfat"), default="fat32")
    p_vdisk.add_argument("--vdisk-kib", type=int, default=256)
    p_vdisk.add_argument("--card-mib", type=int, default=16)

    default_inputs = os.environ.get(
        "A6400_QEMU_DATA_DIR",
        "$HOME/a6400-inputs/inputs-pr-preflight-20261116-1555",
    )
    default_qemu = os.environ.get(
        "A6400_QEMU_DIR",
        "$HOME/sony-a6400-qemu-11.1.3",
    )

    p_qemu = sub.add_parser("qemu-verify", help="Run full 4-core QEMU sony-a6400 E2E reversible verification")
    p_qemu.add_argument("--report-dir", type=Path, required=True, help="New report directory under artifacts/")
    p_qemu.add_argument("--inputs-wsl", default=default_inputs)
    p_qemu.add_argument("--qemu-wsl", default=default_qemu)
    p_qemu.add_argument("--timeout", type=int, default=160)

    p_qpkg = sub.add_parser(
        "qemu-verify-packages",
        help="Verify mounting real flashable kernel (nflasha3/nflasha1) and Custom-OS (ext2/nflasha7) packages from SD in QEMU",
    )
    p_qpkg.add_argument("--report-dir", type=Path, required=True, help="New report directory under artifacts/")
    p_qpkg.add_argument("--inputs-wsl", default=default_inputs)
    p_qpkg.add_argument("--qemu-wsl", default=default_qemu)
    p_qpkg.add_argument("--timeout", type=int, default=180)

    args = parser.parse_args(argv)
    if args.cmd == "build":
        manifest = build_native_bundle(args.out, build_host_ctl=args.host_ctl)
        print(json.dumps(manifest, indent=2))
        return 0
    if args.cmd == "audit":
        print(json.dumps(audit_ko_module(args.ko), indent=2))
        return 0
    if args.cmd == "qemu-verify":
        report_dir = args.report_dir.resolve()
        if report_dir.exists():
            raise ValueError(f"Refusing to overwrite existing report directory: {report_dir}")
        bundle_dir = report_dir / "bundle"
        manifest = build_native_bundle(bundle_dir)
        vdisk = build_ext2_vdisk(
            {
                "emunand.manifest": b"kemco_cfw.txt /setting/kemco.txt\n",
                "kemco_cfw.txt": b"EMUNAND_VDISK_KEMCO_OVERRIDE_V1\n",
                "usr_payload.txt": b"EMUNAND_EXT2_PAYLOAD_OK\n",
            },
            size_bytes=256 * 1024,
        )
        vdisk_sha = sha256_bytes(vdisk)
        (report_dir / "CFW_VDISK.IMG").write_bytes(vdisk)
        sd_exfat = build_exfat_sd_image([("CFW_VDISK.IMG", vdisk)], card_bytes=16 * 1024 * 1024)
        sd_path = report_dir / "sd_exfat_16m.img"
        sd_path.write_bytes(sd_exfat)
        (report_dir / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        e2e_wsl = to_wsl_path(PROJECT_ROOT / "tools" / "research" / "emunand_qemu_e2e.py")
        cmd_list = [
            "python3", e2e_wsl,
            "--repo-root", to_wsl_path(PROJECT_ROOT),
            "--bundle-dir", to_wsl_path(bundle_dir),
            "--sd-image", to_wsl_path(sd_path),
            "--vdisk-sha256", vdisk_sha,
            "--inputs-dir", args.inputs_wsl,
            "--qemu-dir", args.qemu_wsl,
            "--report-dir", to_wsl_path(report_dir),
            "--timeout", str(args.timeout),
        ]
        if os.name == "nt":
            proc = subprocess.run(["wsl", "-e", *cmd_list], check=False)
        else:
            proc = subprocess.run(cmd_list, check=False)
        return proc.returncode
    if args.cmd == "qemu-verify-packages":
        report_dir = args.report_dir.resolve()
        if report_dir.exists():
            raise ValueError(f"Refusing to overwrite existing report directory: {report_dir}")
        bundle_dir = report_dir / "bundle"
        manifest = build_native_bundle(bundle_dir)

        fw_a6400a = PROJECT_ROOT / "firmware" / "extracted_a6400a_v101" / "firmware.tar_unpacked"
        nflasha3_pkg = (fw_a6400a / "0700_part_image" / "dev" / "nflasha3").read_bytes()
        nflasha7_pkg = (fw_a6400a / "0700_part_image" / "dev" / "nflasha7").read_bytes()
        nflasha1_pkg = (fw_a6400a / "0400_updater" / "dev" / "nflasha1").read_bytes()
        with (fw_a6400a / "0700_part_image" / "dev" / "nflasha5").open("rb") as wbi_fh:
            wbi_slice = wbi_fh.read(4096)

        probe_elf = (bundle_dir / "custom_os_probe.elf").read_bytes()
        custom_os_vdisk = build_ext2_vdisk(
            {
                "emunand.manifest": b"@boot_pkg/. /system\n",
                "etc/os-release": b"NAME=BionzX-CustomOS-SD\nVERSION=1.0-QEMU\n",
                "sbin/custom_os_probe.elf": probe_elf,
            },
            size_bytes=256 * 1024,
        )
        vdisk_sha = sha256_bytes(custom_os_vdisk)
        (report_dir / "CFW_VDISK.IMG").write_bytes(custom_os_vdisk)

        # Pack all 5 images (~43 MiB total) onto a 64 MiB virtual exFAT SD card
        sd_exfat = build_exfat_sd_image(
            [
                ("CFW_VDISK.IMG", custom_os_vdisk),
                ("NFLASH3.IMG", nflasha3_pkg),
                ("NFLASH7.IMG", nflasha7_pkg),
                ("NFLASH1.IMG", nflasha1_pkg),
                ("WBI_RAW.IMG", wbi_slice),
            ],
            card_bytes=64 * 1024 * 1024,
        )
        sd_path = report_dir / "sd_flashpkg_64m.img"
        sd_path.write_bytes(sd_exfat)
        manifest["packaged_sd_inputs"] = {
            "CFW_VDISK.IMG": {"bytes": len(custom_os_vdisk), "sha256": vdisk_sha},
            "NFLASH3.IMG": {"bytes": len(nflasha3_pkg), "sha256": sha256_bytes(nflasha3_pkg)},
            "NFLASH7.IMG": {"bytes": len(nflasha7_pkg), "sha256": sha256_bytes(nflasha7_pkg)},
            "NFLASH1.IMG": {"bytes": len(nflasha1_pkg), "sha256": sha256_bytes(nflasha1_pkg)},
            "WBI_RAW.IMG": {"bytes": len(wbi_slice), "sha256": sha256_bytes(wbi_slice)},
            "sd_flashpkg_64m.img": {"bytes": len(sd_exfat), "sha256": sha256_bytes(sd_exfat)},
        }
        (report_dir / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        e2e_wsl = to_wsl_path(PROJECT_ROOT / "tools" / "research" / "emunand_qemu_e2e.py")
        cmd_list = [
            "python3", e2e_wsl,
            "--mode", "packages",
            "--repo-root", to_wsl_path(PROJECT_ROOT),
            "--bundle-dir", to_wsl_path(bundle_dir),
            "--sd-image", to_wsl_path(sd_path),
            "--vdisk-sha256", vdisk_sha,
            "--inputs-dir", args.inputs_wsl,
            "--qemu-dir", args.qemu_wsl,
            "--report-dir", to_wsl_path(report_dir),
            "--timeout", str(args.timeout),
        ]
        if os.name == "nt":
            proc = subprocess.run(["wsl", "-e", *cmd_list], check=False)
        else:
            proc = subprocess.run(cmd_list, check=False)
        return proc.returncode
    if args.cmd == "pack-sd":
        files_map: dict[str, bytes] = {}
        for p in sorted(args.overlay_dir.rglob("*")):
            if p.is_file():
                rel = p.relative_to(args.overlay_dir).as_posix()
                files_map[rel] = p.read_bytes()
        vdisk = build_ext2_vdisk(files_map, size_bytes=args.vdisk_kib * 1024)
        if args.out_vdisk:
            args.out_vdisk.parent.mkdir(parents=True, exist_ok=True)
            args.out_vdisk.write_bytes(vdisk)
        sd_files = [("CFW_VDISK.IMG", vdisk)]
        card_bytes = args.card_mib * 1024 * 1024
        if args.fs == "fat32":
            sd_img = build_fat32_sd_image(sd_files, card_bytes=card_bytes)
        else:
            sd_img = build_exfat_sd_image(sd_files, card_bytes=card_bytes)
        args.out_sd.parent.mkdir(parents=True, exist_ok=True)
        args.out_sd.write_bytes(sd_img)
        print(json.dumps({
            "vdisk_bytes": len(vdisk),
            "vdisk_sha256": sha256_bytes(vdisk),
            "sd_bytes": len(sd_img),
            "sd_sha256": sha256_bytes(sd_img),
            "sd_fs": args.fs,
        }, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
