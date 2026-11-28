# E0730 — `grm_service.py`: Import Missing `Tuple` Annotation Used by `_wbi_pools`

```text
ID: E0730
Date: 2026-11-26
Claim: Importing `Tuple` from `typing` in `src/a6400/execution/grm_service.py` resolves the `NameError: name 'Tuple' is not defined` import failure on `GrmMaService._wbi_pools` (`line 258`), restoring `tests.test_grm_service` (46/46 OK) and `test_uipc_service_guard.py` (11/11 OK).
Status: verified-offline (46/46 `tests.test_grm_service` PASS, 11/11 `test_uipc_service_guard.py` PASS, `tools.research.ci_check` PASS)
```

## Verification Commands & Results

1. **GRM-MA & UIPC service guard suites**
   - `.venv/bin/python -m unittest tests.test_grm_service -v` -> `Ran 46 tests OK`
   - `.venv/bin/python -m unittest discover -s tests -p "test_uipc_service_guard.py" -v` -> `Ran 11 tests OK`
2. **Repository & guardrail checks**
   - `.venv/bin/python -m tools.research.ci_check` -> `PASS`

## Remaining Limitations

- Host-side execution contract test fix only; no physical hardware was accessed.
