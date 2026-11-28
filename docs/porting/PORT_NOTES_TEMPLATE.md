# Port notes: <mod or kit> → <target model> <firmware>

Copy this file next to the ported mod as `PORT_NOTES.md`. It is the record a reviewer reads to
decide whether the port may be offered to testers. Fill every section; write "not checked" rather
than leaving one out.

## 1. Identity

| | Source build | Target build |
| :--- | :--- | :--- |
| Model / firmware | <e.g. ILCE-6400 2.00> | <e.g. ILCE-6100 3.00> |
| `TargetProfile` | `src/a6400/targets/<id>` | `src/a6400/targets/<id>` |
| Files the mod touches (path, size, SHA-256) | | |

The kit or mod must refuse to run when the target files do not match these hashes.

## 2. Every changed site

One row per address the mod reads as a guard or writes. No site may be written without a guard
that checks its stock bytes first.

| Site | Source address | Target address | Stock bytes on target | New bytes | How the target address was found |
| :--- | :--- | :--- | :--- | :--- | :--- |
| | | | | | `tools.research.signature_scanner` match / disassembly / emulation |

Run the fail-closed scanner across bodies and keep its report:

```powershell
.\venv\Scripts\python.exe -m tools.research.signature_scanner --help
```

## 3. What was run offline

- Unicorn: which target code paths ran with the new bytes, and the result.
- QEMU: boot receipt with the mod loaded (if the target has a machine).
- Tables the camera builds at run time: how their target values were obtained (emulation of the
  target's own set-up code, or a read-only dump), never assumed equal to the source.

## 4. Differences from the source design

List every place where the target differs (screen shape, keys, buffer sizes, sensor modes) and what
the port does about it.

## 5. What is not checked

Be specific. These items go into the mod README's **Not tested yet** row.

## 6. First physical run

Leave empty until someone runs it. Then link the
[physical test report](../../.github/ISSUE_TEMPLATE/physical-test-report.yml) issue and set the
README status label ([STATUS_LABELS.md](../STATUS_LABELS.md)).
