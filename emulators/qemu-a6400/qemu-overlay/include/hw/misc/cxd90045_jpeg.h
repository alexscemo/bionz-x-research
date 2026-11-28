/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Self-contained ITU-T T.81 baseline JPEG codec (SOF0 4:2:2 / 4:2:0 <-> packed
 * UYVY) and Sony ARW 2.3 (cRAW2 16-byte delta block) packer for CXD90045 ZMA
 * DGI op 0x11 (decode), op 0x1B (encode ECS), and 0xF3030000 IDT/RCRAW.
 */
#ifndef HW_MISC_CXD90045_JPEG_H
#define HW_MISC_CXD90045_JPEG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct Cxd90045JpegCrop {
    bool enabled;
    uint16_t x;
    uint16_t y;
    uint16_t w;
    uint16_t h;
} Cxd90045JpegCrop;

/*
 * Decode a baseline 8-bit 3-component (YCbCr 4:2:2 or 4:2:0) JPEG image from
 * [jpeg, jpeg + jpeg_len) into packed UYVY (Cb0, Y0, Cr0, Y1) at [dst_uyvy]
 * of dimensions dst_w x dst_h and row stride dst_stride_bytes.
 */
bool cxd90045_jpeg_decode_to_uyvy(const uint8_t *jpeg, size_t jpeg_len,
                                  uint8_t *dst_uyvy, uint32_t dst_w,
                                  uint32_t dst_h, uint32_t dst_stride_bytes,
                                  const Cxd90045JpegCrop *crop);

/*
 * Encode packed UYVY [src_uyvy] (width x height, row stride src_stride_bytes)
 * into a baseline JPEG entropy-coded scan (ECS) + FF D9 (EOI) using the
 * caller-supplied zig-zag quantization tables q_luma[64] and q_chroma[64],
 * standard ITU-T T.81 Annex K Huffman tables, and optional DRI restart
 * interval (dri_mcus). Returns malloc'd buffer via g_malloc and byte count in
 * *out_len, or NULL on invalid dimensions.
 */
uint8_t *cxd90045_jpeg_encode_uyvy_ecs(const uint8_t *src_uyvy,
                                       uint32_t width, uint32_t height,
                                       uint32_t src_stride_bytes,
                                       bool is_420, uint16_t dri_mcus,
                                       const uint8_t q_luma[64],
                                       const uint8_t q_chroma[64],
                                       size_t *out_len);

/*
 * Pack one 16-pixel group of 14-bit unsigned Bayer values into a 16-byte
 * (128-bit) Sony ARW 2.3 (cRAW2, Compression=32767) compressed block.
 */
void cxd90045_craw2_pack_block16(const uint16_t pixels14[16],
                                 uint8_t out_block[16]);

/*
 * Unpack one 16-byte Sony ARW 2.3 (cRAW2) block back into 16 x 14-bit pixels.
 */
void cxd90045_craw2_unpack_block16(const uint8_t in_block[16],
                                   uint16_t pixels14[16]);

#endif /* HW_MISC_CXD90045_JPEG_H */
