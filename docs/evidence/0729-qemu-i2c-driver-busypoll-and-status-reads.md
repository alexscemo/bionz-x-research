# E0729 — QEMU `cxd90045_i2c`: Enforce Busy-Bit Mask on `I2C_ABORT` and Error-Bit Mask on `I2C_STATUS`

```text
ID: E0729
Date: 2026-11-26
Claim: Disassembly of `/kmod/i2c.ko` establishes that `wait_reg_not_busy` (`+0x12b8`) polls bit 15 (`0x8000`) == 0 on `+0x20` (`I2C_ABORT`) as well as `+0x10`/`+0x14`, and `+0x15c4` reads `+0x00` (`I2C_STATUS`) and tests bit 5 (`0x20`) even when `complete_transfers` is false. Updating `cxd90045_i2c.c` clears bit 15 on `I2C_ABORT` reads and clears bit 5 on `I2C_STATUS` reads when `complete_transfers=off` (while consolidating the duplicate `I2C_T10`/`I2C_T14`/`I2C_STATUS` branches and adding qtest coverage).
Status: verified-offline (9/9 `tests.test_qemu_peripherals_and_models` PASS, `tools.research.ci_check` PASS)
```

## Firmware Contract (`/kmod/i2c.ko`)

- `i2c.ko +0x12b8` (`wait_reg_not_busy`): polls `ldrh` on `+0x10` (`I2C_T10`), `+0x14` (`I2C_T14`), and `+0x20` (`I2C_ABORT`) with `tst r3, #0x8000` until bit 15 is clear before writing. Previously `cxd90045_i2c.c` cleared bit 15 on `I2C_T10` and `I2C_T14`, but returned raw `i2c_store_read` on `I2C_ABORT`.
- `i2c.ko +0x15c4`: reads `+0x00` (`I2C_STATUS`) and tests bit 5 (`ands r2, r3, #0x20`) for bus/NACK error regardless of whether `complete_transfers` is enabled. Previously `cxd90045_i2c.c` only cleared bit 5 when `complete_transfers` was enabled.

## Verification Commands & Results

1. **QTest & peripheral regression suite**
   - Command: `.venv/bin/python -m unittest tests.test_qemu_peripherals_and_models -v`
   - Result: `Ran 9 tests in 2.369s OK` (includes `test_i2c_prewrite_busypoll_and_status_reads_without_complete_transfers`).
2. **Repository & guardrail checks**
   - Command: `.venv/bin/python -m tools.research.ci_check`
   - Result: `PASS` (`py_compile`, `test_discovery_guard`, `binary_and_size_guard`, `markdown_links`, `status_svg_sync`, `guardrail_tests`).

## Remaining Limitations

- Offline QEMU qtest verification only; no physical camera was accessed.
