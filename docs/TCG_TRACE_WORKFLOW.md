# Standard Snapshot, Targeted TCG Trace, Persistent Symbol Index & 13-Body Signature Workflow

Use this four-pillar workflow instead of iterative cold-boot trial-and-error, hardcoded single-use QEMU plugins, or repeated whole-binary Ghidra auto-analysis.

## 1. QEMU Snapshot / Instant-Resume (`savevm` / `loadvm`)

Every `sony-a6400` peripheral device model implements deterministic `VMStateDescription` serialization. Capture booted machine + device state once, then iterate in `< 1-2 s`:

- **Capture a snapshot bundle (WSL2):**
  ```bash
  python3 -m tools.research.qemu_snapshot create \
    --inputs "$HOME/a6400-inputs/inputs-moddev-1" \
    --bundle-dir /tmp/a6400-snap-ready \
    --tag ready \
    --guest-command "uname -a"
  ```
- **Instant-resume a bundle:**
  ```bash
  python3 -m tools.research.qemu_snapshot resume \
    --bundle-dir /tmp/a6400-snap-ready \
    --report-dir /tmp/a6400-snap-resumed \
    --guest-command "echo OK"
  ```
- **Warm mod-dev & interactive UI:**
  - `python3 emulators/qemu-a6400/tools/warm.py save ready` / `warm.py load ready` / `warm.py start --from-snapshot ...`
  - `rear_panel.html` exposes **Save** (`/emu/savevm`) and **Restore** (`/emu/loadvm`) toolbar buttons alongside `--from-snapshot` / `--loadvm`.
  - Direct launcher overrides in `emulators/qemu-a6400/run.sh`: `SNAPSHOT_OVERLAY=<qcow2>` and `LOADVM=<tag>`.

## 2. Tier-1 SQLite Index & Tier-2 Persistent WSL2 Headless Ghidra (`symbol_index`)

Static lookups use an indexed WAL SQLite database (default `/home/administrator/a6400-ghidra-index/symbol_index.sqlite3`) and a resident `systemd --user` Headless Ghidra RPC daemon (`tools/research/ghidra/PersistentSymbolDaemon.java`, loopback `127.0.0.1:47640`):

- **Build / update Tier-1 index (ELF symbols, Thumb-2 direct callgraph, IPC queues, `.rodata` strings):**
  ```bash
  python3 -m tools.research.symbol_index build --body ILCE-6400 --root firmware/extracted_a6400
  python3 -m tools.research.symbol_index lookup osal_snd_msg --body ILCE-6400
  python3 -m tools.research.symbol_index callers osal_snd_msg --binary libObj.so
  python3 -m tools.research.symbol_index symbolize --binary viewUnified2.so --va 0x5ab658
  ```
- **Manage daemon & decompile on demand (no cold JVM startup or full auto-analysis):**
  ```bash
  python3 -m tools.research.symbol_index daemon ensure
  python3 -m tools.research.symbol_index decompile \
    --elf firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/viewUnified2.so \
    --target 0x5ab655
  ```

## 3. Targeted TCG Trace Plugin (`tcg_trace.c` + `qemu_tcg_trace.py`)

`emulators/qemu-a6400/tools/tcg_trace.c` is a parameterized QEMU Plugin API v7 observer emitting structured JSONL with zero guest modification:

- Supported plugin parameters: `out=`, `ring=<N>`, `ring_range=<lo>:<hi>`, `probe=<id>:<va>[+<base>]`, `deref=<id>:r<0-14>+<off>:<len>`, `mem=<pa_lo>:<pa_hi>` (poll-loop coalesced), `vmem=<va_lo>:<va_hi>`, `max_events=<N>`, `dump_ring=1`, `exit_on_hit=1`.
- **Single-shot snapshot + probe + MMIO capture + auto-symbolization (WSL2):**
  ```bash
  python3 -m tools.research.qemu_tcg_trace run \
    --from-snapshot /tmp/a6400-snap-ready \
    --report-dir /tmp/a6400-trace-run \
    --probe "set_root:0x5ab654+0x155e6000" \
    --deref "set_root:r0+0x0:32" \
    --mem "0xf2403000:0xf2404000" \
    --ring 128 --dump-ring --exit-on-hit \
    --body ILCE-6400
  ```

## 4. 13-Camera Function & Offset Signature Scanner (`signature_scanner`)

Propagate verified hooks and constants fail-closed (`UNIQUE_VERIFIED` vs `AMBIGUOUS_REFUSED` vs `ABSENT` vs `MISSING_BINARY`) across all 13 official `CXD90045` models (`docs/evidence/cross_model/platform.tsv`) and `ILCE-6400A`:

```powershell
.\venv\Scripts\python.exe -m tools.research.signature_scanner `
  --roots firmware `
  --json artifacts/analysis/signatures-13bodies/report.json `
  --markdown artifacts/analysis/signatures-13bodies/report.md `
  --emit-c-header artifacts/analysis/signatures-13bodies/headers
```
