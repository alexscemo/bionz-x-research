#!/usr/bin/env python3
"""L0188 static check: the BCM4339 EROM follows the AI EROM contract the guest
driver parses.

The model's `bcm4339_erom[]` table is read by bcmdhd.ko's `ai_scan` through
`get_erom_ent`/`get_asd`.  A component is CIA (tag ER_CI), CIB (tag ER_CI,
NSP > 0 and NMW+NSW > 0), then NMP master-port descriptors and the address
descriptors for the slave register space, master wrappers and slave wrappers.
The old table put a made-up CIB (`0x113`) directly after the CIA: the tag bits
were not ER_CI and NSP was zero, so the driver aborted the scan and `si_attach`
failed.

This test re-implements that walk in Python, reads the C table from the source,
and checks the parsed cores.  The last two checks are the negative control:
the recorded old entry is rejected, so this test fails on the old EROM.

Static checks only (no QEMU boot):
  S.current_table_parses   the four cores parse in order with their bases
  S.chipcommon_rev         ChipCommon is rev 4 (`BCM_CHIPCOMMON_ID` low byte)
  S.core_wrappers          every core has one 4 KiB master wrapper
  S.old_cib_rejected       the recorded CIB 0x113 aborts the scan
  S.old_table_rejected     the old four-word entry never yields a core

Run from the repository root:
  python3 -m unittest emulators/qemu-a6400/tests/test_qemu_l0188_bcm4339_attach.py -q
Set QEMU_BCM4339_SRC to check another copy of the model (negative control).
"""
import os
import re
import unittest
from pathlib import Path

SOURCE = Path(os.environ.get(
    "QEMU_BCM4339_SRC",
    "emulators/qemu-a6400/qemu-overlay/hw/misc/cxd90045_bcm4339.c"))

# aidmp.h / ai_scan contract (values are the ones in the guest driver).
ER_TAG = 0xE
ER_TAG1 = 0x6
ER_VALID = 0x1
ER_CI = 0x0
ER_MP = 0x2
ER_ADD = 0x4
ER_END = 0xE

AD_ST_SLAVE = 0x00
AD_ST_BRIDGE = 0x40
AD_ST_SWRAP = 0x80
AD_ST_MWRAP = 0xC0

CIA_MFG_MASK = 0xFFF00000
CIA_MFG_SHIFT = 20
CIA_CID_MASK = 0x000FFF00
CIA_CID_SHIFT = 8
CIB_REV_MASK = 0xFF000000
CIB_REV_SHIFT = 24
CIB_NSW_MASK = 0x00F80000
CIB_NSW_SHIFT = 19
CIB_NMW_MASK = 0x0007C000
CIB_NMW_SHIFT = 14
CIB_NSP_MASK = 0x00003E00
CIB_NSP_SHIFT = 9
CIB_NMP_MASK = 0x000001F0
CIB_NMP_SHIFT = 4

AD_ADDR_MASK = 0xFFFFF000
AD_SP_MASK = 0x00000F00
AD_SP_SHIFT = 8
AD_ST_MASK = 0x000000C0
AD_SZ_MASK = 0x00000030
AD_SZ_SZD = 0x00000030
AD_AG32 = 0x00000008

PACKAGE_COUP = (ER_END | ER_VALID)
# Not modelled here; kept so the filters match the driver's loop.
DEF_AI_COMP = 0x800
MFGID_ARM = 0x43B


class EromError(Exception):
    pass


def c_macro_ns():
    er_valid, er_ci, er_mp, er_add, er_end = ER_VALID, ER_CI, ER_MP, ER_ADD, ER_END

    return {
        "BCM_EROM_ER_VALID": er_valid,
        "BCM_EROM_ER_CI": er_ci,
        "BCM_EROM_ER_MP": er_mp,
        "BCM_EROM_ER_ADD": er_add,
        "BCM_EROM_ER_END": er_end,
        "BCM_EROM_MFG_BRCM": 0x4BF,
        "BCM_EROM_AD_ST_SLAVE": 0x0,
        "BCM_EROM_AD_ST_MWRAP": 0x3,
        "BCM_EROM_AD_SZ_4K": 0x0,
        "BCM4339_TCM_SIZE": 768 * 1024,
        "BCM_EROM_CIA": lambda mfg, cid: ((mfg << 20) | (cid << 8) | er_valid),
        "BCM_EROM_CIB": lambda rev, nmp, nsp, nmw, nsw: (
            (rev << 24) | (nsw << 19) | (nmw << 14) | (nsp << 9) |
            (nmp << 4) | er_valid),
        "BCM_EROM_MP": lambda: (er_valid | er_mp),
        "BCM_EROM_ASD": lambda addr, sp, st, sz: (
            (addr & AD_ADDR_MASK) | ((sp & 0xF) << 8) |
            ((st & 0x3) << 6) | ((sz & 0x3) << 4) | er_add | er_valid),
    }


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _split_top_level(body):
    parts, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def read_erom(path=SOURCE):
    text = _strip_comments(path.read_text(errors="replace"))
    marker = "static const uint32_t bcm4339_erom[] = {"
    start = text.index(marker) + len(marker)
    end = text.index("};", start)
    body = text[start:end]
    ns = c_macro_ns()
    words = []
    for expr in _split_top_level(body):
        # Drop C integer suffixes (0x...u, 0x...UL) so Python can evaluate.
        py = re.sub(r"(0x[0-9a-fA-F]+|\d+)[uUlL]+", r"\1", expr)
        words.append(int(eval(py, {"__builtins__": {}}, ns)) & 0xFFFFFFFF)
    return words


