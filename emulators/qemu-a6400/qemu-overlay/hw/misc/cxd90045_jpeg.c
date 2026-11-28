/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * ITU-T T.81 baseline JPEG decoder / ECS encoder (packed UYVY 4:2:2 & 4:2:0)
 * and Sony ARW 2.3 (cRAW2) 16-byte block codec for CXD90045 ZMA and IDT/RCRAW.
 */

#include "qemu/osdep.h"
#include "hw/misc/cxd90045_jpeg.h"
#include <math.h>

static const uint8_t jpeg_zigzag[64] = {
     0,  1,  8, 16,  9,  2,  3, 10,
    17, 24, 32, 25, 18, 11,  4,  5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13,  6,  7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
};

/* Standard ITU-T T.81 Annex K Huffman specifications (matched by av-cam 0x63a08950) */
static const uint8_t std_dc_luma_bits[16] = {
    0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0
};
static const uint8_t std_dc_luma_val[12] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
};
static const uint8_t std_dc_chroma_bits[16] = {
    0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0
};
static const uint8_t std_dc_chroma_val[12] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
};
static const uint8_t std_ac_luma_bits[16] = {
    0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d
};
static const uint8_t std_ac_luma_val[162] = {
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12,
    0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
    0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08,
    0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0,
    0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16,
    0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
    0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
    0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
    0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79,
    0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98,
    0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7,
    0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6,
    0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5,
    0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4,
    0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
    0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea,
    0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
    0xf9, 0xfa,
};
static const uint8_t std_ac_chroma_bits[16] = {
    0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77
};
static const uint8_t std_ac_chroma_val[162] = {
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21,
    0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
    0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91,
    0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0,
    0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34,
    0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
    0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38,
    0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
    0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58,
    0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
    0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78,
    0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96,
    0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
    0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4,
    0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3,
    0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2,
    0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
    0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9,
    0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
    0xf9, 0xfa,
};

typedef struct HuffDecTable {
    bool valid;
    int32_t maxcode[17];
    int32_t valoffset[17];
    uint8_t huffval[256];
} HuffDecTable;

typedef struct HuffEncTable {
    uint16_t code[256];
    uint8_t size[256];
} HuffEncTable;

static inline uint8_t clip_u8(int v)
{
    return v < 0 ? 0 : (v > 255 ? 255 : (uint8_t)v);
}

static void build_huff_dec(HuffDecTable *ht, const uint8_t bits[16],
                           const uint8_t *vals, unsigned nvals)
{
    memset(ht, 0, sizeof(*ht));
    if (nvals > 256) {
        return;
    }
    memcpy(ht->huffval, vals, nvals);
    int code = 0;
    int k = 0;
    for (int l = 1; l <= 16; l++) {
        if (bits[l - 1] == 0) {
            ht->maxcode[l] = -1;
        } else {
            ht->valoffset[l] = k - code;
            code += bits[l - 1];
            ht->maxcode[l] = code - 1;
            k += bits[l - 1];
        }
        code <<= 1;
    }
    ht->valid = true;
}

static void build_huff_enc(HuffEncTable *et, const uint8_t bits[16],
                           const uint8_t *vals, unsigned nvals)
{
    memset(et, 0, sizeof(*et));
    uint16_t code = 0;
    unsigned k = 0;
    for (unsigned l = 1; l <= 16; l++) {
        for (unsigned i = 0; i < bits[l - 1] && k < nvals; i++, k++) {
            uint8_t sym = vals[k];
            et->code[sym] = code++;
            et->size[sym] = l;
        }
        code <<= 1;
    }
}

/* Exact separable 8x8 IDCT / FDCT using precomputed cosine table */
static bool dct_tables_ready;
static float dct_cos[8][8];

static void ensure_dct_tables(void)
{
    if (dct_tables_ready) {
        return;
    }
    for (int u = 0; u < 8; u++) {
        float cu = (u == 0) ? (1.0f / sqrtf(2.0f)) : 1.0f;
        for (int x = 0; x < 8; x++) {
            dct_cos[u][x] = 0.5f * cu *
                            cosf(((2.0f * (float)x + 1.0f) * (float)u *
                                  (float)G_PI) / 16.0f);
        }
    }
    dct_tables_ready = true;
}

