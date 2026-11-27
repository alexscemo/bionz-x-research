#!/usr/bin/env python3
"""Small compatibility patches to QEMU 10.0.0's eMMC/SDHCI model (idempotent).

1. "ext-csd-rev" property on the eMMC card: the camera's emmc.ko accepts EXT_CSD revisions
   5..8 only, QEMU hard-codes 3 (default 0 keeps QEMU's value).
2. CMD1 of the camera driver sends the voltage window 0x80 with the sector-addressing bit,
   which QEMU's eMMC model rejects.
3. SDHCI command issue should need only the SD clock enable bit.
Items 2 and 3 were found independently in this repository's a6400 QEMU patch
(qemu-a6400/patches); they are re-implemented here against 10.0.0, not copied.

usage: patch_sd_emmc.py <qemu-source-root>
The patched files stay under QEMU's GPL-2.0-or-later; only this patch script is in the repository.
"""
import sys

NL = "\n"


def patch(path, edits):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    changed = False
    for old, new in edits:
        if new in text:
            continue
        if text.count(old) != 1:
            sys.exit("unexpected layout, not patched: " + old.strip())
        text = text.replace(old, new, 1)
        changed = True
    if changed:
        with open(path, "w", encoding="utf-8", newline=NL) as handle:
            handle.write(text)
        print(path + " patched")
    else:
        print(path + " already patched")


def main():
    root = sys.argv[1].rstrip("/")
    patch(root + "/hw/sd/sd.c", (
        ("    uint8_t boot_config;" + NL,
         "    uint8_t boot_config;" + NL + "    uint8_t ext_csd_rev;" + NL),
        ("sd->ext_csd[EXT_CSD_REV] = 3;",
         "sd->ext_csd[EXT_CSD_REV] = sd->ext_csd_rev ? sd->ext_csd_rev : 3;"),
        ('    DEFINE_PROP_UINT8("boot-config", SDState, boot_config, 0x0),' + NL,
         '    DEFINE_PROP_UINT8("boot-config", SDState, boot_config, 0x0),' + NL +
         '    DEFINE_PROP_UINT8("ext-csd-rev", SDState, ext_csd_rev, 0),' + NL),
        ("    if (FIELD_EX32(sd->ocr & req.arg, OCR, VDD_VOLTAGE_WINDOW)) {" + NL,
         "    if (sd_is_emmc(sd) && FIELD_EX32(req.arg, OCR, CARD_CAPACITY)) {" + NL +
         "        sd->ocr = FIELD_DP32(sd->ocr, OCR, CARD_CAPACITY, 1);" + NL +
         "    }" + NL + NL +
         "    if (FIELD_EX32(sd->ocr & req.arg, OCR, VDD_VOLTAGE_WINDOW) ||" + NL +
         "        (sd_is_emmc(sd) && (req.arg & 0x80))) {" + NL),
    ))
    patch(root + "/hw/sd/sdhci.c", (
        ("    if (!SDHC_CLOCK_IS_ON(s->clkcon) ||" + NL,
         "    if (!(s->clkcon & SDHC_CLOCK_SDCLK_EN) ||" + NL),
        # The Sony driver writes ADMA2-64 entries at a 16-byte stride (12-byte
        # descriptor + 4 bytes padding); QEMU's stock 12-byte stride reads the
        # padding as the next descriptor's attribute byte (E9202 4e).
        ("        dscr->incr = 12;" + NL,
         "        dscr->incr = s->adma64_16byte ? 16 : 12;" + NL),
        ('    DEFINE_PROP_BOOL("wp-inverted", SDHCIState,' + NL +
         "                     wp_inverted, false)," + NL,
         '    DEFINE_PROP_BOOL("wp-inverted", SDHCIState,' + NL +
         "                     wp_inverted, false)," + NL +
         '    DEFINE_PROP_BOOL("adma64-16byte", SDHCIState,' + NL +
         "                     adma64_16byte, false)," + NL),
    ))
    patch(root + "/include/hw/sd/sdhci.h", (
        ("    uint8_t vendor;        /* For vendor specific functionality */" + NL,
         "    uint8_t vendor;        /* For vendor specific functionality */" + NL +
         "    bool adma64_16byte;    /* Sony CXD90057: 16-byte ADMA2-64 stride */" + NL),
    ))


if __name__ == "__main__":
    main()
