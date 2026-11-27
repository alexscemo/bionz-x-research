# E9202 - ILCE-6700 kernel first boot on a new `sony-a6700` QEMU machine

- **Date:** 2026-11-27.
- **Scope:** offline only. No camera, USB, SD or flash access; the inputs are the 2.00 update
  images and the device tree captured in E9201. No firmware bytes are published.
- **Machine:** `qemu-a6700/hw/arm/sony-a6700.c` (QEMU 10.0.0, tarball SHA-256
  `22c075601fdcf8c7b2671a839ebdcef1d4f2973eb6735254fd2e1bd0f30b3896`), scripts and notes in
  [qemu-a6700/README.md](../../qemu-a6700/README.md). Tests: none beyond the run below.

## 1. Inputs

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| `vmlinux.bin` (AArch64 `Image`, `nflasha3` of the 2.00 update) | 7,008,256 | `bf878384f09ac2fc7ca5cd673e7ededb803a214f3fb410e19102ce575d6b16da` |
| `initrd.img` (raw ext2, same partition) | 5,218,304 | `76e56da1585c60b3addede9e6180f8695a8aca7ab4077d3253300472df9a5a19` |
| `cxd90057.dtb` (`/setting`, read from the camera in E9201) | 16,875 | `c5735ad9c6da8c48fb24eeee0bb743d07c486a47d5db19bef13b5eaea24ad280` |

Kernel banner: `Linux version 4.9.337-scel+ ... (gcc 6.4.0, Sony CE Linux 14.0.1.0) #1 SMP
PREEMPT Sat May 24 03:39:03 JST 2025`.

## 2. Observations from the image and the device tree

- The `Image` header is valid (`ARM\x64`, text offset `0x80000`), but its first instruction
  branches to code that programs `VBAR_EL3`, `SCR_EL3`, `CPTR_EL3`, `ACTLR_EL3`, `CNTFRQ_EL0`
  and `HCR_EL2` and `ERET`s to EL1: it expects to start at EL3.
- The device tree declares four `arm,cortex-a35` CPUs with `spin-table`, an `arm,gic-400`
  at `0xF13A1000`, a 2 MHz generic timer, five `arm,pl011` UARTs from `0xF1000000`, and the
  SoC blocks (SCU, clocks, resets, PL330 DMA, DWC3 USB, PCIe, SDHCI).

## 3. Run

Stock `qemu-system-aarch64` with `-M virt` ran silently and stopped with a data abort at
`FAR 0xFE005010`, because the early code reads a SoC register that `virt` lacks and the
image starts at EL3. On the new machine the kernel starts through EL3 and reaches, in order:
EL3 to EL1 hand-off, device-tree parse, GIC probe, `CXD clock driver init done`, the
architected timer at 2 MHz, five PL011 UARTs (`ttyAMA0`..`ttyAMA4`, IRQ 250..254), initrd
unpack, `RAMDISK: ext2 filesystem found`, `VFS: Mounted root (ext2 filesystem) on device
1:0`, and the real `/sbin/init`, which loads `devno.ko` and `dma330.ko`. `dma330_probe`
dereferences NULL and Sony's exception monitor takes over (`Exception>`). About 22 KB of UART
output in the first 8 s.

Boot-contract facts learned by running it: the image's EL3 code hands the kernel the device
tree at `RAM_BASE + 0x2000`; a DTB elsewhere is rejected ("invalid device tree blob"); a
command-line override is looked up at `RAM_BASE + 0x1000`.

## 4. Boundaries

- Only the UART, GIC, timer and RAM are modelled. Every other SoC register block is an
  unimplemented device that reads zero. `GIC CPU mask not found`, `CPU1..3 failed to come
  online` and `check_cr_sram_init_done timed out` are unexplained or expected consequences.
- This is not a verified model of the SoC and not a camera application boot: no UI, no
  USB gadget and no TCP or USB debugger exist for this body yet.