static void idct_8x8(const int16_t coeff[64], uint8_t out[64])
{
    float tmp[64];

    ensure_dct_tables();
    for (int y = 0; y < 8; y++) {
        for (int x = 0; x < 8; x++) {
            float s = 0.0f;
            for (int u = 0; u < 8; u++) {
                s += (float)coeff[y * 8 + u] * dct_cos[u][x];
            }
            tmp[y * 8 + x] = s;
        }
    }
    for (int x = 0; x < 8; x++) {
        for (int y = 0; y < 8; y++) {
            float s = 0.0f;
            for (int v = 0; v < 8; v++) {
                s += tmp[v * 8 + x] * dct_cos[v][y];
            }
            out[y * 8 + x] = clip_u8((int)lrintf(s + 128.0f));
        }
    }
}

static void fdct_8x8(const uint8_t in[64], const uint8_t q_zz[64],
                     int16_t zz_out[64])
{
    float tmp[64];
    float dct[64];

    ensure_dct_tables();
    for (int y = 0; y < 8; y++) {
        for (int u = 0; u < 8; u++) {
            float s = 0.0f;
            for (int x = 0; x < 8; x++) {
                s += ((float)in[y * 8 + x] - 128.0f) * dct_cos[u][x];
            }
            tmp[y * 8 + u] = s;
        }
    }
    for (int u = 0; u < 8; u++) {
        for (int v = 0; v < 8; v++) {
            float s = 0.0f;
            for (int y = 0; y < 8; y++) {
                s += tmp[y * 8 + u] * dct_cos[v][y];
            }
            dct[v * 8 + u] = s;
        }
    }
    for (int z = 0; z < 64; z++) {
        uint8_t q = q_zz[z] ? q_zz[z] : 1;
        int qval = (int)lrintf(dct[jpeg_zigzag[z]] / (float)q);
        if (qval < -1023) {
            qval = -1023;
        } else if (qval > 1023) {
            qval = 1023;
        }
        zz_out[z] = (int16_t)qval;
    }
}

typedef struct BitReader {
    const uint8_t *buf;
    size_t len;
    size_t pos;
    uint32_t bitbuf;
    int nbits;
    bool hit_marker;
    uint8_t marker;
} BitReader;

static int br_next_byte(BitReader *br)
{
    if (br->hit_marker) {
        return 0;
    }
    if (br->pos >= br->len) {
        return -1;
    }
    uint8_t b = br->buf[br->pos++];
    if (b == 0xFF) {
        while (br->pos < br->len && br->buf[br->pos] == 0xFF) {
            br->pos++;
        }
        if (br->pos >= br->len) {
            return -1;
        }
        uint8_t m = br->buf[br->pos++];
        if (m == 0x00) {
            return 0xFF;
        }
        br->hit_marker = true;
        br->marker = m;
        return 0;
    }
    return b;
}

static int br_get_bit(BitReader *br)
{
    if (br->nbits == 0) {
        int b = br_next_byte(br);
        if (b < 0) {
            return -1;
        }
        br->bitbuf = (uint32_t)b;
        br->nbits = 8;
    }
    br->nbits--;
    return (br->bitbuf >> br->nbits) & 1;
}

static int br_get_bits(BitReader *br, int n)
{
    int val = 0;
    for (int i = 0; i < n; i++) {
        int b = br_get_bit(br);
        if (b < 0) {
            return -1;
        }
        val = (val << 1) | b;
    }
    return val;
}

static int huff_decode_sym(BitReader *br, const HuffDecTable *ht)
{
    int32_t code = 0;
    for (int l = 1; l <= 16; l++) {
        int b = br_get_bit(br);
        if (b < 0) {
            return -1;
        }
        code = (code << 1) | b;
        if (code <= ht->maxcode[l]) {
            int idx = code + ht->valoffset[l];
            if (idx < 0 || idx >= 256) {
                return -1;
            }
            return ht->huffval[idx];
        }
    }
    return -1;
}

