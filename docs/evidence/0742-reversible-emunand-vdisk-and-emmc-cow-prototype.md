# E0742 — Reversible SD VDisk (`ext2` over FAT32/exFAT) and Internal eMMC Copy-on-Write Guard Prototype

Date: 2026-11-27. **Strictly emulator/offline.** Verified under Unicorn 2.1.4 and live
four-core QEMU `sony-a6400` (`v11.1.3` + `qemu-a6400` overlay) booting captured `ILCE-6400`
2.00 firmware (`nflasha3-Image` `d7a9ae09…`, `emmc.img` `18e37ec3…`). Zero USB, `/dev/mem`,
physical SD card, or camera hardware access; base `emmc.img` and virtual SD images verified
byte-identical before and after every run.

Labels: `[C]` confirmed by binary bytes or run receipt, `[I]` inference.

## 1. Summary of Verified Result

Implements a completely reversible emulator prototype (`tools/native_camera/emunand/` and
`tools/research/emunand_prototype.py`) for testing SD-backed `ext2` virtual-disk (`CFW_VDISK.IMG`)
overlays combined with kernel-level internal eMMC Copy-on-Write (`CoW`) write-blocking:

1. **Zero-carrier reversible kernel guard (`emunand_guard.ko`, 9,580 B) `[C]`:**
   - Built directly from freestanding ARMv7 C + assembly without embedding any proprietary
     `.ko` carrier bytes (`vermagic=3.0.27_nl-rt106+ SMP preempt mod_unload ARMv7 `, 14/14
     undefined symbols verified against `vmlinux` exports).
   - Dynamically resolves `emmc_seq_read`, `emmc_seq_write`, `wbi_drop`, and
     `v7_coherent_kern_range` via `kallsyms_lookup_name`, verifies the exact 16-byte ARM
     prologue (`e1a0c00d e92dddf0 e24cb004 e24dd010`) shared across `ILCE-6400` and
     `ILCE-6400A` `emmc.ko` (`ALFReD`), and patches both functions atomically via
     `stop_machine` + `v7_coherent_kern_range`.
   - Sinks all sector writes across user partitions (`part_id = 0`, including `vfat` mounts,
     raw `/dev/nflasha4` `MPR`, and `emmc_wbi_write_sector` `WBI1` hibernation) and boot
     partitions (`part_id = 1, 2` `nflashaB0`/`nflashaB1`) into a pre-allocated 4,096-sector
     (2 MiB) `vmalloc` RAM CoW hash table protected by ARMv7 `ldrex`/`strex` + `dmb`,
     never issuing hardware writes even on table exhaustion (`-ENOSPC` fail-closed).
   - Overlays CoW-modified sectors transparently on `emmc_seq_read` (with a zero-I/O fast
     path when all requested sectors reside in CoW), exposes `/proc/emunand_status` (`read`
     telemetry + `clear_cow` reset), and restores original 8-byte prologues and memory on
     `rmmod`.
2. **Freestanding FAT32 + exFAT raw-block VDisk controller (`emunand_vdisk_ctl.elf`, 11,208 B) `[C]`:**
   - Parses FAT32 (`8.3` + `LFN`) and exFAT (`NoFatChain` contiguous and FAT-chained
     fragmented cluster chains) directly on `/dev/mmca1` (`250:11`) **without mounting
     `/dev/mmca1` in the Linux VFS** (avoiding dual-FAT corruption with `im.elf`'s user-space
     `Fsys` and working on `exFAT` cards despite `0` `exfat` symbols in `vmlinux`).
   - Enforces `EMUNAND.OFF` / `disable_hook` SD kill-switches, attests full-image FIPS 180-4
     SHA-256 in freestanding C, attaches contiguous `ro` images zero-copy via `LOOP_SET_FD` +
     `LOOP_SET_STATUS64` (`lo_offset = first_lba * 512`), falls back to `tmpfs` staging for
     fragmented or `--rw` VDisks behind the 8,192 KiB `MemFree` guard (`E0643`), and issues
     `BLKFLSBUF` (`0x1261`) on detach so open raw block caches (`/dev/nflasha4` fd 29 in
     `im.elf`) drop discarded CoW pages immediately.
3. **Unicorn ARMv7 + Four-Core QEMU `sony-a6400` End-to-End Verification `[C]`:**
   - `tests/test_emunand_prototype.py`: `3/3 PASS` (`Ext2Metadata` + `e2fsck -fn` validation,
     8-stage Unicorn ARMv7 `.ko` execution + reversibility suite, and FAT32/exFAT
     contiguous/fragmented + kill-switch suite).
   - Three live 4-core QEMU `sony-a6400` full-system runs (`emunand-e2e-20261127-03`, `-05`,
     `-06`) pass **20/20** assertions: zero-copy `ext2` mount from an **exFAT** virtual SD
     card, `/setting/kemco.txt` bind override, `Backup_save` `1 -> 0`, CoW write/readback
     across `/setting`, `/tmp`, `/dev/nflasha4`, and `/dev/nflashaB0` (`writes_intercepted=9`,
     `sectors_written_cow=16`, `boot_writes_blocked=1`) with QMP `query-blockstats`
     confirming **`0` physical write operations / `0` bytes** on both `sd0` (eMMC) and
     `a6400-sd` (SD), followed by full live `stop` reversibility (`KEMCO_AFTER=kemco`,
     `MPR_AFTER=CMMeX`, `B0_AFTER=EXBL`, `BSAVE_AFTER=1`, `rmmod` clean).
