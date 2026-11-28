# E0733 — `test_qemu_clkrst_pmic_uhs_hdmi.py`: Gate ZMA1 Still-Capture QTest on Optional Firmware Sidecar

```text
ID: E0733
Date: 2026-11-26
Claim: Gating `test_zma_jpeg_decode_encode_rc0_vph_and_craw2_still_capture` on `sa_zima_startup_zma1.bin` at the start of the test causes clean `skipTest` when the proprietary firmware sidecar is absent, matching repository hardware/firmware-gating conventions without spawning QEMU or failing on clean checkouts.
Status: verified-offline (`tests.test_qemu_clkrst_pmic_uhs_hdmi` 5 OK / 1 skipped, `tools.research.ci_check` PASS)
```

## Verification Commands & Results

1. **ZMA1 qtest & module suite**
   - `.venv/bin/python -m unittest tests.test_qemu_clkrst_pmic_uhs_hdmi -v` -> `Ran 6 tests in 57.183s OK (skipped=1)`
2. **Repository & guardrail checks**
   - `.venv/bin/python -m tools.research.ci_check` -> `PASS`

## Remaining Limitations

- When `sa_zima_startup_zma1.bin` is absent, ZMA1/RC0/VPH/cRAW2 qtest execution is skipped rather than validated.