static inline int extend_val(int v, int nbits)
{
    if (nbits == 0) {
        return 0;
    }
    int vt = 1 << (nbits - 1);
    return (v < vt) ? (v - ((1 << nbits) - 1)) : v;
}

static bool decode_block_8x8(BitReader *br, const HuffDecTable *dc_ht,
                             const HuffDecTable *ac_ht, const uint8_t q_zz[64],
                             int *dc_pred, uint8_t pixels_out[64])
{
    int16_t block[64];
    memset(block, 0, sizeof(block));

    int t = huff_decode_sym(br, dc_ht);
    if (t < 0 || t > 11) {
        return false;
    }
    int diff = 0;
    if (t > 0) {
        int bits = br_get_bits(br, t);
        if (bits < 0) {
            return false;
        }
        diff = extend_val(bits, t);
    }
    *dc_pred += diff;
    block[0] = (int16_t)((*dc_pred) * (int)q_zz[0]);

    int k = 1;
    while (k < 64) {
        int rs = huff_decode_sym(br, ac_ht);
        if (rs < 0) {
            return false;
        }
        int r = rs >> 4;
        int s = rs & 0x0F;
        if (s == 0) {
            if (r == 15) {
                k += 16;
                continue;
            }
            break;
        }
        k += r;
        if (k >= 64) {
            return false;
        }
        int bits = br_get_bits(br, s);
        if (bits < 0) {
            return false;
        }
        int ac = extend_val(bits, s);
        block[jpeg_zigzag[k]] = (int16_t)(ac * (int)q_zz[k]);
        k++;
    }

    idct_8x8(block, pixels_out);
    return true;
}