4. **Flashable Kernel & Custom-OS Multi-Partition SD Mount Verification in QEMU `[C]`:**
   - Binary header inspection confirms Sony's `0700_part_image/dev/` and `0400_updater/dev/`
     flashable partition payloads (`nflasha3` `29,492,224 B` `vfat`, `nflasha7` `5,103,616 B`
     `ext2`, `nflasha15` `217,424,896 B` `ext2`, `nflasha1` `8,323,072 B` `vfat`) are standard
     uncompressed block filesystems, whereas `nflasha5` (`WBI1`) and outer `FDAT` `.dat`
     containers are non-filesystem blobs.
   - Upgraded `emunand_vdisk_ctl.elf` (`13,128 B`, `57432802…`) and `emunand_session.sh`
     (`8,485 B`, `4bceb8bc…`) to support up to `1 GiB` zero-copy images, `ext2` + `vfat` +
     `cramfs` detection, O(1) `--fast` metadata-only attach, multi-loop minors (`loop0..loop7`),
     `attach-file` for nested `initrd.img` files (since `ILCE-6400` `BusyBox 1.13.4` omits
     `losetup`), and `--extra-pkg` + `@pkg_subdir/` `/system` overmounts.
   - Two consecutive 4-core QEMU `sony-a6400` runs (`emunand-pkg-verify-20261127-04` and `-05`)
     pass **14/14** assertions off a 64 MiB `exFAT` virtual SD card (`86c29771…`):
     simultaneously mounting Custom-OS `CFW_VDISK.IMG` (`loop0`), unmodified `ILCE-6400A` 1.01
     flashable `NFLASH3.IMG` (`29.5 MiB` `vfat` on `loop1` overmounted onto `/system`, exposing
     `Sony CE Linux 8.2.7.6` `vmlinux.bin` + `av-cam.bin`), flashable updater `NFLASH1.IMG`
     (`8.3 MiB` `vfat` on `loop2`), flashable `NFLASH7.IMG` (`5.1 MiB` `ext2` rootfs on `loop5`
     with `version.txt = 38117_03.2026041003`), nested main `initrd.img` (`ext2` on `loop3`),
     and nested updater `boot/initrd.img` (`cramfs` on `loop4`), executing an ARMv7 Custom-OS
     binary inside `chroot /tmp_bt/emunand/mnt /sbin/custom_os_probe.elf`, rejecting raw `WBI1`
     (`rc=6`), recording **`0` QMP write operations/bytes** on both eMMC and SD, and restoring
     stock `/system` (`Sony CE Linux 8.2.6.0`) on `stop`.

## 2. Input and Artifact Identities

| Artifact / Input | Size | SHA-256 |
| --- | ---: | --- |
| `emunand_guard.ko` | 9,580 B | `352eb1fda1a3159e5237af528cebfaa0ae3a2e0f4fa64bc6360c014e52163c78` |
| `emunand_vdisk_ctl.elf` (v2 multi-loop/cramfs/1GiB) | 13,128 B | `574328027816991a952bb0f06f9e813fc3fe80503579ba5e7951be87f88e0b4b` |
| `emunand_session.sh` (v2 `--extra-pkg` + `/system`) | 8,485 B | `4bceb8bca405c99d8670dacd9c3caf7da85dc6b6f9464518d48f49c62104c34f` |
| Flashable `ILCE-6400A` 1.01 `0700_part_image/dev/nflasha3` (`NFLASH3.IMG`) | 29,492,224 B | `85e09d2145d9cce78afa2363625696422575611afd84ab927e7f1f1f0ba41ff2` |
| Flashable `ILCE-6400A` 1.01 `0700_part_image/dev/nflasha7` (`NFLASH7.IMG`) | 5,103,616 B | `45052699b0f6a765a2652185a64818327195f60f3d60078bfcd6f8b8564698ee` |
| Flashable `ILCE-6400A` 1.01 `0400_updater/dev/nflasha1` (`NFLASH1.IMG`) | 8,323,072 B | `c9040270ad886214c5656894b90fb1e839a3ab942cb65726633d8f1f1221c093` |
| Generated `sd_flashpkg_64m.img` (`exFAT`, before & after) | 67,108,864 B | `86c297713ff27aa1ed34322b07f1936137af02ee871e677f3dd5f969b6b5fb24` |
| Generated `CFW_VDISK.IMG` (`ext2` base E2E) | 262,144 B | `eb202f7213212ffa8f49438f888c85cc122caf2208a302c34761beca0ed6f4a1` |
| Generated `sd_exfat_16m.img` (before & after) | 16,777,216 B | `ecb2e046e2a36dea8685150e83cdd966532a065a18cb9bcf0166369f7d6ad5cf` |
| Captured `ILCE-6400` `emmc.img` (before & after) | 2,147,483,648 B | `18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b` |
| Captured `ILCE-6400` `nflasha3-Image` | 4,085,888 B | `d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618` |
| Powered WBI sub-micro extract (`E0715`) | 384 B | `3385ec340e931aa7b7dbb954da99e5b6239174ba90441eb2c905f01bcf5bbda9` |

