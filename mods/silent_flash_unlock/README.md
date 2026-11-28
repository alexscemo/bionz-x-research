# SilentFlashUnlock (`SMOD v2`, experimental)

RAM-only **Mod Loader v2** module for **Sony ILCE-6400 (`2.00` captured layout)**. It hooks the `ExcMgr` exclusion evaluator in `libObj.so` (`+0x00c708a0`, ref VA `0x069888a0`) and returns "no exclusion" for pairs between Silent Shooting (`0x9983`, `0xa013`) and the user-settable strobe parameters (`0x956e..0x9571`, `0x95f7..0x95fc`, `0x95ff..0x9604`, `0x96f0..0x96f2`, `0x972a`, `0x972c..0x972f`, `0x978f..0x9791`, `0x9925..0x9927`, `0x9f62..0x9f64`), in both directions.

- **Same hook target as `SilentShutterUnlock`:** do not load both mods together.
- **No menu grey-out change:** the flash menu FeatureIds in `DefInh::sm_refTbl` have not been identified, so the flash menu may stay greyed while Silent Shooting is ON. Set the flash mode first, then turn Silent Shooting ON.
- **Unverified on hardware:** it is not known whether `av-cam.bin` fires the strobe during an electronic-shutter exposure. Sensor readout takes about 30–35 ms, so shutter speeds faster than about 1/15–1/25 s give partly lit frames with dark bands.
- **Diagnostics:** every distinct pair that involves Silent Shooting is recorded in `.bss` (`magic 'SFLU'`, counters, 24 pair slots, bypass bitmask). ON/OFF append a line to `/tmp_bt/mods/silent_flash_unlock.log`.

## Build and offline test

```powershell
wsl.exe --exec bash mods/silent_flash_unlock/build_v2_wsl.sh artifacts/tests/silent-flash-unlock-v1
.\venv\Scripts\python.exe mods/silent_flash_unlock/test_mod_v2_unicorn.py artifacts/tests/silent-flash-unlock-v1
```

Build v1: 988 B, SHA-256 `0a01c55cdb9c2659da85dd8cc99456b66058cb6041c0ae9972ae9e23edab35a8`. Unicorn: 8 unlocked pairs, 5 stock pairs, OFF falls through.

## Deployment (2026-11-27, live ILCE-6400 over TCP debugger)

The file was pushed to `/tmp_bt/mods/SilentFlashUnlock.mod` (`.part` + readback + rename; receipt `artifacts/tests/silent-flash-unlock-v1/deploy-receipt-*.json`). The hook target held the stock bytes `2de9f04104464cf2b60000af` before the push. The core loads the file, and the `--auto-on` flag switches it ON, at the next menu root setup.

## Live findings (2026-11-27, ILCE-6400 over TCP debugger, RAM-only)

Builds v2–v8 are in `artifacts/tests/silent-flash-unlock-v2-diag … v8` with push/reload receipts and `.bss` snapshots. Every build passed `test_mod_v2_unicorn.py` before it was pushed.

1. **ExcMgr pairs (v1/v2):** with the mod loaded after Silent Shooting was already ON, 534 ExcMgr calls carried no Silent param. After toggling, the Silent↔Strobe pairs (`0x9983`→`0x956f`, `0x9983`→`0x9f62`) were bypassed, but the flash stayed crossed out.
2. **Menu inhibition (v3/v4/v5b):** `MWF::InhInfo` marks feature F as inhibited when `DefInh::sm_refTbl[F] & mp_inhTbl` is non-zero (`libMWF +0x1e16c`, active-factor bitmap at `*(libMWF +0x41d30)`). The `GetInhInfo` trace showed the Flash Mode menu querying FeatureIds `0x180, 0x182, 0x183, 0x181, 0x184`. The v3 candidates were wrong. With Silent ON, only factor `0x0de2` of the five Silent factors is active. v4 clears the Silent factors on `0x180..0x187` (except `0x183`), `0x769` and `0x76b`, and after that Fill-flash can be selected. `0x181` and `0x183` stay inhibited by factor `0x945`, which is not a Silent factor.
3. **Actual strobe mode (v6/v7):** the `ParamDB::Set` trace (`libObj +0xa11d28`) shows that turning Silent ON delivers one message, applied by `ParamDB::ApplyMsg` (`+0xa11dc4`), with `SILENT_ACT=0x451`, `EXC_SILENT_ACT=0xc7b`, `STROBE_MODE_ACT=0xab` (`VALUEID_STROBE_MODE_OFF`) and `WIRELESS_ACT`, while `STROBE_MODE` keeps the user choice. v7 rewrites that OFF in ParamDB. The icon still showed flash off.
4. **Icon source (v8):** the view's `CmnViewModelWrpStlRecStrobe::getFlashMode()` asks the StillRec model, and the model reads `STROBE_MODE_ACT` from the message (`libObj +0x6e0d1c`), not from ParamDB. v8 corrects that read too. **Result: the icon is no longer crossed out, but the flash does not fire.**
5. **Lower layer refuses:** with Silent ON, each new `SET_STROBE_MODE` (Slow Sync, then Fill-flash) was confirmed with `STROBE_MODE_ACT=OFF` (v8 `act_overrides=3`). The strobe-off decision is made below libObj's ParamDB, in the layer that answers the camera commands (most likely `av-cam.bin`). The v8 overrides only change what libObj and the view see, so with v8 ON **the flash icon is misleading**.
6. `STROBE_CHARGE_STATUS` reads `0x14f` (charging) with Silent both ON and OFF; it is a stale value, not the cause.