bool cxd90045_jpeg_decode_to_uyvy(const uint8_t *jpeg, size_t jpeg_len,
                                  uint8_t *dst_uyvy, uint32_t dst_w,
                                  uint32_t dst_h, uint32_t dst_stride_bytes,
                                  const Cxd90045JpegCrop *crop)
{
    if (!jpeg || jpeg_len < 4 || !dst_uyvy || !dst_w || (dst_w & 1) ||
        !dst_h || dst_stride_bytes < dst_w * 2) {
        return false;
    }
    if (jpeg[0] != 0xFF || jpeg[1] != 0xD8) {
        return false;
    }

    uint8_t qtables[4][64];
    bool qvalid[4] = { false, false, false, false };
    HuffDecTable dc_ht[4], ac_ht[4];
    memset(dc_ht, 0, sizeof(dc_ht));
    memset(ac_ht, 0, sizeof(ac_ht));
    /* Seed standard Annex K defaults in case stream omits DHT */
    build_huff_dec(&dc_ht[0], std_dc_luma_bits, std_dc_luma_val, 12);
    build_huff_dec(&dc_ht[1], std_dc_chroma_bits, std_dc_chroma_val, 12);
    build_huff_dec(&ac_ht[0], std_ac_luma_bits, std_ac_luma_val, 162);
    build_huff_dec(&ac_ht[1], std_ac_chroma_bits, std_ac_chroma_val, 162);

    uint16_t src_w = 0, src_h = 0, dri = 0;
    uint8_t comp_id[3] = { 0 }, comp_hv[3] = { 0 }, comp_qt[3] = { 0 };
    uint8_t comp_dc[3] = { 0, 1, 1 }, comp_ac[3] = { 0, 1, 1 };
    bool got_sof = false, got_sos = false;
    size_t pos = 2;

    while (pos + 2 <= jpeg_len && !got_sos) {
        if (jpeg[pos] != 0xFF) {
            pos++;
            continue;
        }
        while (pos < jpeg_len && jpeg[pos] == 0xFF) {
            pos++;
        }
        if (pos >= jpeg_len) {
            return false;
        }
        uint8_t marker = jpeg[pos++];
        if (marker == 0xD9) {
            return false;
        }
        if (marker == 0xD8 || (marker >= 0xD0 && marker <= 0xD7) ||
            marker == 0x01) {
            continue;
        }
        if (pos + 2 > jpeg_len) {
            return false;
        }
        uint16_t seg_len = ((uint16_t)jpeg[pos] << 8) | jpeg[pos + 1];
        if (seg_len < 2 || pos + seg_len > jpeg_len) {
            return false;
        }
        const uint8_t *seg = jpeg + pos + 2;
        size_t pay_len = seg_len - 2;

        if (marker == 0xDB) { /* DQT */
            size_t off = 0;
            while (off < pay_len) {
                uint8_t pq_tq = seg[off++];
                uint8_t pq = pq_tq >> 4;
                uint8_t tq = pq_tq & 0x0F;
                if (pq != 0 || tq >= 4 || off + 64 > pay_len) {
                    return false;
                }
                memcpy(qtables[tq], seg + off, 64);
                qvalid[tq] = true;
                off += 64;
            }
        } else if (marker == 0xC0) { /* Baseline SOF0 */
            if (pay_len < 15 || seg[0] != 8 || seg[5] != 3) {
                return false;
            }
            src_h = ((uint16_t)seg[1] << 8) | seg[2];
            src_w = ((uint16_t)seg[3] << 8) | seg[4];
            for (int c = 0; c < 3; c++) {
                comp_id[c] = seg[6 + 3 * c];
                comp_hv[c] = seg[7 + 3 * c];
                comp_qt[c] = seg[8 + 3 * c];
                if (comp_qt[c] >= 4) {
                    return false;
                }
            }
            if ((comp_hv[0] != 0x21 && comp_hv[0] != 0x22 && comp_hv[0] != 0x11) ||
                comp_hv[1] != 0x11 || comp_hv[2] != 0x11) {
                return false;
            }
            got_sof = (src_w > 0 && src_h > 0 && src_w <= 8192 && src_h <= 8192);
        } else if (marker == 0xC4) { /* DHT */
            size_t off = 0;
            while (off + 17 <= pay_len) {
                uint8_t tc_th = seg[off++];
                uint8_t tc = tc_th >> 4;
                uint8_t th = tc_th & 0x0F;
                if (tc > 1 || th >= 4) {
                    return false;
                }
                const uint8_t *bits = seg + off;
                off += 16;
                unsigned total = 0;
                for (int i = 0; i < 16; i++) {
                    total += bits[i];
                }
                if (total > 256 || off + total > pay_len) {
                    return false;
                }
                if (tc == 0) {
                    build_huff_dec(&dc_ht[th], bits, seg + off, total);
                } else {
                    build_huff_dec(&ac_ht[th], bits, seg + off, total);
                }
                off += total;
            }
        } else if (marker == 0xDD) { /* DRI */
            if (pay_len != 2) {
                return false;
            }
            dri = ((uint16_t)seg[0] << 8) | seg[1];
        } else if (marker == 0xDA) { /* SOS */
            if (!got_sof || pay_len < 10 || seg[0] != 3) {
                return false;
            }
            for (int j = 0; j < 3; j++) {
                uint8_t cs = seg[1 + 2 * j];
                uint8_t td_ta = seg[2 + 2 * j];
                int match = -1;
                for (int c = 0; c < 3; c++) {
                    if (comp_id[c] == cs) {
                        match = c;
                        break;
                    }
                }
                if (match < 0) {
                    return false;
                }
                comp_dc[match] = td_ta >> 4;
                comp_ac[match] = td_ta & 0x0F;
                if (comp_dc[match] >= 4 || comp_ac[match] >= 4) {
                    return false;
                }
            }
            got_sos = true;
        }
        pos += seg_len;
    }

    if (!got_sof || !got_sos) {
        return false;
    }
    for (int c = 0; c < 3; c++) {
        if (!qvalid[comp_qt[c]] || !dc_ht[comp_dc[c]].valid ||
            !ac_ht[comp_ac[c]].valid) {
            return false;
        }
    }

    uint32_t h_samp = comp_hv[0] >> 4;
    uint32_t v_samp = comp_hv[0] & 0x0F;
    uint32_t mcu_w = h_samp * 8;
    uint32_t mcu_h = v_samp * 8;
    uint32_t mcus_x = (src_w + mcu_w - 1) / mcu_w;
    uint32_t mcus_y = (src_h + mcu_h - 1) / mcu_h;
    uint32_t pad_w = mcus_x * mcu_w;
    uint32_t pad_h = mcus_y * mcu_h;

    g_autofree uint8_t *plane_y = g_malloc0((size_t)pad_w * pad_h);
    g_autofree uint8_t *plane_cb = g_malloc0((size_t)pad_w * pad_h);
    g_autofree uint8_t *plane_cr = g_malloc0((size_t)pad_w * pad_h);

    BitReader br = {
        .buf = jpeg,
        .len = jpeg_len,
        .pos = pos,
        .bitbuf = 0,
        .nbits = 0,
        .hit_marker = false,
        .marker = 0,
    };
    int dc_pred[3] = { 0, 0, 0 };
    uint32_t mcu_idx = 0;

    for (uint32_t my = 0; my < mcus_y; my++) {
        for (uint32_t mx = 0; mx < mcus_x; mx++) {
            if (dri > 0 && mcu_idx > 0 && (mcu_idx % dri) == 0) {
                br.nbits = 0;
                if (!br.hit_marker) {
                    while (br.pos < br.len && br.buf[br.pos] != 0xFF) {
                        br.pos++;
                    }
                    while (br.pos < br.len && br.buf[br.pos] == 0xFF) {
                        br.pos++;
                    }
                    if (br.pos < br.len) {
                        br.marker = br.buf[br.pos++];
                    }
                }
                br.hit_marker = false;
                dc_pred[0] = dc_pred[1] = dc_pred[2] = 0;
            }

            uint8_t y_blk[4][64];
            uint8_t cb_blk[64], cr_blk[64];
            unsigned y_blocks = h_samp * v_samp;
            for (unsigned b = 0; b < y_blocks; b++) {
                if (!decode_block_8x8(&br, &dc_ht[comp_dc[0]],
                                      &ac_ht[comp_ac[0]], qtables[comp_qt[0]],
                                      &dc_pred[0], y_blk[b])) {
                    return false;
                }
            }
            if (!decode_block_8x8(&br, &dc_ht[comp_dc[1]], &ac_ht[comp_ac[1]],
                                  qtables[comp_qt[1]], &dc_pred[1], cb_blk) ||
                !decode_block_8x8(&br, &dc_ht[comp_dc[2]], &ac_ht[comp_ac[2]],
                                  qtables[comp_qt[2]], &dc_pred[2], cr_blk)) {
                return false;
            }

            for (uint32_t py = 0; py < mcu_h; py++) {
                for (uint32_t px = 0; px < mcu_w; px++) {
                    unsigned bx = px / 8;
                    unsigned by = py / 8;
                    uint8_t yv = y_blk[by * h_samp + bx][(py & 7) * 8 + (px & 7)];
                    uint8_t cbv = cb_blk[(py / v_samp) * 8 + (px / h_samp)];
                    uint8_t crv = cr_blk[(py / v_samp) * 8 + (px / h_samp)];
                    size_t idx = (size_t)(my * mcu_h + py) * pad_w + (mx * mcu_w + px);
                    plane_y[idx] = yv;
                    plane_cb[idx] = cbv;
                    plane_cr[idx] = crv;
                }
            }
            mcu_idx++;
        }
    }

    uint32_t cx = 0, cy = 0, cw = src_w, ch_h = src_h;
    if (crop && crop->enabled && crop->w > 0 && crop->h > 0 &&
        (uint32_t)crop->x + crop->w <= src_w &&
        (uint32_t)crop->y + crop->h <= src_h) {
        cx = crop->x;
        cy = crop->y;
        cw = crop->w;
        ch_h = crop->h;
    }

    for (uint32_t dy = 0; dy < dst_h; dy++) {
        uint32_t sy = cy + (uint32_t)(((uint64_t)dy * ch_h) / dst_h);
        uint8_t *out_row = dst_uyvy + (size_t)dy * dst_stride_bytes;
        for (uint32_t dx = 0; dx < dst_w; dx += 2) {
            uint32_t sx0 = cx + (uint32_t)(((uint64_t)dx * cw) / dst_w);
            uint32_t sx1 = cx + (uint32_t)(((uint64_t)(dx + 1) * cw) / dst_w);
            size_t i0 = (size_t)sy * pad_w + sx0;
            size_t i1 = (size_t)sy * pad_w + sx1;
            out_row[dx * 2 + 0] = (uint8_t)(((unsigned)plane_cb[i0] + plane_cb[i1]) >> 1);
            out_row[dx * 2 + 1] = plane_y[i0];
            out_row[dx * 2 + 2] = (uint8_t)(((unsigned)plane_cr[i0] + plane_cr[i1]) >> 1);
            out_row[dx * 2 + 3] = plane_y[i1];
        }
    }
    return true;
}

