# Repository layout

The root holds standard project files and ten directories. Each directory answers one
question: *what is it for?*

```text
.github/     CI, release workflows, issue/PR templates
apps/        programs people run: Look Lab, LUT converter, Android clients, camera daemon
docs/        guides, knowledge map, dated evidence records
emulators/   QEMU machines (sony-a6400 family, a6700, a6000)
mods/        camera mods and the Mod Loader v2 payloads
research/    preserved prototypes, read-only probes, telemetry captures
scripts/     launchers and build scripts (.bat/.ps1 with .sh twins)
src/a6400/   Python package: Unicorn engine, target profiles, Mod Manager logic, USB debugger
tests/       offline test suite
tools/       research tools (python -m tools.research.<tool>), Mod Manager UI, native helpers
```

| Entry | Purpose |
| --- | --- |
| `README.md`, `LICENSE`, `CONTRIBUTING.MD`, `AGENTS.MD` | Project, licence, contributor and agent instructions |
| `pyproject.toml`, `requirements*.txt` | Project metadata and the offline test, tool and GUI dependency sets |
| `project_paths.py` | Single root path-resolution module (see below) |
| `.mcp.json.example` | Portable workspace MCP server template (copy to ignored `.mcp.json`) |
| [`apps/`](../apps/README.md) | `isp-studio/` (BIONZ Look Lab), `lut-converter/`, `android/` (`bionz_look_lab`, `cinemahud`, `raw-usb-ref`), `camera-daemon/` |
| [`emulators/`](../emulators/README.md) | `qemu-a6400/` (`CXD90045` machine family), `qemu-a6700/`, `a6000/` |
| [`research/`](../research/README.md) | `legacy/`, `probes/`, `reports/`, `reference/` |
| `firmware/`, `backups/`, `artifacts/`, `scratch/` | Local preserved inputs and generated results; ignored by Git |

The 2026-11-28 move from 17 top-level directories to 10 is recorded, old path to new path,
in [LAYOUT_MIGRATION.md](LAYOUT_MIGRATION.md).

## Where earlier root files went

| Original location | Implementation location | Launch path |
| --- | --- | --- |
| `cxd90045_hle_emulator.py` | `research/legacy/cxd90045_hle_emulator.py` | `python -m research.legacy.cxd90045_hle_emulator` |
| `cxd90045_gui_runner.py` | `research/legacy/cxd90045_gui_runner.py` | `python -m research.legacy.cxd90045_gui_runner` |
| `sony_gui_simulator.py` | `research/legacy/sony_gui_simulator.py` | `python -m research.legacy.sony_gui_simulator` |
| `test_authentic_screens.py` | `research/legacy/test_authentic_screens.py` | `python -m research.legacy.test_authentic_screens` |
| `analyze_hardware_drivers.py` | `tools/research/analyze_hardware_drivers.py` | `python -m tools.research.analyze_hardware_drivers` |
| `firmware_analyzer.py` | `tools/research/firmware_analyzer.py` | `python -m tools.research.firmware_analyzer` |
| `unpack_firmware.py` | `tools/research/unpack_firmware.py` | `python -m tools.research.unpack_firmware` |
| `check_camera.py` | `tools/research/check_camera.py` | `python -m tools.research.check_camera` |
| `backup_camera.py` | `tools/research/backup_camera.py` | `python -m tools.research.backup_camera` |
| `launch_cinema_mpv.bat` | `scripts/launch_cinema_mpv.bat` | run the script; it resolves `%~dp0..` |
| `launch_cinemahud_android.ps1` | `scripts/launch_cinemahud_android.ps1` | run the script; it resolves the repo root from its own path |
| `launch_cinemahud_ptp.bat` | `scripts/launch_cinemahud_ptp.bat` | run the script; it changes to `%~dp0..` |
| `start_camera_shell.bat` | `scripts/start_camera_shell.bat` | run the script; it targets `%~dp0..\tools\cam_live_terminal.py` |
| `launch_custom_os_kernel.bat`, `launch_custom_os_liveview.bat` | `scripts/` | run the script; it changes to `%~dp0..` (the `custom_os/` tree is local and untracked) |
| (macOS/Linux) | `scripts/<same name>.sh` | each `.bat`/`.ps1` in `scripts/` has a bash counterpart that resolves the repo root through `scripts/_common.sh` |
| `status.png`, `status_a6400.png` | `docs/images/` | written by `tools/research/generate_status_image.py` |
| `UVC_HANDOVER_PLAN.md` | `docs/UVC_HANDOVER_PLAN.md` | documentation |
| `ARCHITECTURE_UVC_MONITOR.md` | `docs/ARCHITECTURE_UVC_MONITOR.md` | documentation (hash-pinned by E0348) |
| Root PNG files and `temp_crops/` | `artifacts/legacy/` with original names | generated output |
| `20261015-_DSC0799 copy.jpg` | `research/reference/20261015-_DSC0799 copy.jpg` | reference input |
| `.mcp.json` | `.mcp.json.example` | template (copy locally if needed) |
| `android-client-ref/` | `apps/android/raw-usb-ref/` | raw-USB Android reference client |
| `reports/a6100-picture-profiles.md`, `reports/l3-osd-progress-2026-11-21.md` | `docs/a6100-picture-profiles.md`, `docs/l3-osd-progress-2026-11-21.md` | documentation (`a6400_hw_dump.txt` stays in `research/reports/`) |

The nine former root Python shims were deleted: their implementations had already been moved,
they were only forwarding stubs, and their documented launch paths are the module paths above.
`docs/archives/` and the dated `docs/evidence/` records keep their original path text as
history; they are not current instructions.

## `project_paths.py`

`project_paths.py` stays at the repository root because it is the single path-resolution module
for the codebase: `PROJECT_ROOT`, `REFERENCE_ASSETS` and `legacy_output()` are imported by more
than a hundred first-party modules under `src/`, `tools/`, `tests/` and `research/legacy/`. All
of them are run as modules from the repository root, so the root stays on `sys.path` and the
module has no relocated mirror. `a6400_dump_dir()` reads
`emulators/qemu-a6400/inputs.local.json` and, with a warning, the pre-move
`qemu-a6400/inputs.local.json`.

## Verification scope

`tests/test_repository_layout.py` enforces this layout: the ten top-level directories exist, no
moved directory is tracked at its old place, no forwarding shims or launchers return to the root,
the moved assets live under `docs/`, and the launchers keep their `%~dp0..` /
`$PSScriptRoot`-parent resolution. `tests/test_target_profiles.py` and
`tests/test_research_status_sync.py` cover the multi-device registry and the documentation sync.
The offline suite is the primary check (`tests/README.md`).

Two offline module audits remain available from `tools/research/`:
`python -m tools.research.probe_interpreter` writes its bounded probe report to
`artifacts/execution/`, and `python -m tools.research.audit_execution --mode research` runs the
preserved harness, records unsupported behavior (including a screen factory that returns no
instance) and exits `0`; `--mode strict` stops at the first boundary and exits `2`. Both are
optional checks: a boundary exit is not a regression and neither proves camera fidelity.

USB diagnostics and service-mode backup tools are never executed by repository validation.