## 3. Preserved Intermediate Receipts and Discoveries

All intermediate QEMU receipts are preserved under `artifacts/qemu-a6400/`:

- `emunand-e2e-20261127-01`: initial QEMU run revealed three concrete guest contracts:
  (1) `module_kallsyms_lookup_name` on `!CONFIG_KALLSYMS_ALL` resolves `STT_FUNC` symbols
  only, whereas `backup.ko` exposes `/sys/module/backup/parameters/Backup_save` as mode
  `0644` (`S_IRUGO | S_IWUSR`), saved/zeroed/restored directly by `emunand_session.sh`;
  (2) `ILCE-6400` `BusyBox 1.13.4` omits `tr`, `head`, and `dd conv=sync`; and
  (3) `ttyAM0` `AL_EMMC_SEQ` `printk` lines interleave with serial stdout until
  `/proc/sys/kernel/printk` is lowered.
- `emunand-e2e-20261127-02`: `19/20` assertions passed (`writes_intercepted=9`, `0` QMP
  block writes), while `MPR_AFTER` still read cached `COW_M` because `im.elf` holds fd 29
  open on `/dev/nflasha4` (`echo 3 > /proc/sys/vm/drop_caches` skips open `bdev` mappings).
  Adding `ioctl(fd, BLKFLSBUF, 0)` (`0x1261`) on `detach` in `emunand_vdisk_ctl.c` retired
  that stale cache retention (`MPR_AFTER=CMMeX` in `-03`, `-05`, `-06`).
- `emunand-e2e-20261127-04`: boot-time `AL_EMMC_SEQ` `printk` split the `QUIET_PRINTK`
  marker line before `Created HeapAllocator:`; waiting for `Created HeapAllocator:` and
  anchoring the marker regex gave back-to-back `20/20 PASS` runs in `-05` and `-06`.
- `emunand-pkg-verify-20261127-01..03`: initial multi-package QEMU trials identified the
  1,024-byte TTY canonical line truncation (`-01`), duplicate script edit anchor (`-02`), and
  108-byte `dd` banner slice (`-03`, where all 6 loop mounts and `chroot` ARM execution had
  already succeeded); staging `p1.sh..p3.sh` inside `emunand.tar` with 140-byte banner reads
  yielded back-to-back `14/14 PASS` runs in `-04` and `-05`.

## 4. Reproduction Commands

```powershell
# 1. Build ARM .ko + freestanding ELF bundle and run fail-closed static ELF audit
.\venv\Scripts\python.exe -m tools.research.emunand_prototype build --out artifacts\tests\emunand-check --host-ctl

# 2. Run offline Unicorn ARMv7 + ext2 + cramfs + FAT32/exFAT/kill-switch unit suite
.\venv\Scripts\python.exe -m unittest tests.test_emunand_prototype -v

# 3. Run full 4-core QEMU sony-a6400 live guest + QMP blockstats E2E verification
.\venv\Scripts\python.exe -m tools.research.emunand_prototype qemu-verify --report-dir artifacts\qemu-a6400\emunand-e2e-repro

# 4. Run 4-core QEMU flashable kernel (nflasha3/nflasha1) & Custom-OS (ext2/nflasha7) SD package verification
.\venv\Scripts\python.exe -m tools.research.emunand_prototype qemu-verify-packages --report-dir artifacts\qemu-a6400\emunand-pkg-repro
```

## 5. Assumptions, Boundaries, and Next Steps

- **Emulator-only verification:** No physical camera or physical SD card was accessed; any
  supervised hardware trial requires explicit owner go-ahead.
- **Post-boot live guard vs. PID-1 `kexec` remap:** `emunand_guard.ko` + `emunand_session.sh`
  activate after Linux and `mmc_drv.ko` are up (protecting runtime `/setting`, `/tmp`,
  `/etc`, `MPR`, `WBI1`, and `B0`/`B1` writes and bind-mounting `/usr`/`/setting`/`/etc`
  overlays). Replacing the running kernel or early `av-cam.bin` boot from PID 1 remains the
  separate `kexec` (`G1`–`G13` `handover_guard`, `E0715`) workstream.
- **Non-eMMC hardware state:** Blocking eMMC writes makes filesystem, `Backup.bin`, and
  `WBI1` mutations completely non-persistent, but does not intercept external sub-micro
  (`ObjMic`/`UPM`), PMIC, or mechanical lens/shutter commands.
