"""Offline Unicorn test of the SMOD v2 SilentFlashUnlock build (no camera).

Usage: python mods/silent_flash_unlock/test_mod_v2_unicorn.py BUILD_DIR
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

BUILD_DIR = Path(sys.argv[1])
mod = (BUILD_DIR / "SilentFlashUnlock.mod").read_bytes()

magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from(
    "<III32s6I", mod
)
assert magic == 0x444F4D53, f"bad magic {magic:#x}"
assert ver == 2, f"bad ver {ver}"
assert name.rstrip(b"\0") == b"Silent Flash", name
assert bss == 6980, f"unexpected bss {bss}"
assert hooks == 5, f"expected 5 hooks, got {hooks}"
assert (flags & 4) != 0, "expected FLAG_RELOC (4)"

hook_off = 68
target_va, orig_len, fix_off, orig_bytes, tmpl_bytes = struct.unpack_from(
    "<IBB2x16s16s", mod, hook_off
)
assert target_va == 0x069888A0, f"unexpected target_va {target_va:#x}"
assert orig_len == 12, f"unexpected orig_len {orig_len}"
assert orig_bytes[:orig_len] == bytes.fromhex("2de9f04104464cf2b60000af"), orig_bytes.hex()
hook_entry_off = struct.unpack_from("<I", tmpl_bytes, fix_off)[0]
assert (hook_entry_off & 1) == 1, f"hook_entry_off must be Thumb: {hook_entry_off:#x}"

code_off = hook_off + hooks * 40
code = mod[code_off : code_off + code_size]

rlc_off = code_off + code_size
rlc_magic, rlc_libs, rlc_count = struct.unpack_from("<IHH", mod, rlc_off)
assert rlc_magic == 0x31434C52, f"expected RLC1 trailer, got {rlc_magic:#x}"
assert rlc_libs == 3 and rlc_count == 8, f"expected 3 libs / 8 words, got {rlc_libs}/{rlc_count}"
libs = [struct.unpack_from("<II24s", mod, rlc_off + 8 + 32 * i)[2].rstrip(b"\0").decode() for i in range(rlc_libs)]
reloc_vals = {}
for i in range(rlc_count):
    e = struct.unpack_from("<I", mod, rlc_off + 8 + 32 * rlc_libs + 4 * i)[0]
    reloc_vals.setdefault(libs[e >> 24], set()).add(struct.unpack_from("<I", code, e & 0xFFFFFF)[0])
assert reloc_vals == {"libObj.so": {0x069888AD, 0x06729D35, 0x0708A57C, 0x063F8D29, 0x063F8D31}, "libSysDef.so": {0x056918B8},
                      "libMWF.so": {0x04115339, 0x041153B1}}, reloc_vals
INH_HOOKS = []
for k in (1, 2, 3, 4):
    t_va, o_len, f_off, o_b, t_b = struct.unpack_from("<IBB2x16s16s", mod, hook_off + 40 * k)
    INH_HOOKS.append((t_va, o_len, f_off, o_b[:o_len], t_b[:o_len]))
assert [(h[0], h[3].hex()) for h in INH_HOOKS] == [
    (0x0411532C, "2de9f0410d46194982b08046"), (0x041153A4, "f0b50d46174983b0064600af"),
    (0x06729D28, "2de9f843044600af16468846"), (0x063F8D1C, "f0b500af15461c467e691eb9")], INH_HOOKS


SYSDEF_RW_BASE = 0x05691000
INH_REF_TBL_VA = 0x056918B8
INH_TBL_FILE_OFF = 0x188B8
INH_TBL_LEN = 2190 * 160 * 4
ORIG_INH_TBL = (Path(__file__).resolve().parents[2] / "artifacts" / "analysis" / "cam-usr" / "lib"
                / "libSysDef.so").read_bytes()[INH_TBL_FILE_OFF:INH_TBL_FILE_OFF + INH_TBL_LEN]
assert len(ORIG_INH_TBL) == INH_TBL_LEN
SILENT_FACTORS = [0x0A83, 0x0DE2, 0x114F, 0x1296, 0x13BB]
INH_FEATS = [0x180, 0x181, 0x182, 0x184, 0x185, 0x186, 0x187, 0x769, 0x76B]
SENTINEL_FEATS = [0x16, 0x118, 0x183, 0x509, 0x51B, 0x832]

BASE = 0x40AB3000
DB_SINGLE = 0x20020000
DB_ARRAY = 0x20021000
DB_PARAMS = 0x20025000
LIBOBJ_PAGE = 0x06988000
RESUME_ADDR = 0x069888AC
OUT_BUF_ADDR = 0x20008000
SELF_PTR = 0x12345678
STOP = 0x31000000
AVCAM_PA = 0x03BCFD26
DEVMEM_VA = 0x32000000
AVCAM_CTX_STOCK = bytes.fromhex("e4f49ffd10b1032384f8")
AVCAM_CTX_PATCH = bytes.fromhex("e4f49ffd02e0032384f8")
AVCAM_OFF = (AVCAM_PA & 0xFFF) - 4
MASK_OFF = 0xDF4
MASK_STOCK = bytes.fromhex("11fa03f313f07b0203d1")
MASK_PATCH = bytes.fromhex("11fa03f313f07f0203d1")
devmem_maps: list[int] = []

# Mock original body at 0x069888AC (same as the SilentShutterUnlock test): records
# self, r0 (0xc0b6 from the replayed prologue), src, dst, dst_val in out_buf; returns 0x3a.
MOCK_ORIG_CODE = bytes.fromhex(
    "ddf81c50" "2c60" "6860" "a960" "eb60" "ddf81860" "2e61" "3a20" "bde8f081"
)


def make_machine() -> tuple[uc.Uc, list[str]]:
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    map_sz = ((len(code) + bss + 0x2000) + 0xFFF) & ~0xFFF
    m.mem_map(BASE, map_sz)
    m.mem_write(BASE, code)
    m.mem_write(BASE + len(code), b"\x00" * bss)
    m.mem_map(LIBOBJ_PAGE, 0x1000)
    patched = bytearray(tmpl_bytes[:orig_len])
    struct.pack_into("<I", patched, fix_off, BASE + hook_entry_off)
    m.mem_write(target_va, bytes(patched))
    m.mem_map(0x04115000, 0x1000)
    m.mem_map(0x06729000, 0x1000)
    m.mem_map(0x063F8000, 0x1000)
    m.mem_map(0x0708A000, 0x1000)
    m.mem_map(0x20020000, 0x10000)
    m.mem_write(0x0708A57C, struct.pack("<I", DB_SINGLE))
    m.mem_write(DB_SINGLE + 0x1A20, struct.pack("<I", DB_ARRAY))
    m.mem_write(0x063F8D28, bytes.fromhex("00f06ab800bf00bf00f066b800000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000049f271539d4203d1086820600020f0bd49f685139d4205d1886818b1486820600020f0bd0120f0bd"))
    for t_va, o_len, f_off, _o, t_b in INH_HOOKS:
        pt = bytearray(t_b)
        struct.pack_into("<I", pt, f_off, BASE + struct.unpack_from("<I", t_b, f_off)[0])
        m.mem_write(t_va, bytes(pt))
    # Mock bodies at the resume points: store the replayed-prologue registers to OUT_BUF.
    m.mem_write(0x04115338, bytes.fromhex("48f20003c2f20003c3f800805d609960da604220 02b0bde8f081".replace(" ", "")))
    m.mem_write(0x06729D34, bytes.fromhex("48f20003c2f200031c60c3f804809e604420bde8f883"))
    m.mem_write(0x041153B0, bytes.fromhex("48f20003c2f200031e605d6099604320 03b0f0bd".replace(" ", "")))
    m.mem_write(RESUME_ADDR, MOCK_ORIG_CODE)
    m.mem_map(0x20000000, 0x10000)
    m.mem_map(STOP, 0x1000)
    m.mem_map(DEVMEM_VA, 0x1000)
    m.mem_write(DEVMEM_VA + AVCAM_OFF, AVCAM_CTX_STOCK)
    m.mem_write(DEVMEM_VA + MASK_OFF, MASK_STOCK)
    m.mem_map(SYSDEF_RW_BASE, 0x160000)
    m.mem_write(INH_REF_TBL_VA, ORIG_INH_TBL)
    logs: list[str] = []

    def intr(mu, _n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2)]
        if nr == 5:
            path = bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode(errors="replace")
            if path == "/dev/mem":
                assert a[1] == (0o2 | 0o4010000), f"/dev/mem flags {a[1]:#o}"   # O_RDWR | O_SYNC
                ret = 7
            else:
                assert path == "/tmp_bt/mods/silent_flash_unlock.log", f"unexpected open {path}"
                ret = 4
        elif nr == 192:   # mmap2(addr, len, prot, flags, fd, pgoff)
            r3, r4, r5 = (mu.reg_read(r) for r in (A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5))
            assert a[1] == 4096 and a[2] == 3 and r3 == 1 and r4 == 7, (a, r3, r4)
            assert r5 == AVCAM_PA >> 12, f"mmap2 pgoff {r5:#x}"
            devmem_maps.append(r5)
            ret = DEVMEM_VA
        elif nr == 91:
            assert a[0] == DEVMEM_VA and a[1] == 4096
            ret = 0
        elif nr == 4:
            assert a[0] == 4
            logs.append(bytes(mu.mem_read(a[1], a[2])).decode())
            ret = a[2]
        elif nr == 6:
            ret = 0
        else:
            raise AssertionError(f"unexpected syscall {nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret & 0xFFFFFFFF)

    m.hook_add(uc.UC_HOOK_INTR, intr)
    return m, logs


def call_life(m: uc.Uc, entry_off: int) -> None:
    m.reg_write(A.UC_ARM_REG_SP, 0x20010000)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start((BASE + entry_off) | 1, STOP, count=20000)
    assert m.reg_read(A.UC_ARM_REG_R0) == 0


def call_hook(m: uc.Uc, src: int, src_val: int, dst: int, dst_val: int) -> tuple[int, list[int]]:
    m.mem_write(OUT_BUF_ADDR, b"\x00" * 20)
    sp = 0x2000F000
    m.mem_write(sp, struct.pack("<II", dst_val, OUT_BUF_ADDR))
    m.reg_write(A.UC_ARM_REG_SP, sp)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.reg_write(A.UC_ARM_REG_R0, SELF_PTR)
    m.reg_write(A.UC_ARM_REG_R1, src)
    m.reg_write(A.UC_ARM_REG_R2, src_val)
    m.reg_write(A.UC_ARM_REG_R3, dst)
    m.emu_start(target_va | 1, STOP, count=20000)
    return m.reg_read(A.UC_ARM_REG_R0), list(struct.unpack("<5I", m.mem_read(OUT_BUF_ADDR, 20)))


def state(m: uc.Uc) -> tuple:
    return struct.unpack("<6I24I2I", m.mem_read(BASE + len(code), 128))


m, logs = make_machine()

# 1. OFF: a flash pair falls through to the original evaluator with all args intact.
ret, out = call_hook(m, 0x9983, 0x451, 0x956F, 0x100)
assert ret == 0x3A and out == [SELF_PTR, 0xC0B6, 0x9983, 0x956F, 0x100], (hex(ret), out)

# 2. ON
call_life(m, on_off)
assert bytes(m.mem_read(DEVMEM_VA + AVCAM_OFF, 10)) == AVCAM_CTX_PATCH, "av-cam branch not patched"
assert bytes(m.mem_read(DEVMEM_VA + MASK_OFF, 10)) == MASK_PATCH, "av-cam mask not patched"
assert logs[-1].rstrip().endswith("avcam=ww"), logs[-1]
def inh_word(feat: int, fac: int) -> int:
    return struct.unpack("<I", m.mem_read(INH_REF_TBL_VA + (feat * 160 + (fac >> 5)) * 4, 4))[0]


def orig_word(feat: int, fac: int) -> int:
    return struct.unpack_from("<I", ORIG_INH_TBL, (feat * 160 + (fac >> 5)) * 4)[0]


expected_inh = sum(1 for f in INH_FEATS for fac in SILENT_FACTORS if orig_word(f, fac) >> (fac & 31) & 1)
assert expected_inh == 36, expected_inh
assert f"silent_flash on=1 bypass=0x00000000 total=0x00000000 pairs=0x00000000 inh=0x{expected_inh:08x}" in logs[-1], logs[-1]
for f in INH_FEATS:
    for fac in SILENT_FACTORS:
        assert not (inh_word(f, fac) >> (fac & 31)) & 1, f"feat {f:#x} factor {fac:#x} still set"
for f in SENTINEL_FEATS:
    for fac in SILENT_FACTORS:
        assert inh_word(f, fac) == orig_word(f, fac), f"sentinel {f:#x} {fac:#x} modified"
assert state(m)[0] == 0x554C4653

unlocked = [
    (0x9983, 0x956F),  # Silent -> Strobe Mode
    (0x956F, 0x9983),  # Strobe Mode -> Silent
    (0xA013, 0x956F),  # EXC Silent -> Strobe Mode
    (0x9983, 0x9F62),  # Silent -> Wireless
    (0x9983, 0x95FF),  # Silent -> Flash compensation
    (0x9983, 0x96F0),  # Silent -> Strobe control mode
    (0x9983, 0x972C),  # Silent -> E-mount red-eye
    (0x9925, 0xA013),  # FEL -> EXC Silent
]
for sp_, dp in unlocked:
    ret, out = call_hook(m, sp_, 0x451, dp, 0x222)
    assert ret == 0 and out == [0] * 5, f"pair ({sp_:#x}, {dp:#x}) failed: {ret:#x} {out}"

stock = [
    (0x9983, 0x9A53),  # Silent -> Picture Profile (SilentShutterUnlock's job, not this mod's)
    (0x9983, 0x95A4),  # Long Exposure NR
    (0x9983, 0x9B50),  # Flickerless
    (0x9983, 0x956C),  # Hotshoe strobe status (read-only status, not bypassed)
    (0x95C8, 0x956F),  # Exposure Mode -> Strobe (no Silent param)
]
for sp_, dp in stock:
    ret, out = call_hook(m, sp_, 0x451, dp, 0x777)
    assert ret == 0x3A and out == [SELF_PTR, 0xC0B6, sp_, dp, 0x777], f"stock ({sp_:#x}, {dp:#x}): {ret:#x} {out}"

# Repeat one pair: must not add a second record.
call_hook(m, 0x9983, 0x451, 0x956F, 0x222)

st = state(m)
magic_, enabled, bypass, total, pair_count, pair_bypassed = st[:6]
pairs = st[6:30]
assert enabled == 1
assert bypass == len(unlocked) + 1, bypass
assert total == len(unlocked) + len(stock) + 1, total
silent_stock = [p for p in stock if 0x9983 in p or 0xA013 in p]
assert pair_count == len(unlocked) + len(silent_stock), pair_count
expected_keys = [(s << 16) | d for s, d in unlocked + silent_stock]
assert list(pairs[:pair_count]) == expected_keys, [hex(x) for x in pairs[:pair_count]]
assert pair_bypassed == (1 << len(unlocked)) - 1, hex(pair_bypassed)
assert st[30] == 0x451 and st[31] == 0x222

# 2b. GetInhInfo trace hooks: arguments pass through, queries are recorded with caller LR.
def call_inh(entry: int, args: list[int]) -> tuple[int, list[int]]:
    m.mem_write(OUT_BUF_ADDR, bytes(20))
    m.reg_write(A.UC_ARM_REG_SP, 0x2000F000)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    for r, v in zip((A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2), args):
        m.reg_write(r, v)
    m.emu_start(entry | 1, STOP, count=20000)
    assert m.reg_read(A.UC_ARM_REG_SP) == 0x2000F000, "stack not balanced"
    return m.reg_read(A.UC_ARM_REG_R0), list(struct.unpack("<5I", m.mem_read(OUT_BUF_ADDR, 20)))


ret, out = call_inh(0x0411532C, [0x0183, 0x2000A000, 2])
assert ret == 0x42 and out[:4] == [0x0183, 0x2000A000, 0x000128DC, 2], (hex(ret), [hex(x) for x in out])
ret, out = call_inh(0x041153A4, [0x0769, 0x2000B000])
assert ret == 0x43 and out[:3] == [0x0769, 0x2000B000, 0x0001287E], (hex(ret), [hex(x) for x in out])
call_inh(0x0411532C, [0x0183, 0x2000A000, 2])
qb = bytes(m.mem_read(BASE + len(code) + 2076, 3080))
q_count, q_over = struct.unpack_from("<2I", qb)
q_key = struct.unpack_from("<256I", qb, 8)
q_lr = struct.unpack_from("<256I", qb, 8 + 1024)
q_hits = struct.unpack_from("<256I", qb, 8 + 2048)
assert q_count == 2 and q_over == 0, (q_count, q_over)
assert q_key[:2] == ((2 << 16) | 0x0183, (0xFFFF << 16) | 0x0769), [hex(x) for x in q_key[:2]]
assert q_lr[:2] == (STOP | 1, STOP | 1) and q_hits[:2] == (2, 1), (q_lr[:2], q_hits[:2])

# 2c. ParamDB::Set trace hook: args pass through; only strobe / silent params are recorded.
PARAM_OBJ = 0x2000C000
m.mem_write(PARAM_OBJ, struct.pack("<4I", 0x11111111, 7, 0xAB, 0x44444444))
ret, out = call_inh(0x06729D28, [0x12340000, 0x9571, PARAM_OBJ])
assert ret == 0x44 and out[:3] == [0x12340000, 0x9571, PARAM_OBJ], (hex(ret), [hex(x) for x in out])
call_inh(0x06729D28, [0x12340000, 0x951E, PARAM_OBJ])   # ISO: not traced
call_inh(0x06729D28, [0x12340000, 0x9983, PARAM_OBJ])
pb = bytes(m.mem_read(BASE + len(code) + 5156, 4 + 28 * 64))
p_seq = struct.unpack_from("<I", pb)[0]
r0 = struct.unpack_from("<7I", pb, 4)
r1 = struct.unpack_from("<7I", pb, 4 + 28)
assert p_seq == 2, p_seq
assert r0 == (0, 0x9571, STOP | 1, 0x11111111, 7, 0xAB, 0x44444444), [hex(x) for x in r0]
assert r1[:3] == (1, 0x9983, STOP | 1), [hex(x) for x in r1]


# 2d. STROBE_MODE_ACT override in ParamDB::Set (v7/v8): OFF becomes the user's mode only while
# Silent Shooting is ON, the flash is raised (ParamDB) and the user selected a flash mode (ParamDB).
def set_db(param: int, value: int) -> None:
    obj = DB_PARAMS + 16 * (param - 0x9500)
    m.mem_write(obj, struct.pack("<4I", value, 0x0413767C, 0, 0x11))
    m.mem_write(DB_ARRAY + 4 * (param - 0x9500), struct.pack("<I", obj))


def pset(param: int, value: int) -> int:
    m.mem_write(PARAM_OBJ, struct.pack("<4I", value, 0x0413767C, 0, 0x0413767C))
    call_inh(0x06729D28, [0x12340000, param, PARAM_OBJ])
    return struct.unpack("<I", m.mem_read(PARAM_OBJ, 4))[0]


def counters() -> tuple:
    return struct.unpack("<6I", m.mem_read(BASE + len(code) + 6952, 24))


set_db(0x956F, 0xAA)          # user: Fill-flash
set_db(0x95FC, 0)             # flash down
pset(0x9985, 0x451)           # Silent ON (ACT) in the message
assert pset(0x9571, 0xAB) == 0xAB, "override with flash down"
set_db(0x95FC, 1)             # flash raised
assert pset(0x9571, 0xAB) == 0xAA, "OFF not replaced by user mode"
assert pset(0x9571, 0xAD) == 0xAD, "non-OFF ACT modified"
pset(0x9985, 0x450)           # Silent OFF
assert pset(0x9571, 0xAB) == 0xAB, "override while Silent OFF"
pset(0x9985, 0x451)
set_db(0x956F, 0xAB)          # user: Flash Off
assert pset(0x9571, 0xAB) == 0xAB, "override although user chose Flash Off"
assert counters()[3] == 1, counters()

# 2e. v8 StillRec model message getter (+0x6e0d1c): same rule; Silent state from the message,
# falling back to ParamDB when the message does not carry SILENT_ACT.
MSG = 0x20026000
OUTV = 0x20026100


def mget(param: int, strobe: int, silent: int, silent_present: int, a5: int = 1) -> tuple[int, int]:
    m.mem_write(MSG, struct.pack("<3I", strobe, silent, silent_present))
    m.mem_write(OUTV, struct.pack("<I", 0xDEADBEEF))
    sp = 0x2000F000
    m.mem_write(sp, struct.pack("<I", a5))
    m.reg_write(A.UC_ARM_REG_SP, sp)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    for r, v in zip((A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3),
                    (0x55550000, MSG, param, OUTV)):
        m.reg_write(r, v)
    m.emu_start(0x063F8D1C | 1, STOP, count=20000)
    assert m.reg_read(A.UC_ARM_REG_SP) == sp, "stack not balanced"
    return m.reg_read(A.UC_ARM_REG_R0), struct.unpack("<I", m.mem_read(OUTV, 4))[0]


set_db(0x956F, 0xAA)
set_db(0x95FC, 1)
assert mget(0x9571, 0xAB, 0x451, 1) == (0, 0xAA), "model getter not overridden"
assert mget(0x9571, 0xAB, 0x451, 1, a5=0) == (0, 0xAA), "a5 == 0 path"
assert mget(0x9571, 0xAB, 0x450, 1) == (0, 0xAB), "override while message says Silent OFF"
set_db(0x9985, 0x451)
assert mget(0x9571, 0xAB, 0, 0) == (0, 0xAA), "ParamDB fallback for SILENT_ACT"
set_db(0x9985, 0x450)
assert mget(0x9571, 0xAB, 0, 0) == (0, 0xAB), "fallback says Silent OFF"
assert mget(0x9571, 0xAC, 0x451, 1) == (0, 0xAC), "non-OFF value modified"
assert mget(0x9985, 0, 0x451, 1) == (0, 0x451), "other param modified"
assert mget(0x9999, 0, 0, 0)[0] == 1, "missing param must stay not-found"
set_db(0x95FC, 0)
assert mget(0x9571, 0xAB, 0x451, 1) == (0, 0xAB), "override with flash down"
assert counters()[5] == 3, counters()

# 3. OFF restores fall-through.
call_life(m, off_off)
assert f"silent_flash on=0 bypass=0x{bypass:08x} total=0x{total:08x} pairs=0x{pair_count:08x} inh=0x00000000" in logs[-1], logs[-1]
assert bytes(m.mem_read(INH_REF_TBL_VA, INH_TBL_LEN)) == ORIG_INH_TBL, "DefInh table not restored byte-for-byte"
assert bytes(m.mem_read(DEVMEM_VA + AVCAM_OFF, 10)) == AVCAM_CTX_STOCK, "av-cam branch not restored"
assert bytes(m.mem_read(DEVMEM_VA + MASK_OFF, 10)) == MASK_STOCK, "av-cam mask not restored"
assert logs[-1].rstrip().endswith("avcam=ww"), logs[-1]
# Foreign content: neither ON nor OFF may write.
m.mem_write(DEVMEM_VA + AVCAM_OFF, bytes.fromhex("e4f49ffd10b1032300f8"))   # context mismatch
call_life(m, on_off)
assert bytes(m.mem_read(DEVMEM_VA + AVCAM_OFF, 10)) == bytes.fromhex("e4f49ffd10b1032300f8") and logs[-1].rstrip().endswith("avcam=xw"), logs[-1]
assert bytes(m.mem_read(DEVMEM_VA + MASK_OFF, 10)) == MASK_PATCH
call_life(m, off_off)
assert bytes(m.mem_read(DEVMEM_VA + AVCAM_OFF, 10)) == bytes.fromhex("e4f49ffd10b1032300f8") and logs[-1].rstrip().endswith("avcam=xw"), logs[-1]
assert bytes(m.mem_read(DEVMEM_VA + MASK_OFF, 10)) == MASK_STOCK
# Already patched: ON reports 'a' without writing.
m.mem_write(DEVMEM_VA + AVCAM_OFF, AVCAM_CTX_PATCH)
m.mem_write(DEVMEM_VA + MASK_OFF, MASK_PATCH)
call_life(m, on_off)
assert logs[-1].rstrip().endswith("avcam=aa"), logs[-1]
call_life(m, off_off)
assert bytes(m.mem_read(DEVMEM_VA + AVCAM_OFF, 10)) == AVCAM_CTX_STOCK and bytes(m.mem_read(DEVMEM_VA + MASK_OFF, 10)) == MASK_STOCK
assert set(devmem_maps) == {AVCAM_PA >> 12} and len(devmem_maps) == 6, devmem_maps
ret, out = call_hook(m, 0x9983, 0x451, 0x956F, 0x333)
assert ret == 0x3A and out == [SELF_PTR, 0xC0B6, 0x9983, 0x956F, 0x333], (hex(ret), out)

print(
    f"UNICORN SILENT_FLASH_UNLOCK PASS: {len(unlocked)} unlocked, {len(stock)} stock-preserved, "
    f"pairs={pair_count}, logs={len(logs)}"
)
