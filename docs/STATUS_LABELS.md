# Status labels

Every mod, kit and camera row in `README.md` and in each `mods/*/README.md` uses exactly one of
these labels per camera and firmware build. A label is a claim, so it links to the evidence record
or test report behind it.

| Label | Meaning | Minimum evidence |
| :--- | :--- | :--- |
| **Verified on camera** | Ran on a physical camera of that model and firmware, did what the README says, and was undone cleanly | A dated evidence record or a physical test report issue with the console output |
| **Emulator only** | Runs the original firmware code in Unicorn and/or the QEMU machine; never run on that camera | Passing offline tests or a QEMU receipt, linked |
| **Not run on a camera yet** | Built and checked offline (static analysis, unit tests), but no emulator or camera run proves the behavior | The offline check that was done |

Rules:

- **One label per build.** "Verified on camera" for `ILCE-6400` 2.00 says nothing about `ILCE-6400A` 1.01; give each build its own label.
- **Downgrade, never stretch.** If a later run fails, change the label in the same change that records the failure, and keep the failed report linked.
- **Known issues and untested items stay visible.** A "Verified on camera" row still lists what was not tested (frame rates, formats, power-switch behavior).
- **Host-written behavior is not a status.** Something a PC or emulator draws or decides on the firmware's behalf never earns a label (see `AGENTS.MD`).

## Mod README header

Each mod README starts with this block, right under its title:

```markdown
| | |
| :--- | :--- |
| **Status** | <label per build, with evidence links> |
| **Tested on** | <model + firmware + date, or "nothing yet"> |
| **Known issues** | <list, or "none reported"> |
| **Not tested yet** | <list> |
| **How to revert** | <OFF in MENU → Mods / power-off / battery pull / documented recovery> |
| **What to send back** | <console output, log zip, clip details>, via a [physical test report](../../.github/ISSUE_TEMPLATE/physical-test-report.yml) |
```

Adjust the relative link depth to the README's folder.
