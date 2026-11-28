"""Offline Unicorn ARMv7 and FAT32/exFAT/ext2 verification for the reversible EmuNAND prototype.

Strictly offline: no camera, USB, /dev/mem, or physical SD card access.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from elftools.elf.elffile import ELFFile
from unicorn import (
    UC_ARCH_ARM,
    UC_HOOK_CODE,
    UC_MODE_ARM,
    UC_PROT_ALL,
    Uc,
)
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_R5,
    UC_ARM_REG_R6,
    UC_ARM_REG_R7,
    UC_ARM_REG_R8,
    UC_ARM_REG_R10,
    UC_ARM_REG_R11,
    UC_ARM_REG_SP,
)

from project_paths import PROJECT_ROOT
from src.a6400.images.ext2_metadata import Ext2Metadata
from tools.research.emunand_prototype import (
    ALLOWED_KO_IMPORTS,
    audit_ko_module,
    build_exfat_sd_image,
    build_ext2_vdisk,
    build_fat32_sd_image,
    build_native_bundle,
    sha256_bytes,
    to_wsl_path,
)


TEST_ARTIFACTS = PROJECT_ROOT / "artifacts" / "tests"


def _wsl_toolchain_available() -> bool:
    if os.name != "nt":
        return bool(shutil.which("arm-linux-gnueabi-gcc") and shutil.which("gcc"))
    if shutil.which("wsl") is None:
        return False
    proc = subprocess.run(
        [
            "wsl", "-e", "bash", "-lc",
            'export PATH="$HOME/a6400-rea-tools/arm-gcc-15.2.0-prepush/bin:$PATH"; '
            "command -v arm-linux-gnueabi-gcc >/dev/null && command -v gcc >/dev/null",
        ],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _run_host_ctl(host_ctl: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = (
        ["wsl", "-e", to_wsl_path(host_ctl), *args]
        if os.name == "nt"
        else [str(host_ctl), *args]
    )
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


WSL_GCC_AVAILABLE = _wsl_toolchain_available()


def _sign_extend(val: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (val & (sign - 1)) - (val & sign)


class UnicornEmunandKoHarness:
    """Relocates and executes the compiled ARMv7 emunand_guard.ko inside Unicorn."""

    MOD_BASE = 0x5F100000
    KERN_STUB_BASE = 0x60100000
    EMMC_MOD_BASE = 0x5F020000
    VMALLOC_BASE = 0x65000000
    LOWMEM_VA_BASE = 0x62000000
    LOWMEM_SIZE = 0x00100000
    STACK_BASE = 0x7DC00000
    STACK_SIZE = 0x00020000
    RETURN_SENTINEL = 0x6010FF00

    def __init__(self, ko_path: Path) -> None:
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.uc.mem_map(self.MOD_BASE, 0x100000, UC_PROT_ALL)
        self.uc.mem_map(self.EMMC_MOD_BASE, 0x10000, UC_PROT_ALL)
        self.uc.mem_map(self.KERN_STUB_BASE, 0x20000, UC_PROT_ALL)
        self.uc.mem_map(self.VMALLOC_BASE, 0x400000, UC_PROT_ALL)
        self.uc.mem_map(self.LOWMEM_VA_BASE, self.LOWMEM_SIZE, UC_PROT_ALL)
        self.uc.mem_map(self.STACK_BASE, self.STACK_SIZE, UC_PROT_ALL)

        # Return sentinel instruction (nop)
        self.uc.mem_write(self.RETURN_SENTINEL, struct.pack("<I", 0xE1A00000))

        # Simulated kernel/module globals
        self.backup_save_va = self.EMMC_MOD_BASE + 0x2000
        self.uc.mem_write(self.backup_save_va, struct.pack("<i", 1))
        self.emmc_seq_read_va = self.EMMC_MOD_BASE + 0x1000
        self.emmc_seq_write_va = self.EMMC_MOD_BASE + 0x1100
        self._install_stock_emmc_prologues()

        # Physical eMMC backing store: (part_id, sector) -> 512 bytes
        self.phys_emmc: dict[tuple[int, int], bytes] = {}
        self.hw_read_calls = 0
        self.hw_write_calls = 0
        self.wbi_drop_calls = 0
        self.vmalloc_next = self.VMALLOC_BASE
        self.vmalloc_active: dict[int, int] = {}
        self.vfree_calls = 0
        self.sg_Cursor = self.LOWMEM_VA_BASE + 0xE0000
        self.proc_fops_va: int | None = None
        self.proc_removed = False
        self.printk_log: list[str] = []

        self.stub_addrs: dict[str, int] = {}
        self.addr_to_stub: dict[int, str] = {}
        stub_names = sorted(ALLOWED_KO_IMPORTS | {"wbi_drop", "v7_coherent_kern_range"})
        for idx, name in enumerate(stub_names):
            va = self.KERN_STUB_BASE + idx * 8
            # bx lr
            self.uc.mem_write(va, struct.pack("<I", 0xE12FFF1E))
            self.stub_addrs[name] = va
            self.addr_to_stub[va] = name

        self.symbols: dict[str, int] = {}
        self._load_and_relocate_ko(ko_path)

        # Install hooks for kernel imports and simulated hardware seq_read/seq_write bodies
        self.uc.hook_add(
            UC_HOOK_CODE,
            self._on_stub,
            begin=self.KERN_STUB_BASE,
            end=self.KERN_STUB_BASE + len(stub_names) * 8,
        )
        self.uc.hook_add(
            UC_HOOK_CODE,
            self._on_emmc_hw_body,
            begin=self.EMMC_MOD_BASE + 0x1000,
            end=self.EMMC_MOD_BASE + 0x1200,
        )

    def _install_stock_emmc_prologues(self) -> None:
        # Exact 16-byte Sony prologue + body nop (+0x10) + Sony epilogue:
        #   +0x00: e1a0c00d  mov ip, sp
        #   +0x04: e92dddf0  push {r4, r5, r6, r7, r8, sl, fp, ip, lr, pc}
        #   +0x08: e24cb004  sub fp, ip, #4
        #   +0x0c: e24dd010  sub sp, sp, #0x10
        #   +0x10: e1a06000  mov r6, r0   (saves args matching real emmc_seq_*)
        #   +0x14: e1a05001  mov r5, r1
        #   +0x18: e1a07002  mov r7, r2
        #   +0x1c: e1a08003  mov r8, r3
        #   +0x20: e1a00000  nop          (hooked hardware body!)
        #   +0x24: e24bd024  sub sp, fp, #0x24
        #   +0x28: e89dadf0  ldm sp, {r4, r5, r6, r7, r8, sl, fp, sp, pc}
        words = [
            0xE1A0C00D,
            0xE92DDDF0,
            0xE24CB004,
            0xE24DD010,
            0xE1A06000,
            0xE1A05001,
            0xE1A07002,
            0xE1A08003,
            0xE1A00000,
            0xE24BD024,
            0xE89DADF0,
        ]
        blob = struct.pack(f"<{len(words)}I", *words)
        self.uc.mem_write(self.emmc_seq_read_va, blob)
        self.uc.mem_write(self.emmc_seq_write_va, blob)

    def _load_and_relocate_ko(self, ko_path: Path) -> None:
        with ko_path.open("rb") as fh:
            elf = ELFFile(fh)
            sec_addrs: dict[int, int] = {}
            cursor = self.MOD_BASE
            for idx, sec in enumerate(elf.iter_sections()):
                if not (sec["sh_flags"] & 0x2):  # SHF_ALLOC
                    continue
                align = max(int(sec["sh_addralign"]), 4)
                cursor = (cursor + align - 1) & ~(align - 1)
                sec_addrs[idx] = cursor
                if sec["sh_type"] != "SHT_NOBITS" and sec["sh_size"] > 0:
                    self.uc.mem_write(cursor, sec.data())
                cursor += int(sec["sh_size"])

            symtab = elf.get_section_by_name(".symtab")
            assert symtab is not None
            syms = list(symtab.iter_symbols())

            def resolve_sym(sym_idx: int) -> int:
                s = syms[sym_idx]
                shndx = s["st_shndx"]
                if shndx == "SHN_UNDEF":
                    if s.name not in self.stub_addrs:
                        raise AssertionError(f"Unstubbed undefined symbol {s.name!r}")
                    return self.stub_addrs[s.name]
                if not isinstance(shndx, int) or shndx not in sec_addrs:
                    raise AssertionError(f"Unmapped section {shndx} for symbol {s.name!r}")
                return sec_addrs[shndx] + int(s["st_value"])

            for s in syms:
                if s.name and isinstance(s["st_shndx"], int) and s["st_shndx"] in sec_addrs:
                    self.symbols[s.name] = sec_addrs[s["st_shndx"]] + int(s["st_value"])

            for sec in elf.iter_sections():
                if sec["sh_type"] != "SHT_REL":
                    continue
                target_idx = sec["sh_info"]
                if target_idx not in sec_addrs:
                    continue
                base_va = sec_addrs[target_idx]
                for rel in sec.iter_relocations():
                    loc = base_va + int(rel["r_offset"])
                    rtype = int(rel["r_info_type"])
                    S = resolve_sym(int(rel["r_info_sym"]))
                    insn = struct.unpack("<I", self.uc.mem_read(loc, 4))[0]
                    if rtype == 2:  # R_ARM_ABS32
                        val = (S + insn) & 0xFFFFFFFF
                        self.uc.mem_write(loc, struct.pack("<I", val))
                    elif rtype == 3:  # R_ARM_REL32
                        val = (S + insn - loc) & 0xFFFFFFFF
                        self.uc.mem_write(loc, struct.pack("<I", val))
                    elif rtype in (1, 28, 29):  # R_ARM_PC24 / CALL / JUMP24
                        addend = _sign_extend(insn & 0x00FFFFFF, 24) << 2
                        disp = (S + addend - loc) >> 2
                        patched = (insn & 0xFF000000) | (disp & 0x00FFFFFF)
                        self.uc.mem_write(loc, struct.pack("<I", patched))
                    elif rtype in (43, 44):  # R_ARM_MOVW_ABS_NC / R_ARM_MOVT_ABS
                        imm16 = ((insn >> 4) & 0xF000) | (insn & 0x0FFF)
                        addend = _sign_extend(imm16, 16)
                        full = (S + addend) & 0xFFFFFFFF
                        part = ((full >> 16) & 0xFFFF) if rtype == 44 else (full & 0xFFFF)
                        patched = (insn & 0xFFF0F000) | ((part & 0xF000) << 4) | (part & 0x0FFF)
                        self.uc.mem_write(loc, struct.pack("<I", patched))
                    elif rtype == 40:  # R_ARM_V4BX
                        pass
                    else:
                        raise AssertionError(f"Unsupported ARM relocation type {rtype}")

    def _read_cstr(self, va: int, max_len: int = 256) -> str:
        raw = bytes(self.uc.mem_read(va, max_len))
        return raw.split(b"\x00", 1)[0].decode("latin1")

    def _flatten_sg(self, sg_va: int) -> tuple[int, list[tuple[int, int]]]:
        max_ent, num_ent, is_phys, _mapped, ent_ptr, _dir = struct.unpack(
            "<6I", self.uc.mem_read(sg_va, 24)
        )
        assert num_ent <= max_ent
        segs: list[tuple[int, int]] = []
        total = 0
        for i in range(num_ent):
            buf_ptr, _dma, length = struct.unpack("<3I", self.uc.mem_read(ent_ptr + i * 12, 12))
            va = (buf_ptr + 0x60000000) if is_phys else buf_ptr
            segs.append((va, length))
            total += length
        return total, segs

    def _on_emmc_hw_body(self, uc: Uc, address: int, size: int, user_data: object) -> None:
        if address == self.emmc_seq_read_va + 0x20:
            self.hw_read_calls += 1
            sg_va = uc.reg_read(UC_ARM_REG_R6)
            sector = uc.reg_read(UC_ARM_REG_R5)
            part_id = uc.reg_read(UC_ARM_REG_R7)
            total, segs = self._flatten_sg(sg_va)
            assert total % 512 == 0
            nsec = total // 512
            payload = bytearray()
            for s in range(nsec):
                payload += self.phys_emmc.get((part_id, sector + s), bytes([0xA5]) * 512)
            pos = 0
            for va, length in segs:
                uc.mem_write(va, bytes(payload[pos : pos + length]))
                pos += length
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif address == self.emmc_seq_write_va + 0x20:
            self.hw_write_calls += 1
            sg_va = uc.reg_read(UC_ARM_REG_R6)
            sector = uc.reg_read(UC_ARM_REG_R5)
            part_id = uc.reg_read(UC_ARM_REG_R8)
            total, segs = self._flatten_sg(sg_va)
            assert total % 512 == 0
            raw = bytearray()
            for va, length in segs:
                raw += uc.mem_read(va, length)
            for s in range(total // 512):
                self.phys_emmc[(part_id, sector + s)] = bytes(raw[s * 512 : (s + 1) * 512])
            uc.reg_write(UC_ARM_REG_R0, 0)

    def _format_printf(self, fmt: str, args: list[int]) -> str:
        out = []
        i = 0
        arg_idx = 0
        while i < len(fmt):
            if fmt[i] == "%" and i + 1 < len(fmt):
                spec = fmt[i + 1]
                val = args[arg_idx] if arg_idx < len(args) else 0
                arg_idx += 1
                if spec == "u":
                    out.append(str(val & 0xFFFFFFFF))
                elif spec == "d":
                    out.append(str(_sign_extend(val & 0xFFFFFFFF, 32)))
                elif spec == "x":
                    out.append(f"{val & 0xFFFFFFFF:x}")
                else:
                    out.append(spec)
                i += 2
            else:
                out.append(fmt[i])
                i += 1
        return "".join(out)

    def _on_stub(self, uc: Uc, address: int, size: int, user_data: object) -> None:
        name = self.addr_to_stub.get(address)
        if name is None:
            return
        r0 = uc.reg_read(UC_ARM_REG_R0)
        r1 = uc.reg_read(UC_ARM_REG_R1)
        r2 = uc.reg_read(UC_ARM_REG_R2)
        r3 = uc.reg_read(UC_ARM_REG_R3)
        sp = uc.reg_read(UC_ARM_REG_SP)

        if name == "kallsyms_lookup_name":
            sym = self._read_cstr(r0)
            table = {
                "emmc_seq_read": self.emmc_seq_read_va,
                "emmc_seq_write": self.emmc_seq_write_va,
                "Backup_save": self.backup_save_va,
                "wbi_drop": self.stub_addrs["wbi_drop"],
                "v7_coherent_kern_range": self.stub_addrs["v7_coherent_kern_range"],
            }
            uc.reg_write(UC_ARM_REG_R0, table.get(sym, 0))
        elif name == "vmalloc":
            aligned = (r0 + 0xFFF) & ~0xFFF
            addr = self.vmalloc_next
            self.vmalloc_next += aligned
            self.vmalloc_active[addr] = aligned
            uc.reg_write(UC_ARM_REG_R0, addr)
        elif name == "vfree":
            assert r0 in self.vmalloc_active, f"Invalid vfree({r0:#x})"
            del self.vmalloc_active[r0]
            self.vfree_calls += 1
        elif name == "memset":
            uc.mem_write(r0, bytes([r1 & 0xFF]) * r2)
            uc.reg_write(UC_ARM_REG_R0, r0)
        elif name == "memcpy":
            uc.mem_write(r0, bytes(uc.mem_read(r1, r2)))
            uc.reg_write(UC_ARM_REG_R0, r0)
        elif name in ("__copy_to_user", "__copy_from_user"):
            uc.mem_write(r0, bytes(uc.mem_read(r1, r2)))
            uc.reg_write(UC_ARM_REG_R0, 0)
        elif name == "strncmp":
            s1 = bytes(uc.mem_read(r0, r2)).split(b"\x00", 1)[0]
            s2 = bytes(uc.mem_read(r1, r2)).split(b"\x00", 1)[0]
            cmp_res = 0 if s1 == s2 else (-1 if s1 < s2 else 1)
            uc.reg_write(UC_ARM_REG_R0, cmp_res & 0xFFFFFFFF)
        elif name == "scnprintf":
            fmt = self._read_cstr(r2, 512)
            stack_words = list(struct.unpack("<16I", uc.mem_read(sp, 64)))
            rendered = self._format_printf(fmt, [r3] + stack_words).encode("ascii")
            cap = max(0, r1 - 1)
            clipped = rendered[:cap]
            uc.mem_write(r0, clipped + b"\x00")
            uc.reg_write(UC_ARM_REG_R0, len(clipped))
        elif name == "printk":
            fmt = self._read_cstr(r0, 256)
            self.printk_log.append(fmt)
            uc.reg_write(UC_ARM_REG_R0, len(fmt))
        elif name == "proc_create_data":
            self.proc_fops_va = r3
            self.proc_removed = False
            uc.reg_write(UC_ARM_REG_R0, 0x60990000)
        elif name == "remove_proc_entry":
            self.proc_removed = True
        elif name == "wbi_drop":
            self.wbi_drop_calls += 1
        elif name == "v7_coherent_kern_range":
            remove_cache = getattr(uc, "ctl_remove_cache", None)
            if remove_cache is not None:
                remove_cache(r0, r1)
        elif name == "msleep":
            pass
        elif name == "stop_machine":
            # Redirect control to fn(data) and let fn's bx lr return directly to stop_machine's caller!
            fn_va = r0
            data_arg = r1
            uc.reg_write(UC_ARM_REG_R0, data_arg)
            uc.reg_write(UC_ARM_REG_PC, fn_va)

    def call_arm(self, fn_va: int, *args: int) -> int:
        sp = self.STACK_BASE + self.STACK_SIZE - 0x100
        for idx, val in enumerate(args[:4]):
            self.uc.reg_write([UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3][idx], val & 0xFFFFFFFF)
        if len(args) > 4:
            extra = struct.pack(f"<{len(args) - 4}I", *(a & 0xFFFFFFFF for a in args[4:]))
            sp -= (len(extra) + 7) & ~7
            self.uc.mem_write(sp, extra)
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.uc.reg_write(UC_ARM_REG_LR, self.RETURN_SENTINEL)
        # Flush TB cache before starting a fresh top-level call in case code was patched/unpatched
        flush_tb = getattr(self.uc, "ctl_flush_tb", None)
        if flush_tb is not None:
            flush_tb()
        self.uc.emu_start(fn_va, self.RETURN_SENTINEL, count=5_000_000)
        return _sign_extend(self.uc.reg_read(UC_ARM_REG_R0), 32)

    def build_sg(self, chunks: list[tuple[int, int]], *, is_phys: int = 0) -> int:
        sg_va = self.sg_Cursor
        ent_va = sg_va + 0x40
        self.sg_Cursor += 0x200
        for i, (ptr, length) in enumerate(chunks):
            self.uc.mem_write(ent_va + i * 12, struct.pack("<3I", ptr, 0, length))
        self.uc.mem_write(sg_va, struct.pack("<6I", len(chunks), len(chunks), is_phys, 0, ent_va, 0))
        return sg_va


class EmunandPrototypeTests(unittest.TestCase):
    bundle_dir: Path | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if WSL_GCC_AVAILABLE:
            TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)
            cls.bundle_dir = Path(tempfile.mkdtemp(prefix="emunand-unit-", dir=TEST_ARTIFACTS))
            build_native_bundle(cls.bundle_dir, build_host_ctl=True)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.bundle_dir and cls.bundle_dir.exists():
            shutil.rmtree(cls.bundle_dir, ignore_errors=True)

    def test_ext2_vdisk_builder_matches_ext2_metadata_contract(self) -> None:
        files = {
            "emunand.manifest": b"setting/override.txt /setting/override.txt\n",
            "setting/override.txt": b"EMUNAND_SETTING_ACTIVE\n",
            "usr/large.bin": bytes((i * 37) & 0xFF for i in range(18_000)),  # exercises indirect block!
        }
        vdisk = build_ext2_vdisk(files, size_bytes=256 * 1024)
        digest = sha256_bytes(vdisk)
        TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ARTIFACTS) as tmp:
            img_path = Path(tmp) / "CFW_VDISK.IMG"
            img_path.write_bytes(vdisk)
            meta = Ext2Metadata(img_path, digest)
            self.assertEqual(meta.block_size, 1024)
            root_ino = meta.inode(2)
            self.assertEqual(root_ino["nlink"], 5)  # ., .., lost+found, setting, usr

    @unittest.skipUnless(WSL_GCC_AVAILABLE, "WSL ARM GCC toolchain unavailable")
    def test_ko_static_audit_and_unicorn_cow_guard_and_reversibility(self) -> None:
        assert self.bundle_dir is not None
        ko_path = self.bundle_dir / "emunand_guard.ko"
        audit = audit_ko_module(ko_path)
        self.assertEqual(audit["module_name"], "emunand_guard")

        # 1. Fail-closed prologue check when emmc_seq_write prologue does not match Sony's 16 bytes
        bad = UnicornEmunandKoHarness(ko_path)
        bad.uc.mem_write(bad.emmc_seq_write_va + 8, struct.pack("<I", 0xE1A00000))
        self.assertEqual(bad.call_arm(bad.symbols["init_module"]), -22)
        self.assertEqual(struct.unpack("<i", bad.uc.mem_read(bad.backup_save_va, 4))[0], 1)
        self.assertEqual(bad.vmalloc_active, {})

        # 2. Normal activation: patches prologues, zeroes Backup_save, calls wbi_drop
        h = UnicornEmunandKoHarness(ko_path)
        for s in range(10, 16):
            h.phys_emmc[(0, s)] = bytes([0x10 + s]) * 512
        h.phys_emmc[(1, 0)] = b"EXBL" + bytes(508)

        self.assertEqual(h.call_arm(h.symbols["init_module"]), 0)
        self.assertEqual(struct.unpack("<i", h.uc.mem_read(h.backup_save_va, 4))[0], 0)
        self.assertEqual(h.wbi_drop_calls, 1)
        self.assertIsNotNone(h.proc_fops_va)

        # 3. Split multi-entry al_sg write to user area (sectors 11, 12, 13): 0 hardware writes!
        wbuf1 = h.LOWMEM_VA_BASE + 0x1000
        wbuf2 = h.LOWMEM_VA_BASE + 0x2000
        sec11 = b"A" * 512
        sec12 = b"B" * 512
        sec13 = b"C" * 512
        h.uc.mem_write(wbuf1, sec11)
        h.uc.mem_write(wbuf2, sec12 + sec13)
        sg_w = h.build_sg([(wbuf1, 512), (wbuf2, 1024)])
        self.assertEqual(h.call_arm(h.emmc_seq_write_va, sg_w, 11, 0, 0, 0), 0)
        self.assertEqual(h.hw_write_calls, 0)
        # Physical eMMC remains untouched!
        self.assertEqual(h.phys_emmc[(0, 11)], bytes([0x10 + 11]) * 512)

        # 4. Mixed read spanning untouched + CoW sectors (10..13) overlays CoW seamlessly
        rbuf = h.LOWMEM_VA_BASE + 0x4000
        sg_r = h.build_sg([(rbuf, 2048)])
        self.assertEqual(h.call_arm(h.emmc_seq_read_va, sg_r, 10, 0, 0), 0)
        self.assertEqual(h.hw_read_calls, 1)
        got = bytes(h.uc.mem_read(rbuf, 2048))
        self.assertEqual(got[0:512], bytes([0x10 + 10]) * 512)
        self.assertEqual(got[512:1024], sec11)
        self.assertEqual(got[1024:1536], sec12)
        self.assertEqual(got[1536:2048], sec13)

        # 5. Pure-CoW read (sectors 11..13) hits fast path without calling hardware read!
        h.uc.mem_write(rbuf, bytes(1536))
        sg_r_fast = h.build_sg([(rbuf, 1536)])
        self.assertEqual(h.call_arm(h.emmc_seq_read_va, sg_r_fast, 11, 0, 0), 0)
        self.assertEqual(h.hw_read_calls, 1)  # Unchanged!
        self.assertEqual(bytes(h.uc.mem_read(rbuf, 1536)), sec11 + sec12 + sec13)

        # 6. Physical-address al_sg (is_phys=1, WBI style) + nflashaB0 (part_id=1) boot write-block
        pa_buf = 0x02008000  # Maps to VA 0x62008000 in LOWMEM_VA_BASE!
        va_for_pa = pa_buf + 0x60000000
        b0_new = b"FAKE_B0_" * 64
        h.uc.mem_write(va_for_pa, b0_new)
        sg_b0 = h.build_sg([(pa_buf, 512)], is_phys=1)
        self.assertEqual(h.call_arm(h.emmc_seq_write_va, sg_b0, 0, 0, 1, 0), 0)
        self.assertEqual(h.hw_write_calls, 0)
        self.assertEqual(h.phys_emmc[(1, 0)][:4], b"EXBL")  # Boot0 untouched!

        # 7. Read /proc/emunand_status and test clear_cow
        assert h.proc_fops_va is not None
        proc_read_fn, proc_write_fn = struct.unpack("<2I", h.uc.mem_read(h.proc_fops_va + 8, 8))
        ubuf = h.LOWMEM_VA_BASE + 0x9000
        ppos = h.LOWMEM_VA_BASE + 0x9400
        h.uc.mem_write(ppos, struct.pack("<Q", 0))
        n_read = h.call_arm(proc_read_fn, 0, ubuf, 512, ppos)
        self.assertGreater(n_read, 0)
        status_txt = bytes(h.uc.mem_read(ubuf, n_read)).decode("ascii")
        self.assertIn("guard_enabled=1\n", status_txt)
        self.assertIn("backup_save_orig=1\n", status_txt)
        self.assertIn("backup_save_now=0\n", status_txt)
        self.assertIn("cow_used_sectors=4\n", status_txt)
        self.assertIn("writes_intercepted=2\n", status_txt)
        self.assertIn("boot_writes_blocked=1\n", status_txt)
        self.assertIn("full_cow_reads=1\n", status_txt)

        # Clear CoW via /proc write -> reads revert to original physical eMMC sectors immediately
        cmd_va = h.LOWMEM_VA_BASE + 0x9500
        h.uc.mem_write(cmd_va, b"clear_cow\n")
        self.assertEqual(h.call_arm(proc_write_fn, 0, cmd_va, 10, ppos), 10)
        self.assertEqual(h.call_arm(h.emmc_seq_read_va, sg_r_fast, 11, 0, 0), 0)
        self.assertEqual(bytes(h.uc.mem_read(rbuf, 512)), bytes([0x10 + 11]) * 512)

        # 8. Reversible teardown (cleanup_module): restores prologues, Backup_save, frees pool
        h.call_arm(h.symbols["cleanup_module"])
        self.assertTrue(h.proc_removed)
        self.assertEqual(h.vfree_calls, 1)
        self.assertEqual(h.vmalloc_active, {})
        self.assertEqual(struct.unpack("<i", h.uc.mem_read(h.backup_save_va, 4))[0], 1)

        # Post-unload call executes original unpatched emmc_seq_write
        self.assertEqual(h.call_arm(h.emmc_seq_write_va, sg_w, 11, 0, 0, 0), 0)
        self.assertEqual(h.hw_write_calls, 1)
        self.assertEqual(h.phys_emmc[(0, 11)], sec11)

    @unittest.skipUnless(WSL_GCC_AVAILABLE, "WSL ARM GCC toolchain unavailable")
    def test_vdisk_ctl_fat32_exfat_fragmentation_and_killswitches(self) -> None:
        assert self.bundle_dir is not None
        host_ctl = self.bundle_dir / "emunand_vdisk_ctl_host"

        vdisk = build_ext2_vdisk(
            {"emunand.manifest": b"hello.txt /setting/hello.txt\n", "hello.txt": b"WORLD\n"},
            size_bytes=64 * 1024,
        )
        vdisk_sha = sha256_bytes(vdisk)

        with tempfile.TemporaryDirectory(dir=TEST_ARTIFACTS) as tmp:
            root = Path(tmp)
            cases = [
                ("fat32_contig", build_fat32_sd_image([("CFW_VDISK.IMG", vdisk)], card_bytes=4 * 1024 * 1024, partition_lba=0, fragmented_first_file=False), 1),
                ("fat32_frag", build_fat32_sd_image([("CFW_VDISK.IMG", vdisk)], card_bytes=4 * 1024 * 1024, partition_lba=0, fragmented_first_file=True), 16),
                ("exfat_contig", build_exfat_sd_image([("CFW_VDISK.IMG", vdisk)], card_bytes=4 * 1024 * 1024, partition_lba=0, fragmented_first_file=False), 1),
                ("exfat_frag", build_exfat_sd_image([("CFW_VDISK.IMG", vdisk)], card_bytes=4 * 1024 * 1024, partition_lba=0, fragmented_first_file=True), 16),
            ]
            for label, img_bytes, expected_extents in cases:
                img_path = root / f"{label}.img"
                stage_path = root / f"{label}.staged"
                img_path.write_bytes(img_bytes)

                proc = _run_host_ctl(host_ctl, "inspect", "--dev", to_wsl_path(img_path))
                self.assertEqual(proc.returncode, 0, msg=f"{label}: {proc.stderr}")
                self.assertIn("FORMAT=ext2", proc.stdout)
                self.assertIn(f"SIZE={len(vdisk)}", proc.stdout)
                self.assertIn(f"EXTENTS={expected_extents}", proc.stdout)
                self.assertIn(f"SHA256={vdisk_sha}", proc.stdout)

                # Stage-only reassembly matches original VDisk byte-for-byte
                sproc = _run_host_ctl(
                    host_ctl,
                    "attach",
                    "--dev", to_wsl_path(img_path),
                    "--stage", to_wsl_path(stage_path),
                    "--sha256", vdisk_sha,
                    "--stage-only",
                )
                self.assertEqual(sproc.returncode, 0, msg=f"{label} stage: {sproc.stderr}")
                self.assertEqual(stage_path.read_bytes(), vdisk)

                # Wrong SHA-256 is refused fail-closed (exit 7)
                bad_sha = _run_host_ctl(
                    host_ctl,
                    "attach",
                    "--dev", to_wsl_path(img_path),
                    "--stage", to_wsl_path(root / "never.img"),
                    "--sha256", "0" * 64,
                    "--stage-only",
                )
                self.assertEqual(bad_sha.returncode, 7)

            # Kill-switch EMUNAND.OFF on card root aborts with exit 4 before touching VDisk
            ks_img = root / "killswitch.img"
            ks_img.write_bytes(
                build_exfat_sd_image(
                    [("CFW_VDISK.IMG", vdisk), ("EMUNAND.OFF", b"")],
                    card_bytes=4 * 1024 * 1024,
                    partition_lba=0,
                )
            )
            ks_proc = _run_host_ctl(host_ctl, "inspect", "--dev", to_wsl_path(ks_img))
            self.assertEqual(ks_proc.returncode, 4)
            self.assertIn("EMUNAND_KILLSWITCH", ks_proc.stderr)

    @unittest.skipUnless(WSL_GCC_AVAILABLE, "WSL ARM GCC toolchain unavailable")
    def test_flashable_cramfs_vfat_fast_inspect_and_non_fs_rejection(self) -> None:
        assert self.bundle_dir is not None
        host_ctl = self.bundle_dir / "emunand_vdisk_ctl_host"

        # Executable mode (0755 -> 0x81ED) for sbin/ helper inside Custom-OS ext2 VDisk
        exec_vdisk = build_ext2_vdisk(
            {"sbin/custom_os_probe.elf": b"\x7fELF" + b"\x00" * 60, "etc/os-release": b"NAME=CustomOS\n"},
            size_bytes=64 * 1024,
        )
        with tempfile.TemporaryDirectory(dir=TEST_ARTIFACTS) as tmp:
            root = Path(tmp)
            ep = root / "exec.ext2"
            ep.write_bytes(exec_vdisk)
            meta = Ext2Metadata(ep, sha256_bytes(exec_vdisk))
            # Inode 14 or 15 is sbin/custom_os_probe.elf; verify at least one regular file has 0755 (0x81ED)
            modes = {meta.inode(i)["mode"] for i in range(12, 17)}
            self.assertIn(0x81ED, modes)

            # Synthetic cramfs image (0x28cd3d45 magic + "Compressed ROMFS") and WBI1 non-FS blob
            cramfs_blob = bytearray(4096)
            struct.pack_into("<II", cramfs_blob, 0, 0x28CD3D45, 4096)
            cramfs_blob[16:32] = b"Compressed ROMFS"
            wbi_blob = b"WBI1" + bytes(4092)
            vfat_inner = build_fat32_sd_image([("VMLINUX.BIN", b"KERN" * 256)], card_bytes=2 * 1024 * 1024, partition_lba=0)

            sd_multi = root / "multi_pkg.img"
            sd_multi.write_bytes(
                build_exfat_sd_image(
                    [
                        ("CRAMFS.IMG", bytes(cramfs_blob)),
                        ("NFLASH3.IMG", vfat_inner),
                        ("WBI_RAW.IMG", wbi_blob),
                    ],
                    card_bytes=8 * 1024 * 1024,
                    partition_lba=0,
                )
            )
            c_proc = _run_host_ctl(
                host_ctl, "inspect", "--fast", "--dev", to_wsl_path(sd_multi), "--image", "CRAMFS.IMG"
            )
            self.assertEqual(c_proc.returncode, 0, msg=c_proc.stderr)
            self.assertIn("FORMAT=cramfs SIZE=4096 EXTENTS=1", c_proc.stdout)
            self.assertIn("SHA256=skipped", c_proc.stdout)

            v_proc = _run_host_ctl(
                host_ctl, "inspect", "--fast", "--dev", to_wsl_path(sd_multi), "--image", "NFLASH3.IMG"
            )
            self.assertEqual(v_proc.returncode, 0, msg=v_proc.stderr)
            self.assertIn(f"FORMAT=vfat SIZE={len(vfat_inner)} EXTENTS=1", v_proc.stdout)

            w_proc = _run_host_ctl(
                host_ctl, "inspect", "--fast", "--dev", to_wsl_path(sd_multi), "--image", "WBI_RAW.IMG"
            )
            self.assertEqual(w_proc.returncode, 6)


if __name__ == "__main__":
    unittest.main()
