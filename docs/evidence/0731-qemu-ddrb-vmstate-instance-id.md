# E0731 — QEMU `sony_a6400.c`: Use `VMSTATE_INSTANCE_ID_ANY` for `cxd90045-ddrb`

```text
ID: E0731
Date: 2026-11-26
Claim: Using `VMSTATE_INSTANCE_ID_ANY` in `cxd90045_create_ddrb_at()` prevents the duplicate `SaveStateEntry` startup abort (`id=cxd90045-ddrb, instance_id=0x0`) when `pmic-reset=on` registers both `cxd90045.ddra` and `cxd90045.ddrb`, while preserving `instance_id=0` for existing single-instance snapshots.
Status: verified-offline (`test_pmic_soc_warm_reset_and_sram_gpio_poweroff` PASS, 5/5 `tests.test_qemu_snapshot` PASS, `tools.research.ci_check` PASS)
```

## Verification Commands & Results

1. **PMIC reset & snapshot roundtrip suites**
   - `.venv/bin/python -m unittest tests.test_qemu_clkrst_pmic_uhs_hdmi.QemuPrerequisitesHardwareTests.test_pmic_soc_warm_reset_and_sram_gpio_poweroff -v` -> `Ran 1 test in 0.089s OK`
   - `.venv/bin/python -m unittest tests.test_qemu_snapshot -v` -> `Ran 5 tests in 1.691s OK`
2. **Repository & guardrail checks**
   - `.venv/bin/python -m tools.research.ci_check` -> `PASS`

## Remaining Limitations

- Offline QEMU qtest/snapshot verification only; no physical hardware was accessed.
