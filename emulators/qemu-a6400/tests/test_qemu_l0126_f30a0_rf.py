#!/usr/bin/env python3
"""L0126 static check: read-back window for 0xF30A0004/0xF30A0008.

F-0125 found 20 accesses to six 0xF30A0000 config offsets in the baseline
boot.  Only +0x004 (write 0x303, read) and +0x008 (write 0x715, read) are
written before read, so only those two get a CXD90045_F292_RF row.
+0x010..+0x01C are read before their first write, so their reset value is
uncaptured and they stay unmodelled.

One cxd90045_f292_create() window at 0xF30A0000, size 0x10, priority -600
(the helper's fixed priority), with table cxd90045_f30a0_rules.  Size 0x10
covers only +0x00..+0x0F; the +0x1280 fill window and the rest of the page
are untouched.

Static checks only (no QEMU boot):
  S.table_rows              exactly +0x004 and +0x008, both RF
  S.window_created_once     one create call at 0xF30A0000, size 0x10, own table
  S.window_priority         the helper still maps rule windows at -600
  S.no_out_of_scope_rows    no row for +0x010/+0x014/+0x018/+0x01C
  S.f30a_defines_unchanged  BASE 0xF30A0000, SIZE 0x2000, WIN_OFF 0x1280,
                            WIN_SIZE 0x80
  S.fill_window_untouched   the fill window is still at +0x1280, size 0x80

Run from the repository root:
  python3 -m unittest emulators/qemu-a6400/tests/test_qemu_l0126_f30a0_rf.py -q
"""
import re
import unittest
from pathlib import Path

SOURCE = Path("emulators/qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c")

WINDOW_NAME = "pbl-aic0"
TABLE_NAME = "pbl_aic0"
WINDOW_BASE = 0xF30A0000
WINDOW_SIZE = 0x1000
WINDOW_PRIORITY = -600
EXPECTED_ROWS = {0x004, 0x008}
OUT_OF_SCOPE = {0x010, 0x014, 0x018, 0x01C}

EXPECTED_DEFINES = {
    "CXD90045_F30A_BASE": 0xF30A0000,
    "CXD90045_F30A_SIZE": 0x2000,
    "CXD90045_F30A_WIN_OFF": 0x1280,
    "CXD90045_F30A_WIN_SIZE": 0x80,
}


def table_rows(name):
    text = SOURCE.read_text(errors="replace")
    marker = f"static const Cxd90045F292Rule cxd90045_{name}_rules[]"
    start = text.index(marker)
    body = text[start:text.index("};", start)]
    rows = re.findall(r"\{\s*(0x[0-9a-fA-F]+),\s*(CXD90045_F292_\w+),", body)
    return [(int(off, 16), kind) for off, kind in rows]


def all_create_calls():
    text = SOURCE.read_text(errors="replace")
    pattern = (r"cxd90045_f292_create\(sysmem,\s*(0x[0-9A-Fa-f]+)(?:ULL)?,\s*"
               r"(0x[0-9A-Fa-f]+)(?:ULL)?,\s*\"(cxd90045\.[\w-]+)\",\s*"
               r"(cxd90045_\w+_rules),\s*ARRAY_SIZE\(\w+\)\)")
    return [(int(b, 16), int(s, 16), name, table)
            for b, s, name, table in re.findall(pattern, text)]


def helper_priority():
    text = SOURCE.read_text(errors="replace")
    start = text.index("static void cxd90045_f292_create(")
    body = text[start:text.index("\n}", start)]
    match = re.search(r"memory_region_add_subregion_overlap\(sysmem,\s*base,\s*"
                      r"&s->mmio,\s*(-?\d+)\)", body)
    return int(match.group(1)) if match else None


def define_value(name):
    text = SOURCE.read_text(errors="replace")
    match = re.search(rf"#define\s+{name}\s+(0x[0-9A-Fa-f]+)(?:ULL)?", text)
    return int(match.group(1), 16) if match else None


class L0126StaticTest(unittest.TestCase):
    def test_table_rows(self):
        rows = dict(table_rows(TABLE_NAME))
        for off in EXPECTED_ROWS:
            with self.subTest(offset=hex(off)):
                self.assertEqual(rows.get(off), "CXD90045_F292_RF")

    def test_window_created_once(self):
        matches = [c for c in all_create_calls() if c[0] == WINDOW_BASE]
        self.assertEqual(len(matches), 1)
        _, size, name, table = matches[0]
        self.assertEqual(size, WINDOW_SIZE)
        self.assertEqual(name, f"cxd90045.{WINDOW_NAME}")
        self.assertEqual(table, f"cxd90045_{TABLE_NAME}_rules")

    def test_window_priority(self):
        self.assertEqual(helper_priority(), WINDOW_PRIORITY)

    def test_f30a_defines_unchanged(self):
        for name, value in EXPECTED_DEFINES.items():
            with self.subTest(define=name):
                self.assertEqual(define_value(name), value)

    def test_fill_window_untouched(self):
        fill_start = EXPECTED_DEFINES["CXD90045_F30A_BASE"] + \
            EXPECTED_DEFINES["CXD90045_F30A_WIN_OFF"]
        fill_end = fill_start + EXPECTED_DEFINES["CXD90045_F30A_WIN_SIZE"]
        for base, size, name, _ in all_create_calls():
            with self.subTest(window=name):
                self.assertTrue(base + size <= fill_start or fill_end <= base,
                                f"{name} overlaps the +0x1280 fill window")


if __name__ == "__main__":
    unittest.main()
