# ILCE-6700 (BIONZ XR, CXD90057) QEMU machine - first boot

Status: **the stock 2.00 Linux 4.9.337 kernel boots on a new `sony-a6700` QEMU machine with the
camera's own boot parameters, mounts its initrd as root, runs the real `/sbin/init`, loads
`devno`, `dma330`, `crypt-emmc` and `emmc.ko`, reads the encrypted nflasha7 partition through the
DMA330 path (the ext2 superblock and directory blocks arrive in the cmmc window), mounts nflasha7
as `/rootfs`, pivots into init2, loads the PCIe and OSAL modules against read-zero PCIe scaffold
windows, and starts `im.elf` in user space. With the local live settings image it also mounts all
other fstab partitions; the MWF monitor still fails because `backup.ko` does not find the
bootloader-provided fixed-area state, so `im.elf` takes a user-mode SEGV in
`MonQueue::GetQueueRemain`.**
No UI, USB or TCP debugger exists yet. Inputs are camera-derived and stay under the
git-ignored `artifacts/`; this page lists identities and facts only. Evidence: E9202.

The ILCE-6700 is an AArch64 SoC (`CXD90057`, four Cortex-A35), so it needs a new machine
rather than a `TargetProfile` entry of the BIONZ X (`CXD90045`, ARM32) emulators.

## Inputs

| Input | Source | SHA-256 |
| --- | --- | --- |
| `vmlinux.bin` (AArch64 `Image`, 7,008,256 bytes, text offset `0x80000`) | `nflasha3` of the 2.00 update | `bf878384f09ac2fc7ca5cd673e7ededb803a214f3fb410e19102ce575d6b16da` |
| `initrd.img` (raw ext2, 5,218,304 bytes) | `nflasha3` of the 2.00 update | `76e56da1585c60b3addede9e6180f8695a8aca7ab4077d3253300472df9a5a19` |
| `cxd90057.dtb` (16,875 bytes) | `/setting` (`nflasha2`) read from the camera, E9201 | `c5735ad9c6da8c48fb24eeee0bb743d07c486a47d5db19bef13b5eaea24ad280` |
| `kemco.txt` (kernel parameters) | `/setting` read from the camera | not recorded here |

The device tree exists only on the camera (not in the update).

## The machine (`hw/arm/sony-a6700.c`, QEMU 10.0.0)

Everything comes from the camera's device tree: DRAM at `0x4_0000_0000`, GIC-400 (GICv2,
480 interrupts) at `0xF13A1000`, five `arm,pl011` UARTs from `0xF1000000` (SPI `0xDA`..),
generic timer at 2 MHz. The SoC windows `0xF0000000-0xF1FFFFFF` and `0xFE000000-0xFEFFFFFF`
are unimplemented devices that read as zero and log every access (`-d unimp`).

Boot contract observed from the image (offline and by running it):

- The `Image` starts at **EL3** (its first branch programs `VBAR_EL3`, `SCR_EL3`, `CPTR_EL3`,
  `ACTLR_EL3`, `CNTFRQ_EL0`, `HCR_EL2`, then `ERET`s to EL1). QEMU's `arm_load_kernel()`
  refuses AArch64 at EL3, so the machine loads the files itself and starts the CPU at the
  reset vector with a tiny trampoline that jumps to the Image.
- The image's own EL3 code passes the kernel the device tree at **`RAM_BASE + 0x2000`**
  (`0x4_0000_2000`); a DTB anywhere else is rejected ("invalid device tree blob").
- The kernel looks for a command-line override at `RAM_BASE + 0x1000` ("invalid cmdline
  override @ 0x400001000. Using default cmdline." when absent).
- An early `mrs/msr S3_1_C15_C2_1` touches Cortex-A35 `CPUECTLR_EL1`.

Licensing: `sony-a6700.c` is written against the QEMU 10.0.0 API and is released under
GPL-2.0-or-later like QEMU itself (SPDX header in the file); it is not part of the MIT-licensed
Python tooling. No QEMU source is copied into this repository.

## Sony GPL kernel source (hardware map)

Sony publishes the GPL source for this body: `linux-kernel.tar.gz` for ILCE-6700 Ver.2.00 at
<https://oss.sony.net/Products/Linux/DI/ILCE-6700_Ver200.html> (109,976,756 bytes, SHA-256
`fb39a21e67d6728e716adce36505f2b38ef8b37a76e3560025059371f373ec90`, downloaded under Sony's
notice, kept under `artifacts/sony-gpl/`, not in this repository). Its UDIF platform layer
(`drivers/udif/mach-cxd900xx`) gives the SoC map used by the machine: DDRC at `0xF105A000 +
0x1000*n` (INTSTAT is `+0x44C`), SCU at `0xF1388000` (16-byte DATA/SET/CLR groups), eSRAM at
`0xFE000000` (16 MiB), DMA330 interrupt bases, and the `memrsv=...:uc` non-cached windows.

