#!/usr/bin/env python3
"""Research aid: log every register access of QEMU 10.0.0's pl330 model (idempotent).

The camera's dma330.ko programs the DMA controller; to model it we need the exact register
sequence (E9202 section 4d). The log goes to the -d unimp log as "pl330-reg: ...".

usage: patch_pl330_log.py <qemu-source-root>
The patched file stays under QEMU's GPL-2.0-or-later; only this patch script is in the repository.
"""
import sys

MARK = "pl330-reg:"
EDITS = [
    (
        "    trace_pl330_iomem_write((unsigned)offset, (unsigned)value);",
        "    trace_pl330_iomem_write((unsigned)offset, (unsigned)value);\n"
        '    qemu_log_mask(LOG_UNIMP, "pl330-reg: write +0x%x <- 0x%x\\n", (unsigned)offset,\n'
        "                  (unsigned)value);",
    ),
    (
        "    trace_pl330_iomem_read((uint32_t)offset, ret);",
        "    trace_pl330_iomem_read((uint32_t)offset, ret);\n"
        "    if (offset < 0xFE0 && !(offset == 0x28 && ret == 0)) {\n"
        '        qemu_log_mask(LOG_UNIMP, "pl330-reg: read  +0x%x -> 0x%x\\n", (unsigned)offset,\n'
        "                      (unsigned)ret);\n"
        "    }",
    ),
    (
        "    trace_pl330_fault(ch, flags);",
        "    trace_pl330_fault(ch, flags);\n"
        '    qemu_log_mask(LOG_UNIMP, "pl330-reg: FAULT flags 0x%x pc 0x%x\\n", (unsigned)flags,\n'
        "                  (unsigned)ch->pc);",
    ),
    (
        "    insn->exec(ch, buf[0], &buf[1], insn->size - 1);",
        '    qemu_log_mask(LOG_UNIMP, "pl330-reg: exec pc 0x%x op 0x%02x\\n", (unsigned)ch->pc,\n'
        "                  (unsigned)buf[0]);\n"
        "    insn->exec(ch, buf[0], &buf[1], insn->size - 1);",
    ),
]


def main():
    path = sys.argv[1] + "/hw/dma/pl330.c"
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if MARK in text:
        print(path + " already patched")
        return
    for old, new in EDITS:
        if text.count(old) != 1:
            sys.exit("unexpected layout, not patched: " + old.strip())
        text = text.replace(old, new, 1)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(path + " patched")


if __name__ == "__main__":
    main()