typedef struct BitWriter {
    GByteArray *arr;
    uint32_t bitbuf;
    int nbits;
} BitWriter;

static void bw_put_bits(BitWriter *bw, uint16_t bits, uint8_t n)
{
    if (n == 0) {
        return;
    }
    bw->bitbuf = (bw->bitbuf << n) | (bits & ((1U << n) - 1U));
    bw->nbits += n;
    while (bw->nbits >= 8) {
        bw->nbits -= 8;
        uint8_t b = (bw->bitbuf >> bw->nbits) & 0xFFU;
        g_byte_array_append(bw->arr, &b, 1);
        if (b == 0xFFU) {
            uint8_t zero = 0x00;
            g_byte_array_append(bw->arr, &zero, 1);
        }
    }
}

static void bw_flush_pad1(BitWriter *bw)
{
    if (bw->nbits > 0) {
        uint8_t pad_len = (uint8_t)(8 - bw->nbits);
        bw_put_bits(bw, (uint16_t)((1U << pad_len) - 1U), pad_len);
    }
}

static inline uint8_t coeff_category(int v, uint16_t *mag_bits)
{
    if (v == 0) {
        *mag_bits = 0;
        return 0;
    }
    int a = v < 0 ? -v : v;
    uint8_t cat = 0;
    while (a > 0) {
        a >>= 1;
        cat++;
    }
    *mag_bits = (v > 0) ? (uint16_t)v : (uint16_t)(v + (1 << cat) - 1);
    return cat;
}