Next boundary: find who produces the ACT reply message (caller of `ParamDB::ApplyMsg` for the `SET_SILENT_SHUTTER_MODE` / `SET_STROBE_MODE` completions) and, if it is `av-cam.bin`, analyze its electronic-shutter capture sequence offline. Patching `av-cam.bin` in RAM is outside Mod Loader v2 and the standing camera authorization.

## av-cam root cause (2026-11-27, offline analysis + live read-only check)

Input: `artifacts/hardware/av-cam-camera-original.bin` (16,803,048 B, SHA-256 `dd8f8d0c…`, ORIL base VA `0x635C6000`); string and call xrefs come from `artifacts/scratch/r5/{pcref_index,call_index}.pkl`.

- `FW: SET_STROBE_MODE` (`0x63bee474`) only stores the requested strobe / wireless mode in the FW param block `0x6442451C` (`+0x424`/`+0x428`). `CheckSetStrobeMode` and `CheckSetSilentShutterMode` only log cur/req mismatches.
- The strobe charge controller is `0x63bcfb40`, called from `0x63b9caac`. It logs `EV CamStrbChargeOn/Off` and writes the charge state in obj5 (`0x6418FCC8`, `+0x7f`/`+0x80`/`+0x83`). It turns charging off when `0x63bcfa78(...)` returns 1.
- `0x63bcfa78` returns 1 when any of these holds: `inhibit_tbl[a0]` with `inhibit_tbl = 0x63F926FC = {0,0,1,0,0,0,0}`; bit `a1` of mask `0x7EF07`; `a2 != 0`; `a3 == 1`; `obj5+0xa6 == 1`; or (`*(0x6444C78C)+0x1c == 1` and `obj5+0xec != 1`). Here `a0 = [obj6+0x368]`, `a1 = [obj6+0x8]`, `a2 = [obj6+0x712]` with obj6 = `0x6440A278`, and `a3 = [0x6440B230+0x51]`.
- **Live read-only check** (user go-ahead for exactly 6 DRAM pages: PA `0x0440A000`, `0x0440B000`, `0x0418F000`, `0x0444C000`, `0x044B8000`, `0x04446000`). `ro_page` (`55144dad…`) made 9 reads per state of at most 256 B each, with no SIGBUS, uptime rising throughout, the helper removed afterwards and `/tmp_bt` clean. Receipts are `artifacts/tests/ro-page-flash-20261127/read-state{A,B}-*.json`.
  - Silent ON: `a0 = 2` and obj5 `+0x7f`/`+0x80` = 0 (charge off).
  - Silent OFF: `a0 = 1` and obj5 `+0x7f`/`+0x80` = 1 (charge on).
  - `a1` (3, mask bit clear), `a2`, `a3` and the global flags were the same in both states.
- **Conclusion:** in electronic-shutter mode av-cam sets its shutter mode `[obj6+0x368] = 2`, and `inhibit_tbl[2] = 1` disables strobe charging. That is why the flash cannot fire, independently of every libObj / UI override above.
- **Candidate patch (not applied):** clear the byte `inhibit_tbl[2]` at VA `0x63F926FE` (PA `0x03F926FE`). This would be a write to av-cam DRAM, which is outside Mod Loader v2 and the standing authorization. It may also only enable charging: the electronic-shutter capture sequence may need its own strobe trigger, and the ~30–35 ms readout limits full-frame flash sync to about 1/15 s or slower.

## Correction after v9 (2026-11-27)

v9 swapped `inhibit_tbl[2]` (PA `0x03F926FE`) with verified readback (`avcam=w`), but the flash still did not fire. A third read-only read (Silent ON, patched, `read-stateC-patched-*.json`) shows the charge state unchanged: obj5 `+0x7f`/`+0x80` = 0 and `+0x88` = 3.