## Boot contract (from the updater partition and `kemco.txt`)

`/boot/loadaddr.txt` on `nflasha1` reads `0x400080000,0x408000000,0x400001000,0x400002000`: kernel,
initrd, command-line blob (`kemco.txt`), device tree. `kemco.txt` carries the camera's real
parameters (`mem=4G`, `initrd=0x408000000,0x1000000`, `memrsv=...` incl. `:uc` windows,
`adrs_conv.*`, `console=ttynull0`). Without them the DMA module fails with `arch_phys_to_noncache`
errors; with them it loads.

## Build and run

```sh
sh emulators/qemu-a6700/build_qemu.sh /path/to/qemu-10.0.0        # tarball SHA-256 22c07560...3896
sh emulators/qemu-a6700/run_first_boot.sh /path/to/qemu-10.0.0/build /path/to/kernel-dir 30 /path/to/emmc.img
```

## Result (about 10 s of guest time, one CPU)

Reached, in order: EL3 to EL1 hand-off, device-tree parse, GIC probe, the CXD clock driver, the
2 MHz timer, five PL011 UARTs, USB PHY init bits, initrd ext2 root, the real `/sbin/init`, then
`devno.ko`, `dma330.ko` (both controllers), `crypt-emmc.ko`, `emmc.ko`, the encrypted nflasha7
reads and `pci_ep_cp.ko`. Contracts found by disassembling the modules and by tracing the bus
(each is an observation, not a model of the hardware):

- `crypt-emmc.ko` reads key bytes from `0xFFFFB800..` (fuse/OTP window: read-only zeros here, so
  the key is all zeros), uses a register block at `0xF10E0000` (status `+4` needs bits 18 and 20 set
  and bit 0 clear) and a 64 MiB PIO data window at `0xF8000000`. No AES-XTS is performed; data
  passes through. The block and window are stubs.
- The eMMC SDHCI is at `0xF10D6000` (size `0x2000`, `IRQ_EMMC0` = 208, SPI 176; Sony's GPL
  `platform.c` and `irqs.h`). The driver requires the ADMA2 capability and reads capabilities at
  `0x40`; the machine advertises `0x156CC8B0` (bit 28 is set because the driver only selects
  ADMA2-64 when 64-bit system addressing is advertised). The vendor block at `0xF10D7000` is not
  identified.
- The driver assumes the boot loader left the card initialised: it starts with CMD13 for RCA 1 and
  keeps `ocr = 0x40000000`. The machine replays that hand-off (CMD0, CMD1, CMD2, CMD3 with RCA 1,
  CMD7) once the VM starts.
- `patch_sd_emmc.py` also accepts the driver's CMD1 voltage window `0x80` and sets the capacity bit
  for eMMC; without that edit (previously skipped because of an idempotency bug) the driver's
  re-init timed out in CMD2. Each edit is now applied independently.
- ADMA descriptors are 12-byte ADMA2-64 entries (`{attr,length,addr64}`) at a **16-byte stride**
  (4 bytes padding); QEMU's stock 12-byte stride misread the second entry. The machine sets the
  opt-in `adma64-16byte` SDHCI property. Descriptor data addresses are full 64-bit values
  (e.g. table `0x4_1CB4E800`, data `0x4_08502E00`).
- PARTITION_CONFIG (EXT_CSD[179]) must satisfy `(value & ~7) == 0x08` (boot partition 1 enabled),
  otherwise the driver panics; the machine sets `boot-config` 0x08. The driver accepts EXT_CSD
  revisions 5..8 only (jump table in `set_emmc_spec`); the machine uses 8. QEMU hard-codes
  revision 3, so `patch_sd_emmc.py` adds an `ext-csd-rev` property to QEMU's `hw/sd/sd.c`. The
  camera's real EXT_CSD values are not read yet.
