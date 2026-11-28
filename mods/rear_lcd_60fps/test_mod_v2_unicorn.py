"""Offline Cortex-A5 Thumb-2 Unicorn verification of RearLcd60fps.mod (no camera).

Validates both ILCE-6400 (2.00 captured layout) and ILCE-6400A (1.01) SMOD v2 builds,
including:
  1. SMOD v2 header, FNV-1a, 14-byte Backup_read prologue preimage, and RLC1 relocation table.
  2. Stock pass-through before ON and after OFF (0x01071489 -> 0x02 = 30 fps).
  3. Live ON while Rear LCD (dev == 1, E-mount adp == 0) is active: immediately invokes
     setCameraDispResolutionEEFrameRateNoAdapter(1), which reads 0x01071489 through the
     installed Backup_read hook and dispatches 0x01 (60 fps), while EVF High Quality
     (0x01071487 -> 0x02, 0x0107147b -> 0x00) and LA-EA adapter IDs (0x01071483 -> 0x02)
     stay untouched.
  4. Live ON while EVF (dev == 2) is active or while an LA-EA adapter (adp == 2 or 3) is
     attached: skips immediate NoAdapter(1) dispatch without disturbing EVF / LA-EA, while
     subsequent eye-sensor EVF->LCD Backup_read(0x01071489) calls return 0x01 (60 fps).
  5. Error / NULL-buffer safety when Backup_read fails (rc != 0).
  6. Runtime RLC1 relocation under shifted /proc/self/maps library bases.

Usage: python mods/rear_lcd_60fps/test_mod_v2_unicorn.py MOD_FILE [--target ilce_6400|ilce_6400a]
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

TARGETS = {
    "ilce_6400": {
        "hook_va": 0x0401F204,
        "resume_va": 0x0401F213,
        "adp_va": 0x15890FE1,
        "dev_va": 0x159E9E21,
        "set_va": 0x159E89A1,
        "backup_ref_lo": 0x0401E000,
        "view_ref_lo": 0x155E6000,
    },
    "ilce_6400a": {
        "hook_va": 0x0405C204,
        "resume_va": 0x0405C213,
        "adp_va": 0x131F3A0D,
        "dev_va": 0x1334DE61,
        "set_va": 0x1334C9E1,
        "backup_ref_lo": 0x0405B000,
        "view_ref_lo": 0x12F46000,
    },
}

ORIG_BACKUP_READ_14B = bytes.fromhex("80b584b002af07f108034ff0ff32")
# Remainder of Backup_read (+0x1212..+0x122e) calling a mock internal helper at +0x0da8:
#   +0x1212: 43f8 042d  str r2, [r3, #-4]!
#   +0x1216: 0432       adds r2, #4
#   +0x1218: 8de8 0300  stm.w sp, {r0, r1}
#   +0x121c: 1846       mov r0, r3
#   +0x121e: 00bf       nop (replaces PC-lit load not needed by mock)
#   +0x1220: 0223       movs r3, #2
#   +0x1222: fff7 c1ef  bl #0xda8 (we intercept execution at resume_stub_call)
# Instead of a long relative BL, we place an authentic stack-frame body at resume_va & ~1
# that stores {r0=id, r1=buf} on stack as real Backup_read does, invokes SVC #90 so the
# test harness services the exact BK4 table lookup, writes rc to [r7,#4], and pops {r7, pc}.
MOCK_BACKUP_READ_TAIL = (
    bytes.fromhex("43f8042d")  # str r2, [r3, #-4]!   ([r7,#4] = -1)
    + bytes.fromhex("0432")    # adds r2, #4
    + bytes.fromhex("8de80300")  # stm.w sp, {r0, r1} ([sp,#0]=id, [sp,#4]=buf)
    + bytes.fromhex("5adf")    # svc #0x5a            (harness services [sp,#0],[sp,#4] -> [r7,#4])
    + bytes.fromhex("7868")    # ldr r0, [r7, #4]
    + bytes.fromhex("07f10807")  # add.w r7, r7, #8
    + bytes.fromhex("bd46")    # mov sp, r7
    + bytes.fromhex("80bd")    # pop {r7, pc}
)

# Mock getMountAdapterType() -> SVC #0x5b returns adapter byte in r0; bx lr
MOCK_GET_ADP = bytes.fromhex("5bdf7047")
# Mock getPanelOutDevice() -> SVC #0x5c returns panel dev int in r0; bx lr
MOCK_GET_DEV = bytes.fromhex("5cdf7047")


def make_mock_set_ee_fps(backup_read_va: int) -> bytes:
    """Thumb-2 stub matching setCameraDispResolutionEEFrameRateNoAdapter(int dev):
    Reads BK4 0x01070c8a via Backup_read, then reads 0x01071489 (High, dev==1),
    0x0107148a (Standard, dev==1), or 0x01071487 (High, dev==2) via Backup_read,
    and reports (dev, ee_fps) via SVC #0x5d.
    """
    # push {r4, r5, r7, lr}; sub sp, #8; add r7, sp, #0; mov r4, r0
    # movw r0, #0x0c8a; movt r0, #0x0107; adds r1, r7, #7; ldr r5, [pc, #...]; blx r5
    # ldrb r3, [r7, #7]; cbnz r3, std_branch
    # high_branch: movw r0, #0x1489; cmp r4, #1; beq do_read; movw r0, #0x1487; b do_read
    # std_branch:  movw r0, #0x148a
    # do_read: movt r0, #0x0107; adds r1, r7, #6; blx r5; mov r0, r4; ldrb r1, [r7, #6]; svc #0x5d
    # add sp, #8; pop {r4, r5, r7, pc}; .balign 4; .word (backup_read_va | 1)
    code = bytearray()
    code += bytes.fromhex("b0b582b000af0446")              # 0x00..0x07 (8B)
    code += bytes.fromhex("40f68a40c0f20710")              # 0x08..0x0f: movw r0,#0xc8a; movt r0,#0x107
    code += bytes.fromhex("f91d0c4da847")                  # 0x10..0x15: adds r1,r7,#7; ldr r5,[pc,#0x30]->0x44; blx r5
    code += bytes.fromhex("fb7933b9")                      # 0x16..0x19: ldrb r3,[r7,#7]; cbnz r3, +12 -> 0x2a
    code += bytes.fromhex("41f28940012c02d041f2874001e0")  # 0x1a..0x27 (14B): movw r0,#0x1489; cmp r4,#1; beq 0x2a; movw r0,#0x1487; b 0x2c
    # Fix branch offsets cleanly:
    # Let's assemble exact halfwords with verified offsets:
    code.clear()
    code += bytes.fromhex("b0b5")          # 0x00: push {r4, r5, r7, lr}
    code += bytes.fromhex("82b0")          # 0x02: sub sp, #8
    code += bytes.fromhex("00af")          # 0x04: add r7, sp, #0
    code += bytes.fromhex("0446")          # 0x06: mov r4, r0
    code += bytes.fromhex("40f68a40")      # 0x08: movw r0, #0x0c8a
    code += bytes.fromhex("c0f20710")      # 0x0c: movt r0, #0x0107
    code += bytes.fromhex("f91d")          # 0x10: adds r1, r7, #7
    code += bytes.fromhex("0c4d")          # 0x12: ldr r5, [pc, #0x30] -> Align(0x16,4)+0x30 = 0x44
    code += bytes.fromhex("a847")          # 0x14: blx r5
    code += bytes.fromhex("fb79")          # 0x16: ldrb r3, [r7, #7]
    code += bytes.fromhex("3bb9")          # 0x18: cbnz r3, #0x2a (0x1c + 14 = 0x2a)
    code += bytes.fromhex("41f28940")      # 0x1a: movw r0, #0x1489
    code += bytes.fromhex("012c")          # 0x1e: cmp r4, #1
    code += bytes.fromhex("03d0")          # 0x20: beq #0x2a? Wait: do_read is 0x2e -> (0x2e - 0x24)/2 = 5 -> 05d0!
    code[-2:] = bytes.fromhex("05d0")      # 0x20: beq #0x2e
    code += bytes.fromhex("41f28740")      # 0x22: movw r0, #0x1487
    code += bytes.fromhex("02e0")          # 0x26: b #0x2e ((0x2e - 0x2a)/2 = 2)
    code += bytes.fromhex("00bf")          # 0x28: nop (padding so std_branch == 0x2a)
    code += bytes.fromhex("41f28a40")      # 0x2a: movw r0, #0x148a
    # 0x2e: do_read
    code += bytes.fromhex("c0f20710")      # 0x2e: movt r0, #0x0107
    code += bytes.fromhex("b91d")          # 0x32: adds r1, r7, #6
    code += bytes.fromhex("a847")          # 0x34: blx r5
    code += bytes.fromhex("2046")          # 0x36: mov r0, r4
    code += bytes.fromhex("b979")          # 0x38: ldrb r1, [r7, #6]
    code += bytes.fromhex("5ddf")          # 0x3a: svc #0x5d
    code += bytes.fromhex("02b0")          # 0x3c: add sp, #8
    code += bytes.fromhex("b0bd")          # 0x3e: pop {r4, r5, r7, pc}
    code += bytes.fromhex("00bf00bf")      # 0x40..0x43: nop; nop
    assert len(code) == 0x44
    code += struct.pack("<I", backup_read_va | 1)
    return bytes(code)


def run_suite(mod_bytes: bytes, target_id: str, bias_backup: int = 0, bias_view: int = 0) -> None:
    cfg = TARGETS[target_id]
    magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from(
        "<III32s6I", mod_bytes
    )
    assert magic == 0x444F4D53 and ver == 2, (hex(magic), ver)
    assert name.rstrip(b"\0") == b"Rear LCD 60fps", name
    assert bss == 32, bss
    assert hooks == 1, hooks
    assert (flags & 4) != 0, "expected SMOD_V2_FLAG_RELOC"

    hook_off = 68
    target_va, orig_len, fix_off, orig_bytes, tmpl_bytes = struct.unpack_from(
        "<IBB2x16s16s", mod_bytes, hook_off
    )
    assert target_va == cfg["hook_va"], (hex(target_va), hex(cfg["hook_va"]))
    assert orig_len == 14, orig_len
    assert orig_bytes[:orig_len] == ORIG_BACKUP_READ_14B, orig_bytes[:orig_len].hex()
    hook_entry_off = struct.unpack_from("<I", tmpl_bytes, fix_off)[0]
    assert (hook_entry_off & 1) == 1

    code_off = hook_off + hooks * 40
    code = bytearray(mod_bytes[code_off : code_off + code_size])

    rlc_off = code_off + code_size
    rlc_magic, rlc_libs, rlc_count = struct.unpack_from("<IHH", mod_bytes, rlc_off)
    assert rlc_magic == 0x31434C52, hex(rlc_magic)
    assert rlc_libs == 2 and rlc_count == 4, (rlc_libs, rlc_count)

    libs = {}
    for i in range(rlc_libs):
        lo, hi, lname = struct.unpack_from("<II24s", mod_bytes, rlc_off + 8 + i * 32)
        libs[i] = (lo, hi, lname.rstrip(b"\0").decode())
    assert {v[2] for v in libs.values()} == {"libbackup.so", "viewUnified2.so"}, libs

    lib_biases = {"libbackup.so": bias_backup, "viewUnified2.so": bias_view}
    seen_words = []
    rel_base = rlc_off + 8 + rlc_libs * 32
    for i in range(rlc_count):
        entry = struct.unpack_from("<I", mod_bytes, rel_base + i * 4)[0]
        li = entry >> 24
        woff = entry & 0xFFFFFF
        orig_w = struct.unpack_from("<I", code, woff)[0]
        seen_words.append((libs[li][2], orig_w))
        struct.pack_into("<I", code, woff, (orig_w + lib_biases[libs[li][2]]) & 0xFFFFFFFF)

    expected_words = {
        ("libbackup.so", cfg["resume_va"]),
        ("viewUnified2.so", cfg["adp_va"]),
        ("viewUnified2.so", cfg["dev_va"]),
        ("viewUnified2.so", cfg["set_va"]),
    }
    assert set(seen_words) == expected_words, (seen_words, expected_words)

    MOD_BASE = 0x41200000
    STACK_BASE = 0x20000000
    STOP = 0x31000000

    hook_rt = (cfg["hook_va"] + bias_backup) & 0xFFFFFFFF
    resume_rt = ((cfg["resume_va"] & ~1) + bias_backup) & 0xFFFFFFFF
    adp_rt = ((cfg["adp_va"] & ~1) + bias_view) & 0xFFFFFFFF
    dev_rt = ((cfg["dev_va"] & ~1) + bias_view) & 0xFFFFFFFF
    set_rt = ((cfg["set_va"] & ~1) + bias_view) & 0xFFFFFFFF

    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    m.mem_map(MOD_BASE, 0x4000)
    m.mem_write(MOD_BASE, bytes(code))
    m.mem_write(MOD_BASE + len(code), b"\x00" * bss)

    pages: set[int] = set()
    for addr in (hook_rt, resume_rt, adp_rt, dev_rt, set_rt):
        pg = addr & ~0xFFF
        pages.add(pg)
        pages.add(pg + 0x1000)
    for pg in sorted(pages):
        m.mem_map(pg, 0x1000)

    m.mem_write(hook_rt, ORIG_BACKUP_READ_14B)
    m.mem_write(resume_rt, MOCK_BACKUP_READ_TAIL)
    m.mem_write(adp_rt, MOCK_GET_ADP)
    m.mem_write(dev_rt, MOCK_GET_DEV)
    m.mem_write(set_rt, make_mock_set_ee_fps(hook_rt))

    m.mem_map(STACK_BASE, 0x10000)
    m.mem_map(STOP, 0x1000)

    bk4 = {
        0x01070C8A: 0x00,  # Display Quality = High (0)
        0x0107147B: 0x00,  # EVF High dispRes = 0x00 (1996x800)
        0x0107147D: 0x01,  # Rear LCD High dispRes = 0x01 (1996x570)
        0x01071483: 0x02,  # WithAdapter High Rear LCD = 30 fps
        0x01071487: 0x02,  # NoAdapter High EVF = 30 fps
        0x01071488: 0x01,  # NoAdapter Standard EVF = 60 fps
        0x01071489: 0x02,  # NoAdapter High Rear LCD = 30 fps (stock)
        0x0107148A: 0x01,  # NoAdapter Standard Rear LCD = 60 fps
    }
    env = {"adp": 0, "dev": 1, "force_bkup_err": False}
    sent_172: list[tuple[int, int]] = []
    logs: list[str] = []

    def intr(mu: uc.Uc, intno: int, _user) -> None:
        # Read immediate from Thumb svc #imm8 right before PC
        pc = mu.reg_read(A.UC_ARM_REG_PC)
        svc_ins = struct.unpack_from("<H", bytes(mu.mem_read(pc - 2, 2)))[0]
        imm = svc_ins & 0xFF
        if imm == 0x5A:  # mock Backup_read lookup
            sp = mu.reg_read(A.UC_ARM_REG_SP)
            r7 = mu.reg_read(A.UC_ARM_REG_R7)
            uid, buf_ptr = struct.unpack_from("<II", bytes(mu.mem_read(sp, 8)))
            if env["force_bkup_err"] or uid not in bk4:
                rc = -1
            else:
                if buf_ptr != 0:
                    mu.mem_write(buf_ptr, bytes([bk4[uid]]))
                rc = 0
            mu.mem_write(r7 + 4, struct.pack("<i", rc))
            return
        if imm == 0x5B:
            mu.reg_write(A.UC_ARM_REG_R0, env["adp"])
            return
        if imm == 0x5C:
            mu.reg_write(A.UC_ARM_REG_R0, env["dev"] & 0xFFFFFFFF)
            return
        if imm == 0x5D:
            sent_172.append((mu.reg_read(A.UC_ARM_REG_R0), mu.reg_read(A.UC_ARM_REG_R1)))
            return
        assert imm == 0, f"unexpected svc #{imm:#x}"
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a0, a1, a2 = (mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2))
        if nr == 5:
            path = bytes(mu.mem_read(a0, 64)).split(b"\0", 1)[0].decode()
            assert path == "/tmp_bt/mods/rear_lcd_60fps.log", path
            mu.reg_write(A.UC_ARM_REG_R0, 7)
        elif nr == 4:
            assert a0 == 7
            logs.append(bytes(mu.mem_read(a1, a2)).decode())
            mu.reg_write(A.UC_ARM_REG_R0, a2)
        elif nr == 6:
            assert a0 == 7
            mu.reg_write(A.UC_ARM_REG_R0, 0)
        else:
            raise AssertionError(f"unexpected Linux syscall {nr}")

    m.hook_add(uc.UC_HOOK_INTR, intr)

    def install_hook() -> None:
        patched = bytearray(tmpl_bytes[:orig_len])
        struct.pack_into("<I", patched, fix_off, MOD_BASE + hook_entry_off)
        m.mem_write(hook_rt, bytes(patched))
        m.ctl_remove_cache(hook_rt, hook_rt + 16)

    def revert_hook() -> None:
        m.mem_write(hook_rt, ORIG_BACKUP_READ_14B)
        m.ctl_remove_cache(hook_rt, hook_rt + 16)

    def call_backup_read(uid: int, out_addr: int = STACK_BASE + 0x800) -> tuple[int, int]:
        if out_addr:
            m.mem_write(out_addr, b"\xaa")
        m.reg_write(A.UC_ARM_REG_SP, STACK_BASE + 0xF000)
        m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        m.reg_write(A.UC_ARM_REG_R0, uid)
        m.reg_write(A.UC_ARM_REG_R1, out_addr)
        m.emu_start(hook_rt | 1, STOP, count=2000)
        rc = struct.unpack("<i", struct.pack("<I", m.reg_read(A.UC_ARM_REG_R0)))[0]
        val = m.mem_read(out_addr, 1)[0] if out_addr else 0xAA
        return rc, val

    def call_life(off: int) -> None:
        m.reg_write(A.UC_ARM_REG_SP, STACK_BASE + 0xF000)
        m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        m.emu_start((MOD_BASE + off) | 1, STOP, count=20000)
        assert m.reg_read(A.UC_ARM_REG_R0) == 0

    # 1. Unhooked stock behavior: 0x01071489 -> 0x02 (30 fps)
    assert call_backup_read(0x01071489) == (0, 0x02)
    assert call_backup_read(0x01071487) == (0, 0x02)

    # 2. Enable mod (matches mlv2 core.c: install hook first, then call on()) while Rear LCD active
    env["adp"] = 0
    env["dev"] = 1
    sent_172.clear()
    install_hook()
    call_life(on_off & ~1)
    assert sent_172 == [(1, 0x01)], f"expected live 60fps apply (1, 1), got {sent_172}"
    assert logs and "rear_lcd_60fps on=1" in logs[-1], logs

    # 3. Direct Backup_read checks while ON:
    #    - 0x01071489 (NoAdapter High Rear LCD) -> overridden to 0x01 (60 fps)!
    #    - 0x01071487 (NoAdapter High EVF)      -> untouched 0x02 (30 fps)!
    #    - 0x0107147B (NoAdapter High EVF res)  -> untouched 0x00!
    #    - 0x01071483 (WithAdapter High LCD)    -> untouched 0x02 (30 fps)!
    assert call_backup_read(0x01071489) == (0, 0x01)
    assert call_backup_read(0x01071487) == (0, 0x02)
    assert call_backup_read(0x0107147B) == (0, 0x00)
    assert call_backup_read(0x01071483) == (0, 0x02)
    assert bk4[0x01071489] == 0x02, "underlying BK4 storage must never be mutated"

    # 4. Error & NULL-pointer guards while ON:
    env["force_bkup_err"] = True
    assert call_backup_read(0x01071489) == (-1, 0xAA)
    env["force_bkup_err"] = False
    assert call_backup_read(0x01071489, out_addr=0) == (0, 0xAA)

    # 5. Disable mod while Rear LCD active (matches mlv2 core.c: call off() BEFORE hooks_revert()):
    sent_172.clear()
    call_life(off_off & ~1)
    assert sent_172 == [(1, 0x02)], f"expected live 30fps restore (1, 2) on OFF, got {sent_172}"
    assert call_backup_read(0x01071489) == (0, 0x02), "hook with enabled==0 must pass stock 0x02"
    revert_hook()
    assert call_backup_read(0x01071489) == (0, 0x02)
    assert "rear_lcd_60fps on=0" in logs[-1]

    # 6. Enable while EVF (dev == 2) is active: must NOT call NoAdapter(1) live,
    #    but eye-sensor switch to Rear LCD (Backup_read 0x01071489) returns 0x01!
    env["dev"] = 2
    sent_172.clear()
    install_hook()
    call_life(on_off & ~1)
    assert sent_172 == [], f"must not disturb active EVF on toggle, got {sent_172}"
    assert call_backup_read(0x01071489) == (0, 0x01)
    call_life(off_off & ~1)
    assert sent_172 == []
    revert_hook()

    # 7. Enable while LA-EA mount adapter (adp == 2 and adp == 3) is attached on Rear LCD:
    #    must NOT call NoAdapter(1) live!
    for laea in (2, 3):
        env["dev"] = 1
        env["adp"] = laea
        sent_172.clear()
        install_hook()
        call_life(on_off & ~1)
        assert sent_172 == [], f"must not override LA-EA adapter {laea}, got {sent_172}"
        call_life(off_off & ~1)
        assert sent_172 == []
        revert_hook()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mod_file", type=Path)
    ap.add_argument("--target", choices=sorted(TARGETS), default="ilce_6400")
    args = ap.parse_args()

    raw = args.mod_file.read_bytes()
    run_suite(raw, args.target, bias_backup=0, bias_view=0)
    run_suite(raw, args.target, bias_backup=0x00300000, bias_view=-0x00400000)
    print(f"PASS: {args.mod_file} ({args.target}) verified at reference + relocated bases")


if __name__ == "__main__":
    main()
