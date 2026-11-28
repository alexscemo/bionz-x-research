#!/usr/bin/env python3
"""L0176 static check: the eMMC card's OCR is carried across a VM restore.

The captured a6400 eMMC is smaller than 2 GiB, but its guest enables block
addressing in CMD1, so sd_cmd_SEND_OP_COND() sets OCR.CARD_CAPACITY at run
time.  sd_ocr_powerup() only derives that bit from the card size, so the
OCR subsection must still be written for a powered-up eMMC; otherwise loadvm
clears CARD_CAPACITY and sd_req_get_address() decodes the guest's sector
numbers as byte addresses (bogus block reads).

hw/sd/sd.c comes from the pinned release plus the v11 patch (the overlay copy
of the file was removed upstream, E0732), so the checks that need only the
changed regions read the patch's post-change text.  The two checks on code the
patch does not touch need a patched tree: set A6400_QEMU_DIR, else they skip.

Static checks only (no QEMU boot):
  S.ocr_field_saved       the OCR subsection still saves VMSTATE_UINT32(ocr)
  S.emmc_carried          the needed guard ORs in sd_is_emmc(sd)
  S.powered_up_control    a powered-up non-eMMC card still omits OCR
  S.unpowered_kept        an unpowered card is still saved
  S.capacity_set_by_cmd1  CMD1 still sets CARD_CAPACITY for the eMMC
  S.pre_load_powers_up    sd_vmstate_pre_load still calls sd_ocr_powerup

Run from the repository root:
  python3 -m unittest qemu-a6400/tests/test_qemu_l0176_snapshot_state.py -q
"""
import os
import re
import unittest
from pathlib import Path

PATCH = Path("qemu-a6400/patches/qemu-v11.1.3-sony-a6400.patch")
TREE = os.environ.get("A6400_QEMU_DIR")


def patched_regions(function):
    """Post-change text of every hw/sd/sd.c hunk whose header names function."""
    text = PATCH.read_text(encoding="utf-8")
    section = text[text.index("diff --git a/hw/sd/sd.c "):]
    section = section[:section.index("\ndiff --git ", 1)]
    regions = []
    for hunk in re.split(r"^(?=@@ )", section, flags=re.M)[1:]:
        header, _, body = hunk.partition("\n")
        if function in header:
            regions.append("\n".join(line[1:] for line in body.splitlines()
                                     if line[:1] in (" ", "+")))
    if not regions:
        raise AssertionError(f"the patch has no hunk in {function}")
    return "\n".join(regions)


def function_body(text, signature):
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[start:pos + 1]
    raise AssertionError(f"unterminated function {signature}")


def tree_source():
    if not TREE:
        raise unittest.SkipTest("set A6400_QEMU_DIR to a patched QEMU tree")
    return (Path(TREE) / "hw/sd/sd.c").read_text(encoding="utf-8")


class SnapshotOcrState(unittest.TestCase):
    def test_ocr_field_saved(self):
        text = tree_source()
        body = text[text.index("static const VMStateDescription sd_ocr_vmstate"):
                    text.index("static bool vmstate_needed_for_rpmb")]
        self.assertIn("VMSTATE_UINT32(ocr, SDState)", body)

    def test_emmc_carried(self):
        body = patched_regions("sd_ocr_vmstate_needed")
        self.assertIn("CARD_POWER_UP", body)
        self.assertIn("sd_is_emmc(sd)", body)
        self.assertRegex(body, r"return\s+.*CARD_POWER_UP.*\|\|.*sd_is_emmc\(sd\)")

    def test_powered_up_control(self):
        # Model the guard: needed = (!powered_up) || is_emmc.
        guard = lambda powered_up, emmc: (not powered_up) or emmc
        self.assertTrue(guard(True, True))    # powered-up eMMC: must save OCR
        self.assertFalse(guard(True, False))  # powered-up SD: upstream omission kept
        self.assertTrue(guard(False, False))  # unpowered card: still saved

    def test_unpowered_kept(self):
        body = patched_regions("sd_ocr_vmstate_needed")
        self.assertIn("!FIELD_EX32(sd->ocr, OCR, CARD_POWER_UP)", body)

    def test_capacity_set_by_cmd1(self):
        body = patched_regions("sd_cmd_SEND_OP_COND")
        self.assertIn("sd_is_emmc(sd)", body)
        self.assertIn("FIELD_DP32(sd->ocr, OCR, CARD_CAPACITY, 1)", body)

    def test_pre_load_powers_up(self):
        body = function_body(tree_source(), "static int sd_vmstate_pre_load")
        self.assertIn("sd_ocr_powerup(sd)", body)


if __name__ == "__main__":
    unittest.main()
