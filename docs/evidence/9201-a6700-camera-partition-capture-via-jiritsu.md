# E9201 - ILCE-6700 live partition capture through Jiritsu (10 partitions absent from the update)

- **Date:** 2026-11-27 (all times UTC, 16:40-17:05).
- **Scope:** one ILCE-6700 body (firmware 2.00), Windows host,
  USB only. Ten nflash partitions that the official 2.00 update does not carry were read
  from the running camera. No firmware was installed, no raw storage write was issued, no
  SD, `/dev/mem` or `nflasha23` access occurred, and no `FORMAT`, `FIRMUP` or `FACTORY`
  Jiritsu command was sent.
- **Related:** E9200 (ILCE-6700 offline firmware decrypt, recorded on the fork branch
  `a6700-e9200-offline-firmware-evidence`, not on `main`). E0643/E0668/E0669 (the a6400A
  route this builds on).
- **Authorization:** the owner explicitly approved the service-mode entry, the upload of an
  AArch64 helper and its use to read partitions. The helper's two files are written to
  `/tmp/jiritsu`, which is the `nflasha6` (Jiritsu) mount, so `nflasha6` was written: two
  files were created and later removed (section 3). It held no other files, so nothing
  needed a backup.

## 1. Result

| Partition | Role (`partinf.conf`) | Bytes | SHA-256 | Observed format |
| --- | --- | ---: | --- | --- |
| `nflasha1` | SYSTEM (Updater) | 25,100,288 | `2f716d79...2b60bf` | FAT (`mkfs.fat`) |
| `nflasha2` | SYSTEM (Setting) | 20,971,520 | `af7a40a2...c39ed` | FAT (`mkdosfs`) |
| `nflasha4` | MPR Flash Backup | 12,582,912 | `0e7efc1b...82cb` | no filesystem signature, 0.4% zero bytes |
| `nflasha6` | Jiritsu | 26,214,400 | `4dd482cd...09b9f` | FAT |
| `nflasha10` | Work (`/tmp`, `/etc`, `/var`) | 12,582,912 | `537e671e...6574` | FAT |
| `nflasha11` | Log | 314,572,800 | `845b6631...5947` | FAT |
| `nflasha12` | Network/Cert | 41,943,040 | `0135c947...6c15` | FAT |
| `nflasha13` | UpdaterWork | 4,194,304 | `5e943385...71ba` | FAT |
| `nflasha18` | LensData | 20,971,520 | `99a7cca5...f8b4` | FAT |
| `nflasha22` | AiDictionary | 68,157,440 | `865b341e...dedf7` | FAT |

Full 64-hex hashes are in the `SHA256SUMS.txt` file of each local capture directory
(gitignored). Total 547,291,136 bytes (521.9 MiB).
Sizes equal the `partinf.conf` sizes and the live `/proc/partitions` blocks.

**Cross-check against the update.** The first 2,097,152 bytes of live `nflasha7`
(read through the same channel) are byte-identical to the update's `nflasha7` image
(SHA-256 `f4c5b94c9664907680138649120a55c63e2577f7edb45877f53b5df0c12a7f4e`).

**Correction to E9200.** `partinf.conf` names 16 partitions with a non-zero size, not 13.
The update carries 5; the 11 absent from it are `nflasha1, 2, 4, 6, 10, 11, 12, 13, 18, 22,
23`. `nflasha23` (PMBP, 64 MiB) was deliberately not read (repository exclusion, E0222).

## 2. Inputs and tool identities