- The kernel, initrd and device tree are proprietary and stay under `artifacts/`. The GPL source
is Sony's and also stays there.

## 4a. Update: Sony GPL source and the camera's own boot parameters

Sony's public GPL package for ILCE-6700 Ver.2.00 (`linux-kernel.tar.gz`, 109,976,756 bytes,
SHA-256 `fb39a21e67d6728e716adce36505f2b38ef8b37a76e3560025059371f373ec90`, downloaded from
oss.sony.net under Sony's notice and kept under the git-ignored `artifacts/`) supplies the SoC map
(`drivers/udif/mach-cxd900xx`): DDRC `0xF105A000`, SCU `0xF1388000` (DATA/SET/CLR groups), eSRAM
`0xFE000000`, and its Linux clock, GIC and PHY drivers. The updater partition's
`/boot/loadaddr.txt` gives the load addresses (kernel `0x400080000`, initrd `0x408000000`, command
line `0x400001000`, device tree `0x400002000`) and `/setting/kemco.txt` the real kernel parameters
(`mem=4G`, `memrsv=...:uc`, `adrs_conv.*`).

With these, the machine adds the 16 MiB eSRAM, the SCU set/clear block, the DDRC INTSTAT done bits,
the USB PHY SRAM-init bits and two `arm,pl330` controllers, and boots with `-m 4G`, the real
parameter file and the camera's load addresses. Result: the USB PHY SRAM-init waits pass, the
`dma330` module prepares both controllers (earlier it crashed), `crypt-emmc.ko` loads and then
`cryptEmmc_setKeyCovertly` aborts. Correction (see 4b): the abort was a data read of the unmapped
address `0xFFFFB8EB` (an OTP/fuse window), not a bad jump as first recorded.

## 4b. Update: crypt-emmc and the eMMC controller

