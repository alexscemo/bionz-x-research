#!/usr/bin/env python3
"""Research aid: log the ADMA descriptors and the writes into the cmmc window (idempotent).

The encrypted read of nflasha7 moves data from the eMMC controller into the crypt-emmc window
at 0xF8000000 and then through a DMA330 channel into RAM (E9202 4d). The open question is
whether the ADMA target really is that window and what the window holds immediately after the
controller wrote it, so this patch logs both to the -d unimp log as "sdhci-adma: ...".
It also dumps 16 raw bytes per descriptor entry, because the Sony driver writes 12-byte
ADMA2-64 entries and a stride mismatch is otherwise hard to see (E9202 4e).

usage: patch_sdhci_adma_log.py <qemu-source-root>
The patched file stays under QEMU's GPL-2.0-or-later; only this patch script is in the repository.
"""
import sys

EDITS = [
    (
        "        get_adma_description(s, &dscr);\n"
        "        trace_sdhci_adma_loop(dscr.addr, dscr.length, dscr.attr);",
        "        get_adma_description(s, &dscr);\n"
        "        {\n"
        "            uint8_t raw[16];\n"
        "            int i;\n"
        "\n"
        "            dma_memory_read(s->dma_as, (hwaddr)s->admasysaddr, raw, sizeof(raw),\n"
        "                            MEMTXATTRS_UNSPECIFIED);\n"
        '            qemu_log_mask(LOG_UNIMP, "sdhci-adma: raw @0x%" HWADDR_PRIx " :",\n'
        "                          (hwaddr)s->admasysaddr);\n"
        "            for (i = 0; i < 16; i++) {\n"
        '                qemu_log_mask(LOG_UNIMP, " %02x", raw[i]);\n'
        "            }\n"
        '            qemu_log_mask(LOG_UNIMP, "\\n");\n'
        "        }\n"
        "        trace_sdhci_adma_loop(dscr.addr, dscr.length, dscr.attr);",
    ),
    (
        "        trace_sdhci_adma_loop(dscr.addr, dscr.length, dscr.attr);",
        "        trace_sdhci_adma_loop(dscr.addr, dscr.length, dscr.attr);\n"
        '        qemu_log_mask(LOG_UNIMP, "sdhci-adma: desc sys 0x%" HWADDR_PRIx\n'
        '                      " addr 0x%" HWADDR_PRIx " len %u attr 0x%02x\\n",\n'
        "                      (hwaddr)s->admasysaddr, dscr.addr, dscr.length, dscr.attr);",
    ),
    (
        "                    res = dma_memory_write(s->dma_as, dscr.addr,\n"
        "                                           &s->fifo_buffer[begin],\n"
        "                                           s->data_count - begin,\n"
        "                                           attrs);",
        "                    res = dma_memory_write(s->dma_as, dscr.addr,\n"
        "                                           &s->fifo_buffer[begin],\n"
        "                                           s->data_count - begin,\n"
        "                                           attrs);\n"
        "                    if (dscr.addr == 0xF8000000ULL) {\n"
        "                        unsigned n = s->data_count - begin;\n"
        "                        uint32_t w0 = 0, w1 = 0;\n"
        "\n"
        "                        if (n >= 8) {\n"
        "                            memcpy(&w0, &s->fifo_buffer[begin], 4);\n"
        "                            memcpy(&w1, &s->fifo_buffer[begin + 4], 4);\n"
        "                            qemu_log_mask(LOG_UNIMP,\n"
        '                                          "sdhci-adma: window start len %u"\n'
        '                                          " w0 0x%08x w1 0x%08x\\n", n, w0, w1);\n'
        "                        }\n"
        "                    }",
    ),
]


def main():
    path = sys.argv[1] + "/hw/sd/sdhci.c"
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    changed = False
    for old, new in EDITS:
        if new in text:
            continue
        if text.count(old) != 1:
            sys.exit("unexpected layout, not patched: " + old.strip())
        text = text.replace(old, new, 1)
        changed = True
    if changed:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(path + " patched")
    else:
        print(path + " already patched")


if __name__ == "__main__":
    main()
