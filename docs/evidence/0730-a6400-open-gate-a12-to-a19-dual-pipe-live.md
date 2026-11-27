# E0730 - ILCE-6400 Open Gate: Live `A12`–`A19` Dual-Pipe, `tbl_0x34` / `desc_0xAC` `.bss` Baseline, and `0x63761C64` Discovery

- **Date:** 2026-11-27
- **Scope:** Physical `ILCE-6400` (`av-cam.bin` SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`, ORIL base `0x635C6000`) RAM-only (`noreboot on`, fail-closed `putback`) experiments `A12`–`A19`, offline disassembly of `0x63761C58` / `0x63762F32` / `0x6394E4E4`, and RAM-only 5 GHz Wi-Fi TCP debugger (`192.168.100.17:4640`) live Movie 4K 24p `.bss` baseline capture.
- **Predecessor:** `E0729` (Round 5 synthesis and Step `A12` candidate).

## 1. Live `A12`–`A19` Results (Physical `ILCE-6400`, All Restored to Stock)

| Step | Scope | Live Observation | `DIAG` Counters (`noreboot on`) | Restore Receipt |
| --- | --- | --- | --- | --- |
| `A12` | `A10` minus `rec_fmt_tbl[0..1]` (`0x03E11750`, `0x03E11760` kept stock `3840`), `375` entries | Clean `16:9` live-view; `A10`/`A11` tearing eliminated | `ref=0 vfc=378613 latch=0x00 rcv=0 sdvga=0` | `PUTBACK_A12: ok written=375`, `stock=375`, `RESETCNT: 0 -> 0` |
| `A13` | `A12` + `tbl_0x34[0]=6528` + `desc_0xAC[0] w[5,7,9]=1684,1684,421`, `384` entries | Left half clean, right half vertically shifted (dual-pipe split) | `ref=18 vfc=385241 latch=0x00 rcv=0 sdvga=0` | `PUTBACK_A13: ok written=384`, `stock=384`, `RESETCNT: 18 -> 0` |
| `A14` | `A13` with 64-aligned `desc_0xAC[0] w[5,7,9]=1728,1728,432`, `384` entries | Identical dual-pipe split (left clean, right shifted) | `ref=10 vfc=395901 latch=0x00 rcv=0 sdvga=0` | `PUTBACK_A14: ok written=384`, `stock=384`, `RESETCNT: 10 -> 0` |
| `A16` | `A12` + `tbl_0x34[0]=6528` only (`desc_0xAC` stock), `381` entries | Continuous L/R (no split), horizontal stride lines across frame | `ref=157 vfc=414045 latch=0x00 rcv=0 sdvga=0` | `PUTBACK_A16: ok written=381`, `stock=381`, `RESETCNT: 157 -> 0` |
| `A15` | `A14` + `desc_0xAC[0] w[28]=1792, w[38]=1728`, `386` entries | Black screen (no sensor image) | `ref=16 vfc=531741 latch=0x00 rcv=0 sdvga=0` | `PUTBACK_A15: ok written=386`, `stock=386`, `RESETCNT: 16 -> 0` |
| `A17` | `A14` + `desc_0xAC[0] w[38]=1728` (`w[28]=2048` stock), `385` entries | Black screen (no sensor image) | `ref=29 vfc=584673 latch=0x00 rcv=0 sdvga=0` | `PUTBACK_A17: ok written=385`, `stock=385`, `RESETCNT: 29 -> 0` |
| `A18` | `A14` + `.text` `0x03761C64` (`movw r4,#3240`) + `0x03762F32` (`movw r3,#3240`), `388` entries | Black screen (no sensor image); `bss_ac` stayed `1984` | `ref=18 vfc=645143 latch=0x00 rcv=0 sdvga=0` | `PUTBACK: ok written=388`, `stock=388`, `RESETCNT: 18 -> 0` |
| `A19` | `A14` + `.text` `0x03761C64` (`movw r4,#3240` -> `1620` offset) only, `386` entries | Black screen (no sensor image); `bss_ac` stayed `1984` | `ref=20 vfc=653439 latch=0x00 rcv=0 sdvga=0` | `PUTBACK: ok written=386`, `stock=386`, `RESETCNT: 20 -> 0` |

## 2. Static & Live `.bss` Findings

1. **`A12` confirms `E0729`:** Keeping `rec_fmt_tbl[0..1]` at stock `3840` eliminated the `A10`/`A11` stride tearing (`ref=0`).
2. **Hardcoded `3840` in `0x6376xxxx` (`.text`):**
   - `0x63761C58` (`0x63761C64`: `mov.w r4, #3840` -> `lsrs r4, r4, #1` = `1920`) is called at `0x6376334A`, `0x637633F4`, and `0x6376358A` inside `0x637631E4` to compute the right-pipe buffer offset. Leaving it at `1920` while `tbl_0x34[0]` was `6528` explains the `A13`/`A14` right-half vertical shift.
   - `0x63762F32` (`mov.w r3, #3840`) stores `3840` to `[r5+0]` and `[r5+2]` for `id == 0` or `id == 19`.
   - Patching `0x03761C64` to `3240` (`1620`, not 16/64-aligned) via uncached `/dev/mem` (without I-cache flush) in `A18`/`A19` resulted in `ref=18`/`20` and black screen; aligned `3200` (`1600`) and `3264` (`1632`) remain to be qualified offline in Round 6 (`docs/OPENCODE_SWARM_OPEN_GATE_PLAN_ROUND6.md`).
3. **Live Movie 4K 24p `.bss` baseline (`ro_page` over TCP `192.168.100.17:4640`, `pid=2356`):**
   - `tbl_0x34` runtime copy at `PA 0x041F1C38`: `7680 x4, 0x10e00, 7680 x2, 0x18e00, 0x10e00, 512, 64, 0, 0x28000` (Core 0 & Core 1 stock).
   - `desc_0xAC` runtime copy at `PA 0x041F2AC8`: `1984, 2160, 1984, 540, 496, 135`, `w[28]=2048`, `w[38]=1984`.
   - `0x637617FC` / `0x63761BF8` `.bss` structs at `PA 0x041F194C` / `PA 0x041F19D4` populated with active buffer descriptors; `0x6394E4E4` logger counter at `PA 0x04378A10` = `0`.
4. **Camera state:** Cleanly restored to stock after every test; RAM-only TCP debugger `/tmp_bt/a6400_usb_debugd_v2.elf` active on `HUAWEI-g9XR` (`192.168.100.17:4640`), `/setting/` unmodified.
