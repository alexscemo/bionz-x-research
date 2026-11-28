#!/usr/bin/env python3
"""L0122 static check: four write-only pages, ZERO-only rule windows.

L0120 (F-0120, refuted as R-0003) mapped six unmapped pages.  L0122 keeps
only the four pages whose baseline pairs are all writes, and leaves the two
read pages 0xF2932000 and 0xF35D0000 unmodelled.  Each kept page gets one
per-offset ZERO-only window, cxd90045.<name> (size 0x1000, priority -600,
same helper as L0118):

  0xF2931000  +0x100 +0x104 +0x12C +0x130
  0xF3203000  +0x384 +0x904
  0xF3212000  +0x05C +0x068 +0x06C
  0xF3215000  +0xB00

Every row is CXD90045_F292_ZERO (no RF row), and no new window may overlap
any other cxd90045_f292_create() window in sony_a6400.c.

Static checks only (no QEMU boot):
  S.rows_per_window        each table holds exactly the listed offsets
  S.all_rows_ZERO          no CXD90045_F292_RF row in any L0122 table
  S.windows_created_once   one create call per base, size 0x1000, own table
  S.no_read_page_tables    no f2932 or f35d rule table or create call exists
  S.no_overlap_existing    the four bases do not overlap any other f292 window
  S.baseline_pairs         the 10 write pairs match loop/oracle/BASELINE.json
  S.pairs_covered          each of the 10 write pairs hits one window row

Run from the repository root:
  python3 -m unittest emulators/qemu-a6400/tests/test_qemu_l0122_zero_windows.py -q
"""
import json
import re
import unittest
from pathlib import Path

SOURCE = Path("emulators/qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c")
# loop/oracle is not checked out in a worktree; read the repository copy.
BASELINE = next(
    (p for p in (Path("loop/oracle/BASELINE.json"),
                 Path(__file__).resolve().parents[6] / "loop/oracle/BASELINE.json")
     if p.exists()),
    Path("loop/oracle/BASELINE.json"))
# The baseline L0122 was scored against (unmodelled 82), before promotion.
SCORED_BASELINE = next(
    (p for p in (Path("loop/oracle/history/BASELINE-20261125T102522Z-before-L0122-r4.json"),
                 Path(__file__).resolve().parents[6]
                 / "loop/oracle/history/BASELINE-20261125T102522Z-before-L0122-r4.json")
     if p.exists()),
    Path("loop/oracle/history/BASELINE-20261125T102522Z-before-L0122-r4.json"))

WINDOWS = {
    "f2931": (0xF2931000, [0x100, 0x104, 0x12C, 0x130]),
    "f3203": (0xF3203000, [0x384, 0x904]),
    "f3212": (0xF3212000, [0x05C, 0x068, 0x06C]),
    "f3215": (0xF3215000, [0xB00]),
}
READ_PAGE_BASES = {0xF2932000, 0xF35D0000}
PAGE = 0x1000

# The 10 baseline write pairs this lead retires: (kind, physical address).
BASELINE_PAIRS = [
    ("write", 0xF2931100), ("write", 0xF2931104),
    ("write", 0xF293112C), ("write", 0xF2931130),
    ("write", 0xF3203384), ("write", 0xF3203904),
    ("write", 0xF321205C), ("write", 0xF3212068),
    ("write", 0xF321206C), ("write", 0xF3215B00),
]


def table_rows(name):
    text = SOURCE.read_text(errors="replace")
    start = text.index(f"static const Cxd90045F292Rule cxd90045_{name}_rules[]")
    body = text[start:text.index("};", start)]
    rows = re.findall(r"\{\s*(0x[0-9a-fA-F]+),\s*(CXD90045_F292_\w+),", body)
    return [(int(off, 16), kind) for off, kind in rows]


def all_create_calls():
    text = SOURCE.read_text(errors="replace")
    pattern = (r"cxd90045_f292_create\(sysmem,\s*(0x[0-9A-Fa-f]+)(?:ULL)?,\s*"
               r"(0x[0-9A-Fa-f]+)(?:ULL)?,\s*\"(cxd90045\.\w+)\",\s*"
               r"(cxd90045_\w+_rules),\s*ARRAY_SIZE\(\w+\)\)")
    return [(int(b, 16), int(s, 16), name, table)
            for b, s, name, table in re.findall(pattern, text)]