Method: `aarch64-linux-gnu-objdump` of `crypt-emmc.ko` and `emmc.ko` (extracted read-only from
the update initrd's `/kmod`), the crash register dumps of Sony's exception monitor, QEMU
`-trace sdhci_*,sdcard_*` output, and Sony's GPL `platform.c`/`irqs.h`. Each item below is an
observation that made the next step run; none is a model of the real hardware.

| Step | Observation | Machine change |
| --- | --- | --- |
| 1 | `cryptEmmc_setKeyCovertly` reads key bytes at `0xFFFFB800..0xFFFFBBFF`; unmapped, so external abort | read-only zero window at `0xFFFFB000` (key = zeros) |
| 2 | engine registers at `0xF10E0000`; after the key block the driver needs status `+4` bits 18 and 20 set, bit 0 clear, else `BUG()` | stateful block with those status bits forced |
| 3 | PIO data window at `0xF8000000`, offsets checked against 64 MiB | 64 MiB RAM stub, no AES-XTS |
| 4 | `emmc.ko` aborts with "Not supported ADMA2": capability register at `0xF10D6000+0x40` | `generic-sdhci` at `0xF10D6000`, `capareg` `0x016CC8B0`, IRQ SPI 176 (`IRQ_EMMC0` = 208 minus 32) |
| 5 | first command is CMD13 with RCA 1; `ocr` `0x40000000`: the card is expected to be initialised | CMD0/1/2/3/7 replay once the VM starts |
| 6 | ADMA table at `0x4_0844F800` but descriptor data `0x08823E00` (low 32 bits) | DMA view aliasing DRAM at 0 |
| 7 | PARTITION_CONFIG must satisfy `(v & ~7) == 0x08` (`fixup_part_conf`), else panic | `boot-config` 0x08 |
| 8 | EXT_CSD revision 3 rejected; jump table in `set_emmc_spec` accepts 5..8 | `ext-csd-rev` 8 via `patch_sd_emmc.py` (adds a property to QEMU's `sd.c`) |

Result: `eMMC: 4096MiB ...`, then `partition table invalid` on an all-zero image; the initrd script
then fails to mount `/dev/nflasha7` and init dies (expected: no partition table yet).
Assumptions: the OTP key is zero, the engine status bits are the ready bits, EXT_CSD revision 8 and
the card hand-off values are chosen from what the driver accepts, not read from the camera.
Boundary: the sibling ILCE-7M4 research (an upstream lead, not verified for this body) also leaves the
eMMC controller initialised by the boot ROM, which is consistent with step 5 but does not prove it.

## 4c. Update: partition table and the first encrypted read

Method: disassembly of `emmc.ko` (`emmc_blk_plat_init`, `emmc_blk_plat_probe_post`) and QEMU
`sdhci_*` traces. Observations:

- The partition table is read from sector 0 of the user area (hardware partition 0). Header:
  magic bytes `8246`, version bytes `2.00`, `n_partition` at +8 (at most 30), entries from +0x20,
  16 bytes each `{start, size, type, flag}` in 512-byte sectors, the same four columns as the
  camera's `partinf.conf`. An entry is used when `flag` bit 0 and `type` bit 2 are set and
  `start+size` fits the device. Header bytes +12..+31 are unexplained and are left zero.
- `qemu-a6700/build_emmc_image.py` builds a sparse 2 GiB image from `partinf.conf` and the local
  partition images; the driver then registers `pnflasha1..` for the 30 entries (result: the
  `pnflasha` partition line in the log).
- The SDHCI must advertise 1.8 V (capability bit 26): the driver switches the bus power to 1.8 V
  after the first data read and waits for the register to read back.
- Sony's GPL `platform.c` names the block at `0xF10E0000` "cmmc" (register window 0x1000) and
  `0xF8000000` (size `0x4000000`) its in-buffer, without an interrupt. Reads of an encrypted
  partition (nflasha7 at sector 0xE4002) program the engine (`+0x30` sector, `+0x24` length,
  `+0x0` control `0x10000011`, `+0x20`) and point the ADMA descriptor at `0xF8000000`.

Boundary: that first encrypted read times out (`hw_data_wait`, error -110) after the SDHCI
reports command and transfer complete; the condition that the driver waits for from the cmmc
engine is not identified, so `/rootfs` (nflasha7) does not mount. The engine remains a stub.
`patch_sd_emmc.py` also carries two eMMC compatibility changes (CMD1 voltage window, SDHCI clock
check) that this repository's a6400 QEMU patch had already found; they are re-implemented, not
copied.

## 4d. Update: the encrypted read waits for a DMA330 completion, not an engine bit

Method: rebuilt the machine from commit `120d13e`, booted with a 2 GiB image from
`build_emmc_image.py` (`-drive if=sd`), read the `a6700-crypt-emmc` access log and disassembled
`emmc.ko` / `crypt-emmc.ko` (`aarch64-linux-gnu-objdump`). Observations:

- With the image attached the driver registers `pnflasha1..30`, then the first read of nflasha7
  (`kalcnflasha7`) fails with `hw_data_wait()` -110, the recovery commands (CMD2) time out and the
  Sony exception monitor reports a call through a NULL function pointer (all callers in `emmc`).
- After the kick (`+0x20 <- 0x30000c`) the driver only polls cmmc `+0x1c` (stays 0) and `+0x4`; it
  does not appear to poll an engine completion bit for the data path.
- `emmc.ko` imports `dma330_peri_transfer_async` and `dma330_reset_channel` (channel 15) from
  `dma330.ko`, and `emmc_crypt_dma330_wait` sleeps on a wait queue (flag at `+136` of its state,
  roughly 1 s timeout) until a DMA330 completion callback sets it. The encrypted path therefore
  moves data between the cmmc buffer window (`0xF8000000`) and memory through a peripheral-flow
  controlled DMA330 channel; the missing piece is that transfer and its completion.
- QEMU's `pl330` model logs `bad write offset 0x4` once, at the moment of the `+0x20` kick, so the
  Sony `dma330` programming is not accepted by that model as is (unverified whether the DMA330 is
  a stock PL330 at all).

Follow-up in the same section (`qemu-a6700/patch_pl330_log.py` logs the pl330 registers, faults and
executed microcode opcodes to the `-d unimp` log; the machine now gives the pl330 the same DMA
view as the SDHCI, DRAM aliased at 0):

- The Sony driver programs a standard PL330 register sequence: `INTEN +0x20 <- 0x80`, then
  `DBGINST0 +0xD08 <- 0x07A20700` (DMAGO, non-secure, channel 7), `DBGINST1 +0xD0C <- 0x103B4C20`
  (microcode address: low 32 bits of a DRAM address), `DBGCMD +0xD04 <- 0`. The write of `+0x4`
  (`0x103B4C18`) that QEMU rejects as "bad write offset" is not a PL330 register; it is ignored.
- The microcode (read from guest RAM through the monitor): DMAMOV CCR, DMAWFP burst on peripheral 0,
  DMAFLUSHP, SAR `0xF8000000` (the cmmc buffer window), DAR `0x21E53400`, loops of DMAWFP/DMALD,
  DMASEV event 7, DMAEND.
- With the DMA view alone QEMU faulted (`0x40`, channel peripheral error) at the first DMAWFP,
  because DMAGO is non-secure and the model's `PNS`/`INS` masks default to 0. With `PNS`/`INS` set
  to all ones (assumption: the boot code leaves peripherals and events non-secure) the whole
  microcode runs, event 7 raises INTMIS `0x80`, the driver clears it and the encrypted read no longer
  times out (`hw_data_wait` -110 and the recovery CMD2 failures are gone).
- The peripheral request line of the cmmc engine is not modelled: QEMU's pl330 never stalls on
  DMAWFP while `periph_busy` is 0, so this transfer is not paced by the engine.

New boundary: `/rootfs` still does not mount (`mount: mounting /dev/nflasha7 on /rootfs failed:
Invalid argument`). The image sector 0xE4002 holds the plaintext ext superblock, but the window at
`0xF8000000` read through the monitor after the failure contains random-looking words, so the
data path between the SDHCI ADMA target, the window and the DMA330 source is not yet confirmed
(possibly the window was overwritten by later crypt-emmc PIO self-test writes, or the ADMA target
differs for this read). Next: log the ADMA descriptor addresses of that read and the window
contents right after the DMA completes.

## 4e. Update: the encrypted read completes - card addressing, ADMA2-64 stride and the window

Method: `qemu-a6700/patch_sdhci_adma_log.py` (logs every ADMA descriptor, its 16 raw bytes and the
first writes into the cmmc window), `-trace sdhci_send_command,sdcard_req_addr,sdcard_set_block_count`,
`aarch64-linux-gnu-objdump` of `emmc.ko` (`sdhci_dma_map`, the host-control code at `0x1c30`), and
the run in section 3 with a 4 GiB image built by `build_emmc_image.py ... 4096` (the driver reports
`eMMC: 4096MiB`, and 4096 MiB is also above QEMU's exactly-2-GiB SDSC boundary).

- `patch_sd_emmc.py` skipped its whole `sd.c` edit list whenever the unrelated `ext_csd_rev`
  marker was already present, so the CMD1 voltage-window/capacity edit had never been applied.
  After the driver's re-init sends CMD1 `0x40000080`, QEMU stayed in idle state, CMD2 timed out
  (`-110`) and the driver reset the card. Fixed: each edit is now applied independently.
- With the capacity bit set, `sdcard_req_addr` shows `req 0xe4002 addr 0x1c800400`; the first word
  written into the window is `0x00000500`, the ext2 `s_inodes_count` of nflasha7 at sector
  `0xE4002` (the partition starts at `0xE4000`, the superblock is at `+1024`). Before the fix the
  card was byte-addressed and the read returned byte `0xE4002` from inside nflasha1.
- The driver selects ADMA2-64 only when the capability register has bit 28 set (disassembly
  `emmc.ko` `0x1c80..0x1c94`: `orr #0x10` vs `orr #0x18`, then `tst ... #0x10000000`). The machine
  now advertises `0x156CC8B0`. The driver's table stores 12-byte ADMA2-64 entries
  (`{attr,length,addr64}`) at a **16-byte stride**; QEMU's stock 12-byte stride read the padding as
  descriptor 1 and raised an ADMA error. Raw bytes of a two-entry window table:
  `21 00 00 10 00 00 00 f8 00 00 00 00 00 00 00 00 21 00 00 10 00 10 00 f8 ...`
  (attr `0x21`, length `0x1000`, address `0xF8000000`, then the second entry at +16).
  `patch_sd_emmc.py` adds an opt-in SDHCI property
  `adma64-16byte` (default off); `sony-a6700` sets it.
- Result: the 26-block encrypted read at sector `0xE43CE` (CMD23 `0x1a`, CMD18) and the DMA330
  transfer run to completion; the earlier `-110`, the recovery CMD2 failures and the `va_emmc_reset`
  panic are gone. The window receives the ext superblock and the following directory blocks
  (observed writes `0x00000500`, `0x00000016`, `0x00000000`, `0x00000002`/`0x000000a4`, then
  file data), and the DMA330 moves them to RAM (`0xF8000000` source, event 7).
- Boundary: no `mount /dev/nflasha7 on /rootfs` line appears in this log, so `/rootfs` is not yet
  shown mounted. Init instead continues to the next module: `insmod pciedma.ko` is missing from the
  captured module set, `pci_ep_cp.ko` loads, and `dw_pcie_read` takes a synchronous external abort
  at the PCIe controller window `0xF7000000` (+`0xF11C0000` PHY); the machine's unimplemented SoC
  window ends at `0xF1FFFFFF`, so those registers are unmapped. Repeatable in two runs (temporary
  stride edit and the formal property), both stopping in `udm_pci_ep_cp` at about 10 s.

## 4f. Update: PCIe windows as logging stubs, init mounts and im.elf

Method: added `create_unimplemented_device` regions for the PCIe controller window
`0xF6000000..0xF7FFFFFF` (covers `pcie_i` at `0xF7000000`, `pcie_m0` at `0xF6800000`,
`pcie_m1` at `0xF6000000`) and the `pcie_i` config/range windows `0xDFFC0000`/`0xDFFE0000`; the
PHY at `0xF11C0000` is already inside the existing `0xF0000000` window. They read zero and log
every access, like the rest of the SoC scaffold. Same 30 s run as 4e.

- `dw_pcie_read` no longer faults. The log shows `dw-pcie f7000000.pcie_i: PCI host bridge to bus
  0000:00` and `mem 0xdffe0000-0xdffeffff`, then `pcie_if`, `pcieatu`, `osal_uipc`, `osal_utm` and
  `ldec` load. The kernel warns in `osal_set_cpuset_cpus` because a cpuset write returns `-28`.
- nflasha7 now mounts on `/rootfs`: the initrd's fstab entry is `/dev/nflasha7 /rootfs ext2
  ro,nodiratime,noatime,sync`, its earlier `EINVAL` line is absent, `pivot_root` proceeds and
  init2 reaches the BusyBox prompt `/ #` before starting `im.elf`.
- All other partition mounts fail with `EINVAL` (`nflasha2 /setting`, `nflasha3 /system`,
  `nflasha8 /cp`, `nflasha10 /tmp,/etc,/var`, `nflasha11 /log`, `nflasha12 /cert`,
  `nflasha18 /lens`). The local images for those partitions start with `eb 3c 90 6d 6b 66 73 2e`,
  not a filesystem; only nflasha7 and nflasha15 have the ext2 magic at `+0x438` and nflasha5 has
  the `WBI2` marker. The captured images of the other partitions are therefore still encrypted or
  wrapped, and `crypt-emmc` is a pass-through stub, so those mounts cannot succeed. This is not an
  eMMC transport failure.
- After `[ulogio] failed to load config from driver!`, `FixedAreaTop/Size: 0` and
  `ERROR : Failed to mmap!`, init starts `im.elf` (`program: im.elf(im.elf)`, pid 455, mappings
  `0x400000..0x419000`, `libIMDB.so`, `libjemalloc_ds.so.2`, `ld-2.25.so`). It takes a user-mode
  SEGV at `PC=0x7f9bc7d9d0` with `r0 = 0`; that address is `libMWF.so+0x409d0`, the entry of
  `MonQueue::GetQueueRemain()`, which dereferences `this` (`ldp x1, x2, [x0]`). Callers are in
  libMWF at `0x3f4c0`/`0x3f570`. An earlier `WARNING: file not found: im.elf` does not prevent the
  binary from starting.
- Boundary: the PCIe block is a read-as-zero scaffold, not a model; the camera application's
  user-space fault is the new stop; the sibling encrypted partitions need the real crypt engine
  (AES-XTS/in-place decryption), not the current pass-through window.

## 4g. Update: all mounts with the live settings image; backup/ulogio and the im.elf monitor

Method: a local eMMC image built from the update partitions plus a live-camera `/setting` FAT
capture that holds `BACKUP.BIN`/`BACKUP.BAK` (the update image does not; the scrubbed capture
omits them by design, and the capture stays outside the repository). The image is built with
`build_emmc_image.py ... 4096` and its slots are spot-checked against the source images by md5 (an
earlier build had left several slots zero; a fresh build restored them). The guest reads of
`/setting/Backup.bin` and `/setting/Backup.bak` match the host md5 exactly (`c1438c...`,
`ba14df...`), so the eMMC/FAT path is not corrupt.

- All fstab mounts now succeed (`nflasha2/3/8/10/11/12/18` vfat and `nflasha7/15` ext2). The
  earlier `EINVAL` mounts were missing partition data in the working image, not a filesystem or
  option problem.
- `backup.ko` passes `selct_path` but fails at `backup_init() error (file_load)`, so `Backup_read`
  and `Backup_get_datasize` are not exported. `osal_ulogio.ko` then fails to load (unknown
  symbols), libMWF prints `[ulogio] failed to load config from driver!`, `FixedAreaTop/Size: 0`,
  `ERROR : Failed to mmap!`, and `im.elf` SEGVs in `libMWF.so+0x409d0`
  (`MonQueue::GetQueueRemain`, `r0 = 0`).
- The backup file header is: first word `4`, cookie `0xAAAAAAAA` at +4, `BK4` magic at +16,
  record count `0x333` at +20. `backupfile_init` maps `arch_p32_to_p64(4)` = `0x400000004` and
  calls `Data_init` there. The machine preloads the first 4096 bytes of the live `Backup.bin` at
  physical `0x400000000` (the `memrsv=0x1000@0x400000000` page); a guest `/dev/mem` read at
  `0x400000004` returns the exact header (md5 `8c05ba8e...`) and no `arch_phys_to_cache` error is
  logged. The failure is therefore in the backup driver's expected fixed-area/bootloader state,
  not in the page mapping or the eMMC read.
- Boundary: the camera bootloader/WBI preload of the backup fixed area (or the `Backup_load`
  contract) is missing; that keeps backup/ulogio from initializing and leaves the MWF monitor
  queue NULL.

## 5. Next boundary

Reconstruct or emulate the bootloader/WBI hand-off of the backup fixed area that `backup.ko`
expects (`/setting/Backup.bin` header at `0x400000004`, `Backup_load=0`), so `osal_ulogio` exports
its symbols and libMWF's monitor queue initializes; then the `im.elf` user-space fault, the PCIe
register contract, USB, display and sensor, the secondary CPUs and the GIC CPU mask.