Reason: `0x63bcfb40` first tests the byte `*(0x6444C78C)`. That byte was 1 in every read, so execution branches to `0x63bcfcb4`, and the `0x63bcfa78` / `inhibit_tbl` path analyzed above **is not executed**. **The "av-cam root cause" section above is superseded.**

In the executed branch, obj5 `+0x88` is set to 3 when `0x638b4864()` returns non-zero (`0x63bcfd22..0x63bcfd2a`), and the same predicate also gates `+0x98` at `0x63bcfd82..0x63bcfd88`. `0x638b4864` returns the global flag byte `0x644AC6AA`, which is set by `0x638b4870` and cleared by `0x638b48a4` and has 101 readers. That flag, presumably the electronic-shutter state, is the candidate charge gate. It is not verified live, because its page is outside the six approved pages.

The v9 table swap is harmless but ineffective; switching the mod OFF restores the table. A targeted fix would patch the two branches in av-cam `.text`. That is a code write on another core, with I-cache coherency uncertain, and it is not applied.

## v10 / v11 results (2026-11-27)

- **v10** turns `cbz r0` at `0x63bcfd26` (PA `0x03BCFD26`) into `b`, guarded by its context, with readback (`avcam=w`). With Silent ON, obj5 `+0x88` went from 3 to 0 (read-only check, state D). The av-cam core therefore executes the patched code; the I-cache did not hide the write. Charging stayed off.
- **v11** also widens `ands r2, r3, #0x7b` at `0x63bcfdf8` to `#0x7f` (halfword `0x027b` -> `0x027f` at `0x63bcfdfa`). av-cam's shutter mode `[r5+0xc]` is 2 with Silent ON and 1 with it OFF, and mode 2 had cleared r6/r7/`+0x86`. With Silent ON and the flash raised (state F): obj5 `+0x7f`/`+0x80` = 1, `+0x86`/`+0x87` = 1, `+0x0` = 1, `+0x88` = 0, and libObj `STROBE_POPUP_ACT` = 1, the same as Silent OFF. **Charging now works with Silent Shooting ON.**
- **The flash still does not fire.** Against Silent OFF, the remaining obj5 differences are `+0x2` (0 vs 1) and `+0xd5` (0 vs 1); the other differing bytes are timestamps. The charge controller does not write `+0x2`; it writes `+0x7f/+0x80/+0x83/+0x115(r7)/+1/+6/+7/+8`. `+0x115` (r7, cleared by many conditions including timing) was outside the read window. The next boundary is the writer of obj5 `+0x2` and the electronic-shutter capture/strobe-trigger sequence. Even if that sequence is unlocked, the ~30–35 ms readout limits full-frame flash sync to about 1/15 s or slower.
- Read receipts: `artifacts/tests/ro-page-flash-20261127/read-state{C,D,E,F}-*.json`. Every read was a single page from the six approved PA pages, `ro_page` was removed after each session, and uptime was monotonic throughout.

## Trigger search (2026-11-27, after v11)

- Read-only states G (Silent ON) and H (Silent OFF), both with the flash raised and v11 active, cover obj5 `+0x000..+0x13f`. `+0x115` (r7 of the charge controller) is 0 in both states, so it is not the gate. The only stable differences are `+0x2` (0 vs 1), `+0xd5` (0 vs 1) and `+0x120` (2 vs 1, a copy of av-cam's shutter mode).
- Static search for the `obj5+0x2` writer: the 10 GOT-load sites of obj5 (slot `0x645C72D4`) and its consumers `0x63b894b8`, `0x63bc25d8`, `0x63ba0dbc`, `0x63bce890`, `0x63bd0f08` show no direct `obj5+0x2` store (`0x63b894b8` and `0x63bc25d8` write `+2` of other structs).
- The capture pre-flash (TTL "Prelight") dispatcher is in `0x638e2d54` (`CapturePrelight_Selector.cpp`; `FW: Not supported: Prelight sequence!` at `0x638e2de2` for mode > 3; it selects per prelight mode 0..3). Whether a strobe / prelight sequence is scheduled for an electronic-shutter capture is decided upstream of that dispatcher, presumably from shutter mode 2 or the global flag `0x644AC6AA` (101 readers). That decision point is not yet identified.
- Recommended next method (AGENTS.md QEMU/RE workflow): trace a flash capture with Silent ON vs OFF in the QEMU a6400 model with the TCG observer, and diff the paths around `0x638e2d54` and the obj5 `+0x2` writer, instead of more iterative live probing.