- Partition table: sector 0 of the user area, magic `8246`, version `2.00`, entries from `partinf.conf`
  (`build_emmc_image.py` builds a local image). With it the driver registers `pnflasha1..`.
- The local image must be larger than 2 GiB (the driver reports 4096 MiB); a 2 GiB image is exactly
  QEMU's SDSC limit and is addressed in bytes, so a sector-`0xE4002` read returned byte `0xE4002`.
- 1.8 V is advertised because the driver switches the bus power after the first data read.
- DMA330: `emmc.ko` starts a non-secure PL330 channel (microcode at `0x103B4C20`, the low 32 bits
  of a DRAM address) that moves data from the window to RAM and signals event 7. Both PL330
  controllers get the DRAM-aliased DMA view; `PNS`/`INS` allow non-secure peripherals/events.
- Result: the nflasha7 read (26 blocks at sector `0xE43CE`) completes; the window first word is
  `0x00000500` (ext2 superblock). nflasha7 mounts on `/rootfs`, `pivot_root` proceeds and init2
  reaches the `/ #` shell. The other fstab partitions are FAT (not encrypted); the update image
  simply lacks their data at some slots, and a fresh build plus the live camera's `/setting` FAT
  (which holds `BACKUP.BIN`/`BACKUP.BAK`) makes all mounts succeed. The guest md5 of
  `/setting/Backup.bin` matches the host.
- `backup.ko` passes its path check but fails `backup_init() error (file_load)`; `osal_ulogio.ko`
  then has unknown `Backup_*` symbols, libMWF reports `[ulogio] failed to load config from driver!`
  and `FixedAreaTop/Size: 0`, `/dev/mem` mmap fails, and `im.elf` SEGVs in
  `libMWF.so+0x409d0` (`MonQueue::GetQueueRemain`, `r0 = 0`). The backup header is at
  `0x400000004` (preloaded first page verified by `/dev/mem`); the missing piece is the
  bootloader/WBI fixed-area hand-off, not the eMMC read or the page mapping.
- The PCIe controller window `0xF6000000..0xF7FFFFFF` and the `pcie_i` config windows are logging
  read-zero scaffold devices. With them, `dw-pcie f7000000.pcie_i` creates a host bridge and
  `pcie_if`, `pcieatu`, `osal_uipc`, `osal_utm` and `ldec` load; init starts `im.elf` (pid 455,
  `libIMDB.so`, `libjemalloc_ds.so.2`, `ld-2.25.so`).

Not yet explained: `GIC CPU mask not found`; CPU 1-3 fail to come online (use `maxcpus=1`); the
`osal_set_cpuset_cpus` cpuset write returns `-28`.

## Next steps

1. Reconstruct the bootloader/WBI hand-off of the backup fixed area (the live `Backup.bin` header
   at `0x400000004`, `Backup_load=0`) so `backup.ko` initializes and `osal_ulogio` loads; this is
   what currently leaves libMWF's `MonQueue` NULL and makes `im.elf` SEGV.
2. Then identify the `im.elf` user-space fault at `libMWF.so+0x409d0`.
3. The PCIe register contract (the scaffold returns zero), then USB, display and sensor.
4. Secondary CPUs (spin-table) and the GIC CPU mask.
5. A USB or TCP debugger only after that: the repository's native debugger
   (`tools/native_camera/a6400_usb_debugd.c`) is ARM32 and written for the a6400.

The local eMMC image is built with
`python emulators/qemu-a6700/build_emmc_image.py partinf.conf images-dir emmc.img 4096` (inputs stay local).

## Scrubbed copies of the captured partitions

`scrub_dump.sh <captures-dir> <output-dir>` makes privacy-scrubbed COPIES for the owner's local
emulator use (the captured images are only mounted read-only). Each FAT partition is rebuilt
as a new image that keeps the generic files and none of the free-space remnants (old DHCP
leases, MAC addresses); the settings backup (owner name, serial number), the face-registration
data, the service token, the client certificate and private key, the network and access logs
and the updater history are left out or zero-filled, and the unreadable MPR backup is
zero-filled. A scan of the results finds no owner name, serial number, MAC address, lease,
private IP, private key or personal e-mail pattern. This is data hygiene, not a factory reset
of the camera and not a licence to redistribute Sony's firmware.
