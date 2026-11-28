/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * From voxivoid/a6000-qemu (commit 62cf3bc)
 *   qemu-overlay/include/hw/dma/cxd90014_mdma.h,
 * ported to QEMU 11.1.3.  voxivoid/a6000-qemu adapts
 * Oein/a5000-emulator (GPL-2.0, Park SungHyun; base commit 6ad36bd) to
 * the ILCE-6000.  The original description follows; its docs/reference/
 * paths are files in voxivoid/a6000-qemu.
 */
/*
 * CXD90014 av-cam memory DMA: peripheral-paced channels.
 * A channel whose source or destination equals the registered peripheral
 * FIFO address (UART1 DR for the E-mount lens) does not copy memory; the
 * peer device produces / consumes the bytes (docs/reference/RE_LENS_EMOUNT.md 9.4).
 */
#ifndef HW_DMA_CXD90014_MDMA_H
#define HW_DMA_CXD90014_MDMA_H

#include "hw/core/qdev.h"

/* Called when a channel with dst == fifo starts: the bytes it sends. */
typedef void (*CxdMdmaPeriphTx)(void *opaque, const uint8_t *buf, size_t len);

/*
 * Called when the guest reads the armed RX channel's +0x04 (progress):
 * the peer may call cxd90014_mdma_periph_rx for bytes that are due.
 */
typedef void (*CxdMdmaPeriphPull)(void *opaque);

void cxd90014_mdma_set_periph(DeviceState *dev, uint32_t fifo,
                              uint32_t ns_per_byte,
                              CxdMdmaPeriphTx tx, CxdMdmaPeriphPull pull,
                              void *opaque);

/*
 * Feed bytes received by the peripheral into the armed channel whose
 * src == fifo.  Returns how many were taken (0 when nothing is armed).
 */
size_t cxd90014_mdma_periph_rx(DeviceState *dev, const uint8_t *buf,
                               size_t len);

#endif