| Item | Identity |
| --- | --- |
| Camera | ILCE-6700, firmware 2.00 (identity query, USB `054C:0E76`, WinUSB `ilce-6700.inf`) |
| Service identity | `054C:0336`, WinUSB `ILCE-6700_Senser` (`oem64.inf`), staged by the owner before entry |
| Protocol library | Sony-PMCA-RE `a82f5baaa8e9c3d9f28f94699e860fb2e48cc8e0` (Senser auth), libusb via `libusb-package` |
| Update | `BODYDATA.DAT` 1,024,017,848 bytes, SHA-256 `d3c7db5b...6db847c` (decrypted with fx30_fw `bfebe07`) |
| `libJiritsu.so` | SHA-256 `f515adb30e562b98b5b4dfb4f9925221b78f8a2ccad1e8dfba62a42d4157db0e`; the camera served identical bytes at `/usr/lib` |
| `libsencore.so` | SHA-256 `62241744565abf87a683c94ea8e22357aaf3d5b10adecb0821f0a42192409ba7`; the camera served identical bytes |
| Tool | `tools/research/a6700_jiritsu_dump.py` (SHA-256 `b65b219e...12bfa4` at the final run) |
| Loader source | `tools/native_camera/a6700_jiritsu_loader.S` (SHA-256 `8c9abfa7...780be3`) |
| Loader binary | `libJit.so`, 2,760 bytes, SHA-256 `0891d189fc58143ac6210d0d0813a5802f23d35e41e4b2726ce7c176ce24976b` (aarch64-linux-gnu-gcc 14.2, `-shared -nostdlib`) |
| Tests | `tests/test_a6700_jiritsu_dump.py`, 18 tests, offline |

Licensing notes: Sony-PMCA-RE is MIT-licensed and is used unmodified from a separate
checkout at run time; nothing from it is copied here. The `fx30_fw` scripts (CC BY-NC-SA
4.0) were used locally only to read the update; no code, key or other content from them is
included in this repository. No firmware bytes are published.

## 3. Original instruction support (offline, hash-pinned libraries)

Observations from disassembling the two libraries above with capstone:

- **Authentication level.** `NinThread` in `libsencore.so` counts successful hop/step/jump
  rounds. Three rounds give level 3: `WorkerThreadManager::onComplete(level)` selects one of
  three handler tables (`pFunc_level_A/B/C`) and stores `adjCodeLevel`. Level A lists
  `0x10, 0x20, 0x30, 0x40, 0xff00, 0xff02`. FileControl `0xff01` and MemoryDump `0xff03` are
  in no table, which explains the Senser file-control refusal (status `0x80`) seen on this
  body, as on the a6400A (E0668).
- **Jiritsu command table.** `libJiritsu.so` holds a 0x48-byte-row table at file offset
  `0x2dad8`: category `0x1001`, commands `0x4001..0x4012` (`MODE, MOUNT, FORMAT, START,
  DEBUG, FIRMUP, SET_FILE, GET_FILE, FILE_DELETE, GET_FILELIST, GET_FILE_HASH,
  COMPARE_MEDIA, GET_VERSION, FACTORY, SET_FILE_PATH, SET_FILE2, GET_FILE2, FILE_DELETE2`),
  the same names, numbers and queue address `0x016F0480` as the a6400A table. Wire code is
  the low byte (the framing in `tools/research/a6400a_jiritsu_worker.py`, `JiritsuFiles`).
- **Read primitive.** GET_FILE with payload `01 00 00 "/.." + path + NUL` resolves to
  `/mnt` + `/..` + path and reads any regular file whose `stat` size is non-zero. `/proc`
  files and block devices report size 0, so they read as empty.
- **Upload and start primitive.** `adj_cmd_set_file2` writes the file, and when the path is
  `/tmp/jiritsu/libJit.so` it calls `dlopen` and immediately `dlclose`. A constructor in the
  uploaded object therefore runs once at upload. SET_FILE_PATH (code 15) sets the target.
- **Where the files go.** `/sbin/initrc/fstab` of the update's `nflasha7` mounts
  `/dev/nflasha10` as `/tmp`, `/etc` and `/var` (vfat, `rw,sync`), but libJiritsu mounts
  `/dev/nflasha6` at `/tmp/jiritsu` (`sys_mount`, vfat, synchronous). The capture of
  `nflasha6` taken while the helper ran contained exactly `run.sh` (731 bytes) and
  `libJit.so` (2,760 bytes, SHA-256 `0891d189...4976b`, equal to the uploaded loader).
  The upload therefore writes the Jiritsu partition; the files are removed again at the end.
  `/tmp_wbshd` is a `tmpfs`; all chunk and control files live there. `nflasha10` only gains
  an empty `/jiritsu` mount-point directory.

## 4. Operations performed on the camera

1. Read-only identity query (PMCA-RE `info -d libusb`): model, product code, firmware.
2. Senser authentication on `0E76` (vendor requests, no SCSI). The camera re-enumerated as
   `054C:0336` and the second authentication on `0336` succeeded (level 3).
