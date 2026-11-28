# E0750 — BCM4339 keeps the chanspec set through WLC_SET_VAR

Date: 2026-11-27
Lead: L0194 (device-model, goal mods)
Base: main + L0188..L0192; base QEMU `828c8e28…`, change QEMU `193d0693…`,
temporary trace QEMU `e5660adf…` (environment-gated logging, built before one length guard was
added; see limits). The trace logging is not part of the change.

## Claim

The modelled BCM4339 now keeps the four bytes of the `WLC_SET_VAR` (CDC command 263) iovar
`chanspec` and returns them on `WLC_GET_VAR` (CDC command 262). In the panel A/B from the same
start frame `f60f46a3`, the guest `wl -i wlan0 chanspec` answers ` (0x0)` on the base build and
`36 (0xd024)` on the change. `0xd024` is channel 36 at 20 MHz, set later by the driver after the
supplicant started; it is not the 36/80 value the AP script asked for. The 36/80 value is read
back only in the trace arm, before it is overwritten.

## Evidence

- Trace (temporary, retained trace build; watched 21:42:22Z to 21:43:37Z). A `WLC_SET_VAR`
  `cmd=263 clen=13` carries `chanspec\0` + `0xe02a` (`payload=…2ae00000`); the next
  `WLC_GET_VAR` `cmd=262` for `chanspec` replies `plen=4`, `first32=2ae00000`, and no `36/40` or
  `36/20` retry set appears before `wpa_supplicant` starts. These requests come a few seconds
  before the panel driver's toggle note, which is written after the mod has already been placed,
  so that note does not mark the script's start. After the AP start the driver sets `0xd024`
  (36/20), and the final GET replies `first32=24d00000`. The base build was not traced; that its
  failed read-back runs the script's retry sets is inferred from the script.
- The AP script's read-back check matches the channel number only, and its log line
  `chanspec 36/80` is identical in both arms, so the log is not evidence of the width or of a
  successful set.
- A/B, counted pair `A-base2`/`B-final3` (both `-nic none` quoted from the saved
  `/proc/<pid>/cmdline`, same start frame `f60f46a3`): base `wl chanspec` is
  ` (0x0)`; change is `36 (0xd024)`. `wlan0` is present and
  `wpa_state=COMPLETED` in both arms. An earlier pair had the same guest
  results and start frame but its saved cmdline lacked the `-nic none` quote
  (the arm runner's `pgrep` pattern missed the temp path), so that pair is
  reported but not counted. A further change arm on a build without the final length guard gave
  the same quotes. Three panel instances were left running after their arms and were alive
  during later arms; each had its own temporary directory and ports.
- qtest `qemu-a6400/tests/test_qemu_l0194_bcm4339_chanspec_state.py`: 3 of 3
  checks pass on the change and 1 of 3 on the base build. `P.default` (a read
  before any set is 0) passes on both; `P.set_get` and `P.update` compare
  reply bytes `[28:32]` read from the device's Fn2 frame and fail on the base
  (0).

## wpa_state (report only, no fix)

Both arms reach `wpa_state=COMPLETED` (`wpa_cli -p …/ctrl -i wlan0 status`;
`wpa.log` ends at `CTRL-EVENT-CONNECTED - … completed (auth)`). After
`wpa_supplicant` starts the trace lists, in order: 263 `pmkid_info`; 122 ×3;
262 `bw_cap`; 263 `chanspec`; 262 `bw_cap`; 263 `chanspec`; 159; 201;
263 `wsec_key` ×4; 76; 78; 263 `wme_bss_disable`; 263 `auth`; 263 `wsec`;
263 `wpa_auth`; 263 `mpc`; 3; 20; 118; 2; 26 (SSID `A6400-5G-SYNC`);
263 `vndr_ie.add` ×2; 263 `ap_isolate`; 263 `wsec_key`; 262 `wsec`;
263 `wsec`; 262 `wsec`; 263 `mcast_list`/`allmulti`/10 …, then the final GETs.
Every model reply carries status 0 in the reply header (from the reply construction) and zero
bytes except the typed GETs. `wpa_state=COMPLETED` is observed in both arms; that no control
request is needed for it is inferred. The model sends no firmware events, so this state is not
evidence of anything on air: no access point, client, association or traffic is shown.

## Assumptions and limits

- A `chanspec` read before any set returns 0 (reset default; the model's
  choice, not a captured hardware value).
- Channel state only: the model does not start a beacon or an access point.
  `wpa.log`, `wpa_cli status` and the DHCP listener are guest outputs.
- The `36/80` encoding is the driver's own `0xe02a`, stored verbatim; the model
  does not decode or construct a chanspec.
- The retained trace build predates a `pay_in_len >= 9` guard added to the same
  GET branch in the final source; the traced read carries a 256-byte payload,
  so its reply bytes are unaffected.
- Offline emulator only; no camera, USB, network route or flash action.