static void encode_block_8x8(BitWriter *bw, const uint8_t blk[64],
                             const uint8_t q_zz[64], const HuffEncTable *dc_et,
                             const HuffEncTable *ac_et, int *dc_pred)
{
    int16_t zz[64];
    fdct_8x8(blk, q_zz, zz);

    int diff = (int)zz[0] - *dc_pred;
    *dc_pred = zz[0];
    uint16_t mbits = 0;
    uint8_t cat = coeff_category(diff, &mbits);
    bw_put_bits(bw, dc_et->code[cat], dc_et->size[cat]);
    bw_put_bits(bw, mbits, cat);

    int zero_run = 0;
    for (int k = 1; k < 64; k++) {
        int v = zz[k];
        if (v == 0) {
            zero_run++;
            continue;
        }
        while (zero_run >= 16) {
            bw_put_bits(bw, ac_et->code[0xF0], ac_et->size[0xF0]);
            zero_run -= 16;
        }
        cat = coeff_category(v, &mbits);
        uint8_t rs = (uint8_t)((zero_run << 4) | cat);
        bw_put_bits(bw, ac_et->code[rs], ac_et->size[rs]);
        bw_put_bits(bw, mbits, cat);
        zero_run = 0;
    }
    if (zero_run > 0) {
        bw_put_bits(bw, ac_et->code[0x00], ac_et->size[0x00]); /* EOB */
    }
}

