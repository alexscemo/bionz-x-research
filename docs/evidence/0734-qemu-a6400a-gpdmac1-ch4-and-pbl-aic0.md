# E0734 — QEMU `sony_a6400.c`: Model GP-DMAC1 Ch4 (`+0x18C/+0x190`) and PBL AIC0 `+0x00/+0x0C`

```text
ID: E0734
Date: 2026-11-26
Claim: Adding `CXD90045_F292_RF` rules for PBL AIC0 `+0x000`/`+0x00C` (`0xF30A0000`) and GP-DMAC Unit 1 Channel 4 (`+0x18C` CFG / `+0x190` CTRL with `BIT(18)` busy == 0 and self-clearing `BIT(0)` start at `0xF0103000`) retires 12 unmodelled MMIO pairs on live `ILCE-6400A` `--d-unimp` boot at `sequencer.step=32` (`13 -> 1`, and `0` with `CLKRST_MODEL=1 PERIPHERALS=1`).
Status: verified-offline (`test_e0705_remaining_mmio_windows_retire_cleanly` PASS, `tools.research.ci_check` PASS, live `sony-a6400a --d-unimp` boots verified)
```

## Verification Commands & Results

1. **QTest MMIO retirement**
   - `.venv/bin/python -m unittest tests.test_qemu_clkrst_pmic_uhs_hdmi.QemuPrerequisitesHardwareTests.test_e0705_remaining_mmio_windows_retire_cleanly -v` -> `Ran 1 test in 0.085s OK`
2. **Live `ILCE-6400A` (`sony-a6400a`) QEMU `--d-unimp` boots (`sequencer.step=32`)**
   - Default flags: `unmodelled.distinct_pairs` dropped from `13` to `1` (leaving only `f290 +0x2900030`).
   - With `CLKRST_MODEL=1 PERIPHERALS=1 --submicro-lens-power`: `unmodelled.distinct_pairs = 0`.
3. **Repository & guardrail checks**
   - `.venv/bin/python -m tools.research.ci_check` -> `PASS`

## Remaining Limitations

- Offline QEMU verification only; no physical `ILCE-6400A` camera was accessed.
