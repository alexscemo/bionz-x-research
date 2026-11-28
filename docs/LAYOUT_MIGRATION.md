# Repository layout migration (2026-11-28)

The repository root went from 17 top-level directories to 10. Products moved under
`apps/`, emulators under `emulators/`, and preserved research material under `research/`.
`src/`, `tools/`, `tests/`, `mods/`, `docs/` and `scripts/` did not move, so every
`python -m tools.research.<tool>` command and every `a6400` import is unchanged.

| Before | After |
| :--- | :--- |
| `qemu-a6400/` | `emulators/qemu-a6400/` |
| `qemu-a6700/` | `emulators/qemu-a6700/` |
| `emulators/a6000/` | unchanged |
| `isp-studio/` | `apps/isp-studio/` |
| `a6400-lut-converter/` | `apps/lut-converter/` |
| `camera-daemon/` | `apps/camera-daemon/` |
| `android/` (`bionz_look_lab`, `cinemahud`, `raw-usb-ref`) | `apps/android/` |
| `legacy/` | `research/legacy/` (`python -m research.legacy.<prototype>`) |
| `probes/` | `research/probes/` |
| `reports/` | `research/reports/` |
| `assets/reference/` | `research/reference/` |
| `launch_custom_os_kernel.bat`, `launch_custom_os_liveview.bat` | `scripts/` (with `.sh` counterparts) |

## Reading older evidence records

Records under `docs/evidence/` and `docs/archives/` are historical and are **not**
rewritten. When a record names one of the paths in the left column, read it as the
path in the right column. Hash receipts in those records stay valid: files were moved
with `git mv` semantics and their contents were not changed by the move.

## Local files that Git does not track

A clone that existed before the move keeps its untracked, ignored files wherever they
were. Move them yourself if you still use them:

- `qemu-a6400/inputs.local.json` → `emulators/qemu-a6400/inputs.local.json`.
  `project_paths.a6400_dump_dir()` still reads the old file and prints a warning.
- `qemu-a6400/qemu/` (QEMU source and build tree) and `qemu-a6400/data/` →
  `emulators/qemu-a6400/qemu/` and `emulators/qemu-a6400/data/`. Re-run
  `bash emulators/qemu-a6400/setup-qemu.sh` if the build directory embedded absolute paths.
- `assets/reference/*` → `research/reference/`.
- `camera-daemon/build/`, `probes/build/` → `apps/camera-daemon/build/`, `research/probes/build/`.

`tests/test_repository_layout.py` fails if a moved directory is tracked at its old place again.

## Documentation moves (2026-11-28)

Plans and dated notes left the top of `docs/`. Notes with findings moved under
`docs/evidence/notes/` and are listed in the knowledge map.

| Before | After |
| :--- | :--- |
| `docs/OPENCODE_SWARM_OPEN_GATE_PLAN.md`, `…_ROUND2.md` … `…_ROUND6.md` | `docs/plans/open-gate/round-1.md` … `round-6.md` |
| `docs/QEMU_UNICORN_BLOCKER_PLAN.md` | `docs/plans/qemu-unicorn-blocker-plan.md` |
| `docs/EMULATOR_GUI_MENU_TOUCH_PLAN.md` | `docs/plans/emulator-gui-menu-touch-plan.md` |
| `docs/FILM_SIM_QUALITY_PLAN.md` | `docs/plans/film-sim-quality-plan.md` |
| `docs/BOOT_TO_GUI_PHASES.md` | `docs/plans/boot-to-gui-phases.md` |
| `docs/SILENT_SHUTTER_MOD_IDEAS.md` | `docs/plans/silent-shutter-mod-ideas.md` |
| `docs/MOTION_SENSOR_FINDINGS.md` | `docs/evidence/notes/2026-11-06-motion-sensor-findings.md` |
| `docs/l3-osd-progress-2026-11-21.md` | `docs/evidence/notes/2026-11-21-l3-osd-progress.md` |
| `docs/a6100-picture-profiles.md` | `docs/evidence/notes/a6100-picture-profiles.md` |
| `docs/sony_a6400_menu_inventory.md` | `docs/evidence/notes/a6400-menu-inventory.md` |
| `docs/ILCE6400_FW200_8257_MACOS_PATCH_NOTES.md` | `docs/evidence/notes/ilce6400-fw200-8257-macos-patch-notes.md` |
| `docs/PHASE0_PROGRESS.md` | `docs/archives/2026-10-18-phase0-progress.md` |