3. Read-only GET_FILE of `/etc/hosts`, `/etc/passwd`, `/bin/busybox`, `/usr/lib/libJiritsu.so`,
   `/usr/lib/libsencore.so`, `/proc/*` (size 0). Live libraries matched the update hashes.
4. Helper session: SET_FILE_PATH + SET_FILE2 of
   `/tmp/jiritsu/run.sh` (readback verified), then of `/tmp/jiritsu/libJit.so` (starts the
   worker). The worker polls control files in `/tmp_wbshd/a6700-<nonce>/` and runs only the
   fixed job templates in the tool: `busybox cat` of `/proc` files, `busybox ls -l`,
   `busybox dd if=/dev/nflashaN bs=4096 skip=K count=M` into `chunk`, `busybox sha1sum`, and
   `printf END >>chunk`. The host reads `chunk` with GET_FILE, checks the SHA-1 reported by
   the camera, and keeps the SHA-256 of the assembled image.
5. A `stop` control file makes the worker remove its RAM directory, `run.sh` and
   `libJit.so`, then exit. The camera then leaves service mode on its own.

Commands never sent: `MODE`, `MOUNT`, `FORMAT`, `START`, `DEBUG`, `FIRMUP`, `FACTORY`,
`FILE_DELETE*`, `COMPARE_MEDIA`, any Senser `0xff01/0xff03`, any write to `/dev/*`.

## 5. Observation, stop condition and cleanup

- The capture moved 512 KiB chunks at roughly 1,000 chunks in 8 minutes; the camera kept
  at least 8 MiB of free memory throughout (guard in the tool).
- A separate read-only session afterwards found `/tmp/jiritsu/run.sh` and
  `/tmp/jiritsu/libJit.so` absent. The `/tmp/jiritsu` directory itself may remain (created
  by the upload); it is empty.
- The camera returned to `054C:0E76` and ran normally. Between `StopSenser` and `0E76` it
  appears briefly as `054C:096F` (HID). The working receipts stay under the gitignored
  `artifacts/`.

## 6. Constraints of the method

- **One launch per power cycle.** libJiritsu keeps the uploaded `libJit.so` mapped, so a
  second upload in the same boot does not rerun its constructor. Remove the battery between
  runs.
- **GET_FILE and sizes.** Reads of files whose size is a multiple of 4096 returned status
  `0x10000006` in every observation; all other sizes read fine. The tool pads each chunk
  with three bytes and strips them. This is an observed pattern, not an identified cause.
- **Transient statuses.** `0x10000006`, `0x10000008` and `0x1000000A` are retried.
- Unverified: the exact meaning of those statuses, and why the stop-path verification
  raises a USB exception (the camera leaves service mode first).

## 7. Assumptions and caveats

- Mounted partitions (`nflasha2, 10, 11, 12, 18, 22` and others) are non-atomic live
  snapshots, not factory images. The captured `nflasha6` contains the helper's two files
  (it holds nothing else), so it is not a pristine Jiritsu partition and was modified,
  then restored to empty, by the capture itself.
- Partition sizes are from `partinf.conf` and agree with live `/proc/partitions`.
- A single pass per partition exists, apart from the 2 MiB `nflasha7` agreement. A second
  pass was not made, so there is no two-pass agreement as for the a6100 set (E0707).
- The captures probably contain personal data (device serial, certificates, network and
  usage logs). They stay under `artifacts/`; this record carries only hashes and sizes.
- The ILCE-6700 is BIONZ XR (`CXD90057`, Linux 4.9). Together with the update's five
  images these give 15 of 16 partitions, but the repository's emulators model BIONZ X
  (`CXD90045`) and do not run them unmodified.

## 8. Repeatability and next boundary

Repeat: power-cycle the camera (battery out), confirm `054C:0E76`, then run
`python -m tools.research.a6700_jiritsu_dump --output-dir <new dir> --loader <libJit.so>
--pmca-source <Sony-PMCA-RE checkout> --partitions ... --confirm-helper-upload` (one launch
per power cycle; the tool refuses to overwrite existing helper files). The output
directory must not exist.

Next: a second pass for two-pass agreement; decide on `nflasha23`; decide whether a
BIONZ XR machine is in scope before any emulator work; keep the personal-data handling
rules for any further use of `nflasha2`, `nflasha11` and `nflasha12`.
