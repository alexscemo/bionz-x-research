# E0735 — `tests/`: Support Direct `unittest` Module Invocation for Helper Test Imports

```text
ID: E0735
Date: 2026-11-26
Claim: Updating 9 test files (`test_boot_event_trace.py`, `test_clone_return.py`, `test_dependency_plan.py`, `test_guest_madvise.py`, `test_guest_stream.py`, `test_syscall_record_cap.py`, `test_uipc_service_guard.py`, `test_uipc_sync_direct_yield.py`, `test_uipc_timer_service.py`) to import helper symbols from `tests.<module>` with an `exc.name`-guarded fallback allows both `python -m unittest tests.<module>` and `discover -s tests` without masking transitive `ModuleNotFoundError`s.
Status: verified-offline (59/59 direct tests PASS, `tools.research.ci_check` PASS)
```

## Verification Commands & Results

1. **Direct module invocation**
   - `.venv/bin/python -m unittest tests.test_guest_madvise tests.test_syscall_record_cap tests.test_dependency_plan tests.test_clone_return tests.test_uipc_timer_service tests.test_uipc_sync_direct_yield tests.test_guest_stream tests.test_boot_event_trace tests.test_uipc_service_guard.UipcServiceGuardTests.test_blocked_receive_retries_svc_after_sender_wakes_it -q` -> `Ran 59 tests in 0.618s OK`
2. **Repository & guardrail checks**
   - `.venv/bin/python -m tools.research.ci_check` -> `PASS`

## Remaining Limitations

- Test-import hygiene fix only; no hardware or QEMU behavior changed.