def get_erom_ent(erom, ptr, mask, match):
    while True:
        if ptr >= len(erom):
            raise EromError("end of table without END marker")
        ent = erom[ptr]
        ptr += 1
        if mask == 0:
            return ent, ptr
        if (ent & ER_VALID) == 0:
            continue
        if ent == (ER_END | ER_VALID):
            return ent, ptr
        if (ent & mask) == match:
            return ent, ptr


def get_asd(erom, ptr, sp, st):
    asd, after = get_erom_ent(erom, ptr, ER_VALID, ER_VALID)
    if ((asd & ER_TAG1) != ER_ADD or
            ((asd & AD_SP_MASK) >> AD_SP_SHIFT) != sp or
            (asd & AD_ST_MASK) != st):
        return None, ptr  # "push it back"
    if asd & AD_AG32:
        raise EromError("64-bit address descriptor")
    size = 0
    if (asd & AD_SZ_MASK) == AD_SZ_SZD:
        if after >= len(erom):
            raise EromError("missing size descriptor")
        szd = erom[after]
        after += 1
        size = szd & AD_ADDR_MASK
        if szd & AD_AG32:
            raise EromError("64-bit size descriptor")
    else:
        size = 0x1000 << ((asd & AD_SZ_MASK) >> 4)
    return {"addr": asd & AD_ADDR_MASK, "size": size}, after


def scan(erom):
    """Walk the table the way bcmdhd.ko ai_scan does."""
    ptr = 0
    cores = []
    while True:
        cia, ptr = get_erom_ent(erom, ptr, ER_TAG, ER_CI)
        if cia == (ER_END | ER_VALID):
            return cores
        cib, ptr = get_erom_ent(erom, ptr, 0, 0)
        if (cib & ER_TAG) != ER_CI:
            raise EromError("CIA not followed by CIB (tag bits)")
        mfg = (cia & CIA_MFG_MASK) >> CIA_MFG_SHIFT
        cid = (cia & CIA_CID_MASK) >> CIA_CID_SHIFT
        rev = (cib & CIB_REV_MASK) >> CIB_REV_SHIFT
        nsw = (cib & CIB_NSW_MASK) >> CIB_NSW_SHIFT
        nmw = (cib & CIB_NMW_MASK) >> CIB_NMW_SHIFT
        nsp = (cib & CIB_NSP_MASK) >> CIB_NSP_SHIFT
        nmp = (cib & CIB_NMP_MASK) >> CIB_NMP_SHIFT
        if nsp == 0:
            continue
        if mfg == MFGID_ARM and cid == DEF_AI_COMP:
            continue
        if nmw + nsw == 0:
            # Only the no-wrapper "component" ids survive this branch.
            continue
        for _ in range(nmp):
            mpd, ptr = get_erom_ent(erom, ptr, ER_VALID, ER_VALID)
            if (mpd & ER_TAG) != ER_MP:
                raise EromError("not enough MP entries")
        main, ptr = get_asd(erom, ptr, 0, AD_ST_SLAVE)
        if main is None:
            raise EromError("missing first slave address descriptor")
        # Extra address spaces in slave port 0.
        while True:
            extra, after = get_asd(erom, ptr, 0, AD_ST_SLAVE)
            if extra is None:
                break
            ptr = after
        # No other slave port is modelled in this table.
        if nsp != 1:
            raise EromError("multi-port component not modelled")
        wrap = None
        for i in range(nmw):
            mw, after = get_asd(erom, ptr, i, AD_ST_MWRAP)
            if mw is None:
                raise EromError("missing master wrapper %d" % i)
            ptr = after
            if i == 0:
                wrap = mw["addr"]
            if mw["size"] != 0x1000:
                raise EromError("master wrapper is not 4 KiB")
        fwp = 0 if nsp == 1 else 1
        for i in range(nsw):
            sw, after = get_asd(erom, ptr, fwp + i, AD_ST_SWRAP)
            if sw is None:
                raise EromError("missing slave wrapper %d" % i)
            ptr = after
            if nmw == 0 and i == 0:
                wrap = sw["addr"]
        cores.append({"mfg": mfg, "cid": cid, "rev": rev,
                      "base": main["addr"], "wrap": wrap})


class TestBcm4339Erom(unittest.TestCase):
    def test_current_table_parses(self):
        cores = scan(read_erom())
        self.assertEqual(
            [(c["cid"], c["base"], c["wrap"]) for c in cores],
            [(0x800, 0x18000000, 0x18100000),
             (0x829, 0x18001000, 0x18101000),
             (0x83E, 0x18002000, 0x18102000),
             (0x812, 0x18003000, 0x18103000)])

    def test_chipcommon_rev(self):
        self.assertEqual(scan(read_erom())[0]["rev"], 4)

    def test_core_wrappers(self):
        for core in scan(read_erom()):
            self.assertIsNotNone(core["wrap"])

    def test_old_cib_rejected(self):
        # The recorded old CIB: tag bits 0b10 (not ER_CI) and NSP = 0.
        self.assertNotEqual(0x113 & ER_TAG, ER_CI)
        self.assertEqual((0x113 & CIB_NSP_MASK) >> CIB_NSP_SHIFT, 0)

    def test_old_table_rejected(self):
        old = [
            (0x4BF << 20) | (0x800 << 8) | 0x01,
            0x113,
            0x18000005,
            0x18100045,
            (ER_END | ER_VALID),
        ]
        with self.assertRaises(EromError):
            scan(old)


if __name__ == "__main__":
    unittest.main()
