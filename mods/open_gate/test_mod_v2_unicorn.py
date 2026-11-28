"""Unicorn verification harness for OpenGate32.mod and OpenGateSensor.mod.

Usage:
    python -m mods.open_gate.test_mod_v2_unicorn [BUILD_DIR_6400] [BUILD_DIR_6400A]
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from project_paths import PROJECT_ROOT  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args:
        for raw_dir in args:
            bdir = Path(raw_dir)
            for fname, exp_sub in (("OpenGate32.mod", 4), ("OpenGateSensor.mod", 3)):
                p = bdir / fname
                blob = p.read_bytes()
                magic, ver, flags, raw_name, code_sz, _fnv, bss, on_off, off_off, hooks = (
                    struct.unpack_from("<III32s6I", blob, 0)
                )
                assert magic == 0x444F4D53 and ver == 2, f"Invalid SMOD header in {p}"
                assert (flags & 0x20) != 0 and hooks == 0 and bss == 64, f"Unexpected flags/bss in {p}"
                assert on_off & 1 and off_off & 1, f"Non-Thumb lifecycle offsets in {p}"
                sm_magic, sm_cnt = struct.unpack_from("<IB", blob, 68 + code_sz)
                assert sm_magic == 0x554E4D53 and sm_cnt == exp_sub, f"Invalid SMNU trailer in {p}"
                print(
                    f"VERIFIED HEADER {p}: name={raw_name.rstrip(b'\x00').decode()} "
                    f"code={code_sz}B submenu={sm_cnt} total={len(blob)}B"
                )

    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_open_gate_32_mod")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        return 1
    print(f"UNICORN OPEN_GATE_32 SUITE PASS ({PROJECT_ROOT / 'mods/open_gate'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