uint8_t *cxd90045_jpeg_encode_uyvy_ecs(const uint8_t *src_uyvy,
                                       uint32_t width, uint32_t height,
                                       uint32_t src_stride_bytes,
                                       bool is_420, uint16_t dri_mcus,
                                       const uint8_t q_luma[64],
                                       const uint8_t q_chroma[64],
                                       size_t *out_len)
{
    if (!src_uyvy || !width || (width & 1) || !height ||
        src_stride_bytes < width * 2 || !q_luma || !q_chroma || !out_len) {
        return NULL;
    }

    HuffEncTable dc_y, dc_c, ac_y, ac_c;
    build_huff_enc(&dc_y, std_dc_luma_bits, std_dc_luma_val, 12);
    build_huff_enc(&dc_c, std_dc_chroma_bits, std_dc_chroma_val, 12);
    build_huff_enc(&ac_y, std_ac_luma_bits, std_ac_luma_val, 162);
    build_huff_enc(&ac_c, std_ac_chroma_bits, std_ac_chroma_val, 162);

    uint32_t mcu_w = 16;
    uint32_t mcu_h = is_420 ? 16 : 8;
    uint32_t mcus_x = (width + mcu_w - 1) / mcu_w;
    uint32_t mcus_y = (height + mcu_h - 1) / mcu_h;

    BitWriter bw = {
        .arr = g_byte_array_sized_new((width * height) / 2 + 256),
        .bitbuf = 0,
        .nbits = 0,
    };
    int dc_pred[3] = { 0, 0, 0 };
    uint32_t mcu_idx = 0;
    uint8_t rst_num = 0;

    for (uint32_t my = 0; my < mcus_y; my++) {
        for (uint32_t mx = 0; mx < mcus_x; mx++) {
            if (dri_mcus > 0 && mcu_idx > 0 && (mcu_idx % dri_mcus) == 0) {
                bw_flush_pad1(&bw);
                uint8_t rst_marker[2] = { 0xFF, (uint8_t)(0xD0U + (rst_num & 7U)) };
                g_byte_array_append(bw.arr, rst_marker, 2);
                rst_num++;
                dc_pred[0] = dc_pred[1] = dc_pred[2] = 0;
            }

            uint8_t y_blk[4][64], cb_blk[64], cr_blk[64];
            unsigned v_samp = is_420 ? 2 : 1;
            for (unsigned by = 0; by < v_samp; by++) {
                for (unsigned bx = 0; bx < 2; bx++) {
                    uint8_t *yb = y_blk[by * 2 + bx];
                    for (unsigned py = 0; py < 8; py++) {
                        uint32_t sy = MIN(my * mcu_h + by * 8 + py, height - 1);
                        const uint8_t *row = src_uyvy + (size_t)sy * src_stride_bytes;
                        for (unsigned px = 0; px < 8; px++) {
                            uint32_t sx = MIN(mx * 16 + bx * 8 + px, width - 1);
                            yb[py * 8 + px] = row[(sx & ~1U) * 2 + ((sx & 1U) ? 3 : 1)];
                        }
                    }
                }
            }
            for (unsigned cy = 0; cy < 8; cy++) {
                uint32_t sy = MIN(my * mcu_h + cy * v_samp, height - 1);
                const uint8_t *row = src_uyvy + (size_t)sy * src_stride_bytes;
                for (unsigned cx = 0; cx < 8; cx++) {
                    uint32_t sx = MIN(mx * 16 + cx * 2, width - 2);
                    cb_blk[cy * 8 + cx] = row[sx * 2 + 0];
                    cr_blk[cy * 8 + cx] = row[sx * 2 + 2];
                }
            }

            for (unsigned b = 0; b < v_samp * 2; b++) {
                encode_block_8x8(&bw, y_blk[b], q_luma, &dc_y, &ac_y,
                                 &dc_pred[0]);
            }
            encode_block_8x8(&bw, cb_blk, q_chroma, &dc_c, &ac_c, &dc_pred[1]);
            encode_block_8x8(&bw, cr_blk, q_chroma, &dc_c, &ac_c, &dc_pred[2]);
            mcu_idx++;
        }
    }

    bw_flush_pad1(&bw);
    uint8_t eoi[2] = { 0xFF, 0xD9 };
    g_byte_array_append(bw.arr, eoi, 2);

    *out_len = bw.arr->len;
    return g_byte_array_free(bw.arr, FALSE);
}