class L0122StaticTest(unittest.TestCase):
    def test_rows_per_window(self):
        for name, (_, offs) in WINDOWS.items():
            with self.subTest(window=name):
                got = sorted(off for off, _ in table_rows(name))
                self.assertEqual(got, sorted(offs))

    def test_all_rows_ZERO(self):
        for name in WINDOWS:
            with self.subTest(window=name):
                kinds = [k for _, k in table_rows(name)]
                self.assertNotIn("CXD90045_F292_RF", kinds)
                self.assertEqual(kinds.count("CXD90045_F292_ZERO"),
                                 len(WINDOWS[name][1]))

    def test_windows_created_once(self):
        calls = all_create_calls()
        for name, (base, _) in WINDOWS.items():
            with self.subTest(window=name):
                matches = [c for c in calls if c[0] == base]
                self.assertEqual(len(matches), 1)
                _, size, cname, table = matches[0]
                self.assertEqual(size, PAGE)
                self.assertEqual(cname, f"cxd90045.{name}")
                self.assertEqual(table, f"cxd90045_{name}_rules")

    def test_no_read_page_tables(self):
        text = SOURCE.read_text(errors="replace")
        self.assertNotIn("cxd90045_f2932_rules", text)
        self.assertNotIn("cxd90045_f35d_rules", text)
        calls = all_create_calls()
        for base in READ_PAGE_BASES:
            with self.subTest(base=hex(base)):
                self.assertEqual([c for c in calls if c[0] == base], [])

    def test_no_overlap_existing(self):
        calls = all_create_calls()
        for name, (base, _) in WINDOWS.items():
            for other_base, other_size, other_name, _ in calls:
                if other_base == base and other_name == f"cxd90045.{name}":
                    continue
                with self.subTest(window=name, other=other_name):
                    self.assertTrue(base + PAGE <= other_base
                                    or other_base + other_size <= base,
                                    f"{hex(base)} overlaps {other_name}")

    def test_baseline_pairs(self):
        if not SCORED_BASELINE.exists():
            self.skipTest("loop/oracle history not available (upstream checkout)")
        top = json.loads(SCORED_BASELINE.read_text())["measure"]["unhandled"][
            "unmodelled_top"]
        pairs = []
        for entry in top:
            # BASELINE lists catch-all offsets; PA = 0xF0000000 + offset.
            kind, off = entry.split()
            pairs.append((kind, 0xF0000000 + int(off, 16)))
        for kind, addr in BASELINE_PAIRS:
            with self.subTest(kind=kind, addr=hex(addr)):
                self.assertIn((kind, addr), pairs)
        self.assertEqual(len(BASELINE_PAIRS), 10)

    def test_pairs_retired_after_promotion(self):
        if not BASELINE.exists():
            self.skipTest("loop/oracle history not available (upstream checkout)")
        baseline = json.loads(BASELINE.read_text())
        if "L0122-r4" not in baseline.get("accepted", []):
            self.skipTest("L0122 not promoted in this baseline")
        top = baseline["measure"]["unhandled"]["unmodelled_top"]
        pairs = []
        for entry in top:
            kind, off = entry.split()
            pairs.append((kind, 0xF0000000 + int(off, 16)))
        for kind, addr in BASELINE_PAIRS:
            with self.subTest(kind=kind, addr=hex(addr)):
                self.assertNotIn((kind, addr), pairs)

    def test_pairs_covered_by_windows(self):
        for kind, addr in BASELINE_PAIRS:
            with self.subTest(kind=kind, addr=hex(addr)):
                self.assertEqual(kind, "write")
                owner = [n for n, (b, _) in WINDOWS.items()
                         if b <= addr < b + PAGE]
                self.assertEqual(len(owner), 1)
                name = owner[0]
                off = addr - WINDOWS[name][0]
                self.assertIn(off, WINDOWS[name][1])


if __name__ == "__main__":
    unittest.main()