/*
 * Sony ARW 2.3 (cRAW2, Compression=32767) 128-bit (16-byte) block format
 * (matches rawspeed SonyArw2Decompressor):
 *   bits [10:0]   : 11-bit max (pixels >> 3 or clamped 11-bit curve domain)
 *   bits [21:11]  : 11-bit min
 *   bits [25:22]  : 4-bit imax index (0..15)
 *   bits [29:26]  : 4-bit imin index (0..15, != imax)
 *   bits [127:30] : 14 x 7-bit unsigned deltas ((p - min) >> sh), sh in 0..4
 */
void cxd90045_craw2_pack_block16(const uint16_t pixels14[16],
                                 uint8_t out_block[16])
{
    uint16_t p11[16];
    uint16_t max_v = 0, min_v = 0x7FF;
    unsigned imax = 0, imin = 1;

    for (unsigned i = 0; i < 16; i++) {
        uint16_t v = (pixels14[i] >> 3) & 0x7FFU;
        p11[i] = v;
        if (i == 0 || v > max_v) {
            max_v = v;
            imax = i;
        }
    }
    imin = (imax == 0) ? 1 : 0;
    min_v = p11[imin];
    for (unsigned i = 0; i < 16; i++) {
        if (i != imax && p11[i] < min_v) {
            min_v = p11[i];
            imin = i;
        }
    }

    uint16_t span = max_v - min_v;
    unsigned sh = 0;
    while (sh < 4 && (span >> sh) > 127) {
        sh++;
    }

    memset(out_block, 0, 16);
    uint32_t hdr = (uint32_t)(max_v & 0x7FFU) |
                   ((uint32_t)(min_v & 0x7FFU) << 11) |
                   ((uint32_t)(imax & 0xFU) << 22) |
                   ((uint32_t)(imin & 0xFU) << 26);
    out_block[0] = hdr & 0xFFU;
    out_block[1] = (hdr >> 8) & 0xFFU;
    out_block[2] = (hdr >> 16) & 0xFFU;
    out_block[3] = (hdr >> 24) & 0x3FU;

    unsigned bitpos = 30;
    for (unsigned i = 0; i < 16; i++) {
        if (i == imax || i == imin) {
            continue;
        }
        uint32_t d = ((uint32_t)(p11[i] - min_v) >> sh) & 0x7FU;
        unsigned byte_idx = bitpos >> 3;
        unsigned bit_off = bitpos & 7U;
        out_block[byte_idx] |= (uint8_t)(d << bit_off);
        if (bit_off + 7U > 8U) {
            out_block[byte_idx + 1] |= (uint8_t)(d >> (8U - bit_off));
        }
        bitpos += 7U;
    }
}

void cxd90045_craw2_unpack_block16(const uint8_t in_block[16],
                                   uint16_t pixels14[16])
{
    uint32_t hdr = (uint32_t)in_block[0] |
                   ((uint32_t)in_block[1] << 8) |
                   ((uint32_t)in_block[2] << 16) |
                   ((uint32_t)in_block[3] << 24);
    uint16_t max_v = hdr & 0x7FFU;
    uint16_t min_v = (hdr >> 11) & 0x7FFU;
    unsigned imax = (hdr >> 22) & 0xFU;
    unsigned imin = (hdr >> 26) & 0xFU;

    uint16_t span = (max_v >= min_v) ? (max_v - min_v) : 0;
    unsigned sh = 0;
    while (sh < 4 && (span >> sh) > 127) {
        sh++;
    }

    unsigned bitpos = 30;
    for (unsigned i = 0; i < 16; i++) {
        uint16_t v11;
        if (i == imax) {
            v11 = max_v;
        } else if (i == imin) {
            v11 = min_v;
        } else {
            unsigned byte_idx = bitpos >> 3;
            unsigned bit_off = bitpos & 7U;
            uint16_t raw16 = (uint16_t)in_block[byte_idx] |
                             ((uint16_t)in_block[byte_idx + 1] << 8);
            uint16_t d = (raw16 >> bit_off) & 0x7FU;
            v11 = MIN(0x7FFU, (uint16_t)(min_v + (d << sh)));
            bitpos += 7U;
        }
        pixels14[i] = (uint16_t)(v11 << 3);
    }
}
