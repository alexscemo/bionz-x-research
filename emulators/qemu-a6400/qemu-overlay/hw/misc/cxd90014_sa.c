/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Adapted for sony-a6400 from Oein/a5000-emulator hw/misc/cxd90014_sa.c
 * (GPL-2.0, Park SungHyun).  The a6400 av-cam.bin contains the same four
 * core ID strings and the same base addresses.  Trace points are stderr
 * lines for the first few starts.
 */

/*
 * CXD90014 SA DSP core, "null SA" model (a5000, fw 1.10).
 *
 * Spec: docs/RE_SA_DSP.md §5 (context: RE_LIVEVIEW_REQUEST.md §3-§4,
 * RE_SD_MEDIA_SERVICE.md §3.3 / §7.2).  Addresses below are av-cam.bin
 * VAs (load address 0x43541000).
 *
 * Four instances, one per core (av-cam accessors 43923E74..43924660):
 *   core 0  0xF34A0000  IRQ A/B/C = GIC 51 / 52 / 53  ID "ZMA0V1.0SA5_V1.0"
 *   core 1  0xF34B0000  IRQ A/B/C = GIC 54 / 55 / 56  ID "ZMA1V1.0SA5_V1.0"
 *   core 2  0xF34C0000  IRQ A/B/C = GIC 61 / 62 / 63  ID "SA0_V1.0SA5_V1.0"
 *   core 3  0xF35C0000  IRQ A/B/C = GIC 64 / 65 / 66  ID "SA1_V1.0SA5_V1.0"
 * Region size 0x1000 (hypothesis: the highest offset av-cam uses is +0x22C).
 * The clock/reset block 0xF3400000 stays on the catch-all register file and
 * does not gate this model (RE_SA_DSP §5.1, [?] which register gates).
 *
 * Registers (per core):
 *   +0x000         RW latch, bit0 = run enable (43923E74)
 *   +0x004         RW latch, bit0 = GO (43923EE0).  Writing bit0 = 1 while
 *                  +0x000 bit0 = 1 starts a "function" (below).  Bit0 reads
 *                  1 while running and 0 after completion (hypothesis: the
 *                  read site 43923EAC was not traced; 0 after completion is
 *                  the safe choice, RE_SA_DSP §5.2)
 *   +0x008 +0x00C  RW latches (+0x008 = 0x80000000 for DRAM programs,
 *   +0x01C +0x030  43923F18; +0x030 = program entry PA, 43924070)
 *   +0x084 +0x088  read 0 (only printed with the result, 439240A0/D4)
 *   +0x0F0..+0x0FF read-only 16-byte core ID, compared at task-44 init
 *                  (4391B334); 4 little-endian words of the ASCII string
 *   +0x100/140/200 source A/B/C status byte: read 0 (ISR 4391A4C4 copies it
 *                  into the event)
 *   +0x104/144/204 source A/B/C interrupt factor (byte).  Write = clear the
 *                  written bits (W1C): the ISR writes back the value it read,
 *                  the arm step 43925FB4 writes 0xFF.  The 0xFF must not read
 *                  back, or the 0x41 result carries int_fact 0xFF and ZIT
 *                  rejects it (RE_SA_DSP §5.2 pitfall)
 *   +0x108/148/208 source A/B/C factor enable mask (byte), RW latch.  Arming
 *                  writes 0xFF, the ISR writes 0 (43924214)
 *   +0x110..+0x11C, +0x150..+0x15C, +0x210..+0x21C
 *                  host->SA mailboxes A/B/C, RW latches (43924284)
 *   +0x120..+0x12C, +0x160..+0x16C, +0x220..+0x22C
 *                  SA->host mailboxes A/B/C: read 0 (copied into the 0x41
 *                  result by 43AC864E, only printed on error)
 *   anything else  RW latch + LOG_UNIMP
 * IRQ line X = (factor_X & mask_X & 0xFF) != 0, level (RE_SA_DSP §1.3).
 *
 * Start behaviour (HLE, AI_WORKFLOW §1.5: the SA program ISA is unknown):
 * a GO write logs the entry PA, +0x008 and the source-A host->SA mailbox
 * (trace event cxd_sa_start plus a LOG_UNIMP line, so later SA clients can
 * be identified), then arms a one-shot timer.  The completion cannot happen
 * inside the MMIO write: the ISR posts to SA manager task 44, which is the
 * task doing the write.  On expiry factor A |= 0x01 ("finished": int_fact 1,
 * SDF WrapSaCalcBase 436532D0, ZIT 4398EA3C) and GO bit0 clears.  +0x000 is
 * left as written (hypothesis, RE_SA_DSP §5.3 [?]).  Sources B and C are
 * wired but never raised. Every result message is built by av-cam
 * (4391D3BC). The opt-in a6400 ZMA1 startup handshake recognizes one
 * original boot program. Its separate JPEG fill operation only initializes
 * the requested output surface and reports through the existing SA IRQ.
 *
 * Open questions (RE_SA_DSP §7): real run time (the 100 us default is a
 * placeholder), semantics of +0x004 read-back, +0x008/+0x00C/+0x01C,
 * +0x084/+0x088 and the status bytes; which clients use sources B/C.
 */

#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "hw/misc/cxd90045_jpeg.h"
#include "qapi/error.h"
#include "qemu/rcu.h"
#include "qemu/timer.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "system/address-spaces.h"
#include "qom/object.h"

#define TYPE_CXD_SA "cxd90014-sa"
OBJECT_DECLARE_SIMPLE_TYPE(CxdSaState, CXD_SA)

#define SA_SIZE     0x1000
#define SA_NSRC     3               /* sources A, B, C */
#define SA_IDLEN    16

#define SA_RUN      0x000
#define SA_GO       0x004
#define SA_PROG     0x008
#define SA_SEND_A   0x00C
#define SA_ENTRY    0x030
#define SA_ID       0x0F0
#define SA_MBOX_A   0x110
#define SA_ZMA1_BOOT_FACTOR  0x80
#define SA_ZMA_DECODE_CMD    0x00001100
#define SA_ZMA1_FILL_CMD     0x00001300
#define SA_ZMA_ENCODE_CMD    0x00001B00
#define SA_ZMA1_FILL_BYTES   0x180
#define SA_ZMA1_STARTUP_CODE_BYTES 2692
#define SA_ZMA0_STARTUP_CODE_SHA256 \
    "83a3e61b07c48dc5ea132352da8763a742fded71a60eceedc735f02b3c0986c9"
#define SA_ZMA1_STARTUP_CODE_SHA256 \
    "46a92d9fc1c715a13477f9792c623ca633598b6595978ad761551570e31eaff4"

/* per-source register block: A = +0x100, B = +0x140, C = +0x200 */
static const hwaddr sa_src_base[SA_NSRC] = { 0x100, 0x140, 0x200 };

struct CxdSaState {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    QEMUTimer *done;
    qemu_irq irq[SA_NSRC];

    uint32_t r[SA_SIZE / 4];        /* latches */
    uint32_t mbox_out_a[4];         /* SA->host mailbox A (+0x120..+0x12C) */
    uint8_t factor[SA_NSRC];
    uint8_t mask[SA_NSRC];
    uint8_t completion_factor;
    bool running;
    bool zma1_ready;
    uint32_t pending_op;            /* 0x1100, 0x1300, 0x1B00, or movie op */
    uint32_t fill_desc;
    uint32_t starts;
    uint32_t venc_es_pa;
    uint32_t venc_es_size;
    uint32_t venc_es_wp;
    uint32_t venc_frames;

    /* properties */
    uint32_t index;
    uint64_t base;                  /* log only */
    char *core_id;
    uint32_t delay_us;
    bool zma1_startup;
    bool zma1_jpeg_fill;
    bool zma1_jpeg_decode;
    bool movie_codec_hle;
    uint8_t id[SA_IDLEN];
};

#define R(o) (s->r[(o) / 4])

static void sa_update(CxdSaState *s)
{
    for (int i = 0; i < SA_NSRC; i++) {
        qemu_set_irq(s->irq[i], (s->factor[i] & s->mask[i]) != 0);
    }
}

/* Classify a word offset: source index and register within it, or -1. */
static int sa_src_reg(hwaddr w, hwaddr *reg)
{
    for (int i = 0; i < SA_NSRC; i++) {
        if (w >= sa_src_base[i] && w < sa_src_base[i] + 0x30) {
            *reg = w - sa_src_base[i];
            return i;
        }
    }
    return -1;
}

static bool sa_ram_range(hwaddr addr, hwaddr len, bool write)
{
    hwaddr translated, span = len;
    MemoryRegion *mr;

    RCU_READ_LOCK_GUARD();
    mr = address_space_translate(&address_space_memory, addr, &translated,
                                 &span, write, MEMTXATTRS_UNSPECIFIED);
    return span >= len && memory_region_is_ram(mr) &&
           !memory_region_is_rom(mr) && !memory_region_is_ram_device(mr);
}

/*
 * E0650 DGI op 0x13: one linear neutral 4:2:2 output extent. Accept only
 * the observed layout, not arbitrary conversion, decoding or scatter DMA.
 */
static bool sa_jpeg_fill(uint32_t pa)
{
    uint8_t d[SA_ZMA1_FILL_BYTES];
    uint32_t width, height, pitch, dst, report, color, extent;
    hwaddr map_len, result_len = sizeof(uint32_t);
    size_t pixel_bytes;
    uint8_t *pixels, *result;

    if ((pa & 0x7f) || pa < 0x04000000 || pa > 0x40000000 - sizeof(d) ||
        !sa_ram_range(pa, sizeof(d), false) ||
        address_space_read(&address_space_memory, pa, MEMTXATTRS_UNSPECIFIED,
                           d, sizeof(d)) != MEMTX_OK) {
        return false;
    }

    width = ldl_le_p(d + 0x18);
    height = ldl_le_p(d + 0x1c);
    pitch = ldl_le_p(d + 0x20);
    dst = ldl_le_p(d + 0x44);
    report = ldl_le_p(d + 0x5c);
    color = ldl_le_p(d + 0x68);
    if (!width || (width & 63) || width != pitch || width > 4096 ||
        !height || height > 2160 || color != 0x00800080) {
        return false;
    }
    extent = 2 * pitch * ROUND_UP(height, 16);
    for (size_t i = 0; i < sizeof(d); i += sizeof(uint32_t)) {
        uint32_t expected;

        switch (i) {
        case 0x18:
            expected = width;
            break;
        case 0x1c:
            expected = height;
            break;
        case 0x20:
            expected = pitch;
            break;
        case 0x44:
        case 0x118:
        case 0x124:
            expected = dst;
            break;
        case 0x5c:
            expected = report;
            break;
        case 0x68:
            expected = color;
            break;
        case 0x110:
            expected = 1;
            break;
        case 0x120:
            expected = extent | 1;
            break;
        default:
            expected = 0;
            break;
        }
        if (ldl_le_p(d + i) != expected) {
            return false;
        }
    }
    if ((dst & 0x7f) || dst < 0x04000000 || dst > 0x40000000 - extent ||
        (report & 0x7f) || report < pa + sizeof(d) ||
        report > 0x40000000 - 8 ||
        !sa_ram_range(dst, extent, true) ||
        !sa_ram_range(report + 4, sizeof(uint32_t), true)) {
        return false;
    }

    pixel_bytes = 2 * pitch * height;
    map_len = pixel_bytes;
    pixels = address_space_map(&address_space_memory, dst, &map_len, true,
                               MEMTXATTRS_UNSPECIFIED);
    if (!pixels) {
        return false;
    }
    if (map_len != pixel_bytes) {
        address_space_unmap(&address_space_memory, pixels, map_len, true, 0);
        return false;
    }

    result = address_space_map(&address_space_memory, report + 4, &result_len,
                               true, MEMTXATTRS_UNSPECIFIED);
    if (!result || result_len != sizeof(uint32_t)) {
        if (result) {
            address_space_unmap(&address_space_memory, result, result_len,
                                true, 0);
        }
        address_space_unmap(&address_space_memory, pixels, map_len, true, 0);
        return false;
    }

    for (size_t i = 0; i < pixel_bytes; i += sizeof(uint32_t)) {
        stl_le_p(pixels + i, color);
    }
    address_space_unmap(&address_space_memory, pixels, map_len, true,
                        pixel_bytes);
    /* The original fill consumer accepts result word +4 equal to 1 or 2. */
    stl_le_p(result, 1);
    address_space_unmap(&address_space_memory, result, result_len, true,
                        result_len);
    return true;
}

/*
 * DGI op 0x11 (0x00001100): authentic JPEG decode from guest DRAM src_jpeg_pa
 * (d+0x34, len d+0x38) into packed UYVY at dst_yc_pa (d+0x44), reporting
 * completion at report_pa + 4 = 1 (dgis_jpeg_decode_check_zima_rpt @ 0x63a097f2).
 */
static bool sa_jpeg_decode(uint32_t pa)
{
    uint8_t d[SA_ZMA1_FILL_BYTES];

    if ((pa & 0x7f) || pa < 0x04000000 || pa > 0x40000000 - sizeof(d) ||
        !sa_ram_range(pa, sizeof(d), false) ||
        address_space_read(&address_space_memory, pa, MEMTXATTRS_UNSPECIFIED,
                           d, sizeof(d)) != MEMTX_OK) {
        return false;
    }

    uint32_t dst_w = ldl_le_p(d + 0x18);
    uint32_t dst_h = ldl_le_p(d + 0x1c);
    uint32_t dst_pitch_px = ldl_le_p(d + 0x20) & 0xFFFFu;
    uint32_t crop_en = ldl_le_p(d + 0x24) & 1u;
    uint32_t crop_xy = ldl_le_p(d + 0x28);
    uint32_t crop_wh = ldl_le_p(d + 0x2c);
    uint32_t src_jpeg_pa = ldl_le_p(d + 0x34);
    uint32_t src_jpeg_len = ldl_le_p(d + 0x38) & 0x3FFFFFFFu;
    uint32_t dst_yc_pa = ldl_le_p(d + 0x44);
    uint32_t report_pa = ldl_le_p(d + 0x5c);

    if (!dst_w || (dst_w & 1u) || dst_w > 8192 || !dst_h || dst_h > 8192 ||
        dst_pitch_px < dst_w || !src_jpeg_len || src_jpeg_len > (32u << 20)) {
        return false;
    }
    uint32_t dst_stride = 2u * dst_pitch_px;
    size_t dst_bytes = (size_t)dst_stride * dst_h;
    if (src_jpeg_pa < 0x04000000u ||
        (uint64_t)src_jpeg_pa + src_jpeg_len > 0x40000000ULL ||
        dst_yc_pa < 0x04000000u ||
        (uint64_t)dst_yc_pa + dst_bytes > 0x40000000ULL ||
        report_pa < 0x04000000u || report_pa > 0x40000000u - 16u ||
        !sa_ram_range(src_jpeg_pa, src_jpeg_len, false) ||
        !sa_ram_range(dst_yc_pa, dst_bytes, true) ||
        !sa_ram_range(report_pa, 12, true)) {
        return false;
    }

    g_autofree uint8_t *jpeg_buf = g_malloc(src_jpeg_len);
    g_autofree uint8_t *uyvy_buf = g_malloc0(dst_bytes);
    if (address_space_read(&address_space_memory, src_jpeg_pa,
                           MEMTXATTRS_UNSPECIFIED, jpeg_buf,
                           src_jpeg_len) != MEMTX_OK) {
        return false;
    }

    Cxd90045JpegCrop crop = {
        .enabled = crop_en != 0,
        .x = (uint16_t)(crop_xy & 0xFFFFu),
        .y = (uint16_t)(crop_xy >> 16),
        .w = (uint16_t)(crop_wh & 0xFFFFu),
        .h = (uint16_t)(crop_wh >> 16),
    };
    if (!cxd90045_jpeg_decode_to_uyvy(jpeg_buf, src_jpeg_len, uyvy_buf, dst_w,
                                      dst_h, dst_stride, &crop)) {
        return false;
    }

    if (address_space_write(&address_space_memory, dst_yc_pa,
                            MEMTXATTRS_UNSPECIFIED, uyvy_buf,
                            dst_bytes) != MEMTX_OK) {
        return false;
    }

    uint8_t rpt[12];
    stl_le_p(rpt + 0, 0);
    stl_le_p(rpt + 4, 1);
    stl_le_p(rpt + 8, src_jpeg_len);
    address_space_write(&address_space_memory, report_pa,
                        MEMTXATTRS_UNSPECIFIED, rpt, sizeof(rpt));
    return true;
}

/*
 * DGI op 0x1B (0x00001B00): authentic JPEG ECS + FF D9 encode from guest DRAM
 * packed UYVY at src_yc_pa (d+0x34) into scatter-gather write extents starting
 * at d+0x118 + (d+0x04 & 0x7F), reporting ecs_bytes at report_pa + 8.
 */
static bool sa_jpeg_encode(uint32_t pa)
{
    uint8_t d[SA_ZMA1_FILL_BYTES];

    if ((pa & 0x7f) || pa < 0x04000000 || pa > 0x40000000 - sizeof(d) ||
        !sa_ram_range(pa, sizeof(d), false) ||
        address_space_read(&address_space_memory, pa, MEMTXATTRS_UNSPECIFIED,
                           d, sizeof(d)) != MEMTX_OK) {
        return false;
    }

    bool is_420 = (ldl_le_p(d + 0x00) & 0x100u) != 0;
    uint32_t ecs_off = ldl_le_p(d + 0x04) & 0x7Fu;
    uint16_t dri = (uint16_t)(ldl_le_p(d + 0x08) & 0xFFFFu);
    uint32_t width = ldl_le_p(d + 0x18);
    uint32_t height = ldl_le_p(d + 0x1c);
    uint32_t src_pitch_px = ldl_le_p(d + 0x20) & 0xFFFFu;
    uint32_t src_yc_pa = ldl_le_p(d + 0x34);
    uint32_t report_pa = ldl_le_p(d + 0x5c);
    uint32_t num_ext = ldl_le_p(d + 0x110);

    if (!width || (width & 1u) || width > 8192 || !height || height > 8192 ||
        src_pitch_px < width || num_ext == 0 || num_ext > 12) {
        return false;
    }
    uint32_t src_stride = 2u * src_pitch_px;
    size_t src_bytes = (size_t)src_stride * height;
    if (src_yc_pa < 0x04000000u ||
        (uint64_t)src_yc_pa + src_bytes > 0x40000000ULL ||
        report_pa < 0x04000000u || report_pa > 0x40000000u - 16u ||
        !sa_ram_range(src_yc_pa, src_bytes, false) ||
        !sa_ram_range(report_pa, 12, true)) {
        return false;
    }

    /* Extract zig-zag QTables (big-endian bytes inside each 32-bit word, 0x63a03350) */
    uint8_t q_luma[64], q_chroma[64];
    for (unsigned w = 0; w < 16; w++) {
        uint32_t wl = ldl_le_p(d + 0x80 + 4 * w);
        uint32_t wc = ldl_le_p(d + 0xC0 + 4 * w);
        q_luma[4 * w + 0] = (wl >> 24) & 0xFFu;
        q_luma[4 * w + 1] = (wl >> 16) & 0xFFu;
        q_luma[4 * w + 2] = (wl >> 8) & 0xFFu;
        q_luma[4 * w + 3] = wl & 0xFFu;
        q_chroma[4 * w + 0] = (wc >> 24) & 0xFFu;
        q_chroma[4 * w + 1] = (wc >> 16) & 0xFFu;
        q_chroma[4 * w + 2] = (wc >> 8) & 0xFFu;
        q_chroma[4 * w + 3] = wc & 0xFFu;
    }

    g_autofree uint8_t *src_uyvy = g_malloc(src_bytes);
    if (address_space_read(&address_space_memory, src_yc_pa,
                           MEMTXATTRS_UNSPECIFIED, src_uyvy,
                           src_bytes) != MEMTX_OK) {
        return false;
    }

    size_t ecs_len = 0;
    g_autofree uint8_t *ecs = cxd90045_jpeg_encode_uyvy_ecs(
        src_uyvy, width, height, src_stride, is_420, dri, q_luma, q_chroma,
        &ecs_len);
    if (!ecs || ecs_len == 0) {
        return false;
    }

    /* Scatter-write ECS across write extents (d + 0x120 + 8*i, bit 0 == 1) */
    size_t written = 0;
    bool first_write_ext = true;
    for (unsigned i = 0; i < num_ext && written < ecs_len; i++) {
        uint32_t ew0 = ldl_le_p(d + 0x120 + 8 * i);
        uint32_t epa = ldl_le_p(d + 0x124 + 8 * i);
        if (!(ew0 & 1u)) {
            continue;
        }
        uint32_t elen = ew0 & 0x0FFFFF80u;
        uint32_t skip = first_write_ext ? ecs_off : 0;
        first_write_ext = false;
        if (elen <= skip || epa < 0x04000000u ||
            (uint64_t)epa + elen > 0x40000000ULL) {
            return false;
        }
        size_t chunk = MIN(ecs_len - written, (size_t)(elen - skip));
        if (!sa_ram_range(epa + skip, chunk, true) ||
            address_space_write(&address_space_memory, epa + skip,
                                MEMTXATTRS_UNSPECIFIED, ecs + written,
                                chunk) != MEMTX_OK) {
            return false;
        }
        written += chunk;
    }
    if (written < ecs_len) {
        return false;
    }

    uint8_t rpt[12];
    stl_le_p(rpt + 0, 0);
    stl_le_p(rpt + 4, 1);
    stl_le_p(rpt + 8, (uint32_t)ecs_len);
    address_space_write(&address_space_memory, report_pa,
                        MEMTXATTRS_UNSPECIFIED, rpt, sizeof(rpt));
    return true;
}

/*
 * Opt-in movie-codec-hle: SFRS Audio SA (SA0/SA1, 0x63a95ad4..f24) and
 * ZIMA DVENC H.264 video SA (ZMA0/ZMA1, 0x639ed904..0x63f688e2).
 */
static void sa_movie_audio_hle(CxdSaState *s)
{
    if (!s->movie_codec_hle || (s->index != 2 && s->index != 3)) {
        return;
    }
    /* SetSaCodecOpenParam (0x63a95ad4): mbox[0] low16 == 0x1194, mbox[3] = BootParamPA */
    uint32_t m0 = R(SA_MBOX_A);
    uint32_t m2 = R(SA_MBOX_A + 8);
    uint32_t boot_pa = R(SA_MBOX_A + 12);
    if ((m0 & 0xFFFFu) == 0x1194u && boot_pa >= 0x04000000u &&
        boot_pa <= 0x40000000u - 64u && sa_ram_range(boot_pa, 64, false)) {
        uint8_t bp[64];
        if (address_space_read(&address_space_memory, boot_pa,
                               MEMTXATTRS_UNSPECIFIED, bp, sizeof(bp)) == MEMTX_OK) {
            /* Scan BootParam pointers for controlInfoAddr and set +8 = 1 (and +11 = 1 for ZoomNR) */
            for (unsigned off = 0; off + 4 <= sizeof(bp); off += 4) {
                uint32_t ptr = ldl_le_p(bp + off);
                if (ptr >= 0x04000000u && ptr <= 0x40000000u - 32u &&
                    sa_ram_range(ptr, 16, true)) {
                    uint8_t hdr[16];
                    address_space_read(&address_space_memory, ptr,
                                       MEMTXATTRS_UNSPECIFIED, hdr, sizeof(hdr));
                    /* Only arm zeroed control status bytes (+8 / +11) */
                    if (hdr[8] == 0) {
                        uint8_t one = 1;
                        address_space_write(&address_space_memory, ptr + 8,
                                            MEMTXATTRS_UNSPECIFIED, &one, 1);
                    }
                    if (((m2 >> 24) & 0xFFu) == 0xB0u && hdr[11] == 0) {
                        uint8_t one = 1;
                        address_space_write(&address_space_memory, ptr + 11,
                                            MEMTXATTRS_UNSPECIFIED, &one, 1);
                    }
                }
            }
        }
    }
}

static void sa_movie_video_hle(CxdSaState *s, uint32_t cmd, uint32_t arg_pa)
{
    static const uint8_t minimal_h264_au[] = {
        /* Annex-B AUD + Baseline/High SPS + PPS + minimal IDR slice */
        0x00, 0x00, 0x00, 0x01, 0x09, 0x10,
        0x00, 0x00, 0x00, 0x01, 0x67, 0x64, 0x00, 0x28, 0xAC, 0xD9, 0x40, 0x78,
        0x02, 0x27, 0xE5, 0xC0, 0x44, 0x00, 0x00, 0x03, 0x00, 0x04, 0x00, 0x00,
        0x03, 0x00, 0xF0, 0x3C, 0x60, 0xC6, 0x58,
        0x00, 0x00, 0x00, 0x01, 0x68, 0xEB, 0xE3, 0xCB, 0x22, 0xC0,
        0x00, 0x00, 0x00, 0x01, 0x65, 0x88, 0x84, 0x00, 0x33, 0xFF,
    };

    s->mbox_out_a[0] = 1; /* VENC_RESULT_DONE / ACK */
    s->mbox_out_a[1] = arg_pa;
    if (arg_pa >= 0x04000000u && arg_pa <= 0x40000000u - 128u &&
        sa_ram_range(arg_pa, 128, true)) {
        uint8_t arg[128];
        address_space_read(&address_space_memory, arg_pa,
                           MEMTXATTRS_UNSPECIFIED, arg, sizeof(arg));
        uint32_t maybe_es = ldl_le_p(arg + 0x10);
        uint32_t maybe_sz = ldl_le_p(arg + 0x14);
        if (maybe_es >= 0x04000000u && maybe_es < 0x40000000u &&
            maybe_sz >= 0x10000u && maybe_sz <= (64u << 20)) {
            s->venc_es_pa = maybe_es;
            s->venc_es_size = maybe_sz;
            s->venc_es_wp = maybe_es;
        }
        if (s->venc_es_pa &&
            sa_ram_range(s->venc_es_wp, sizeof(minimal_h264_au), true)) {
            address_space_write(&address_space_memory, s->venc_es_wp,
                                MEMTXATTRS_UNSPECIFIED, minimal_h264_au,
                                sizeof(minimal_h264_au));
            s->mbox_out_a[2] = s->venc_es_wp;
            s->mbox_out_a[3] = sizeof(minimal_h264_au);
            s->venc_es_wp += ROUND_UP(sizeof(minimal_h264_au), 128u);
            if (s->venc_es_wp + 256u >= s->venc_es_pa + s->venc_es_size) {
                s->venc_es_wp = s->venc_es_pa;
            }
            s->venc_frames++;
        }
        /* Mark completion word in 128B-aligned DVENC arg block */
        uint32_t one = 1;
        address_space_write(&address_space_memory, arg_pa + 4,
                            MEMTXATTRS_UNSPECIFIED, &one, 4);
    }
    (void)cmd;
}

static void sa_done(void *opaque)
{
    CxdSaState *s = opaque;

    s->running = false;
    if (s->fill_desc || s->pending_op) {
        uint32_t desc = s->fill_desc;
        uint32_t op = s->pending_op;
        bool ok = false;

        s->fill_desc = 0;
        s->pending_op = 0;
        R(SA_SEND_A) &= ~1u;
        if (op == SA_ZMA1_FILL_CMD) {
            ok = sa_jpeg_fill(desc);
        } else if (op == SA_ZMA_DECODE_CMD) {
            ok = sa_jpeg_decode(desc);
        } else if (op == SA_ZMA_ENCODE_CMD) {
            ok = sa_jpeg_encode(desc);
        } else if (s->movie_codec_hle) {
            if (s->index == 0 || s->index == 1) {
                sa_movie_video_hle(s, op, desc);
            } else {
                sa_movie_audio_hle(s);
            }
            ok = true;
        }
        if (ok) {
            s->factor[0] |= 0x01;
            sa_update(s);
        } else {
            qemu_log_mask(LOG_UNIMP, "cxd90014-sa%u: unsupported DGI op 0x%x "
                          "descriptor at 0x%08x\n", s->index, op, desc);
        }
        return;
    }
    R(SA_GO) &= ~1u;
    if (s->movie_codec_hle) {
        sa_movie_audio_hle(s);
        if (s->index == 0 || s->index == 1) {
            s->mbox_out_a[0] = 1;
        }
    }
    s->zma1_ready = s->completion_factor == SA_ZMA1_BOOT_FACTOR &&
                    (R(SA_RUN) & 1);
    s->factor[0] |= s->completion_factor;
    if (s->starts <= 8) {
        fprintf(stderr, "[SA%u] done start#%u factorA=0x%02x maskA=0x%02x\n",
                s->index, s->starts, s->factor[0], s->mask[0]);
    }
    sa_update(s);
}

static uint8_t sa_start_factor(CxdSaState *s)
{
    uint32_t entry = R(SA_ENTRY);
    uint8_t program[SA_ZMA1_STARTUP_CODE_BYTES];

    if ((!s->zma1_startup && !s->zma1_jpeg_decode) ||
        (s->index != 0 && s->index != 1) ||
        s->mask[0] != SA_ZMA1_BOOT_FACTOR) {
        return 0x01;
    }

    /* Verified ZMA0 (SA_Z_ST00x00) and ZMA1 (SA_Z_ST10x00) startup programs. */
    if (R(SA_PROG) == 0x80000000 && !(entry & 3) &&
        entry >= 0x04000000 && entry <= 0x40000000 - sizeof(program) &&
        address_space_read(&address_space_memory, entry, MEMTXATTRS_UNSPECIFIED,
                           program, sizeof(program)) == MEMTX_OK) {
        g_autofree char *hash = g_compute_checksum_for_data(
            G_CHECKSUM_SHA256, program, sizeof(program));
        const char *expected = (s->index == 0) ? SA_ZMA0_STARTUP_CODE_SHA256
                                               : SA_ZMA1_STARTUP_CODE_SHA256;

        if (g_str_equal(hash, expected)) {
            return SA_ZMA1_BOOT_FACTOR;
        }
    }
    qemu_log_mask(LOG_UNIMP, "cxd90014-sa%u: unsupported ZMA startup program at 0x%08x\n",
                  s->index, entry);
    return 0;
}

static void sa_start(CxdSaState *s)
{
    s->zma1_ready = false;
    s->starts++;
    if (s->starts <= 8) {
        fprintf(stderr, "[SA%u] start #%u entry 0x%08x +0x008 0x%08x "
                "mbox %08x %08x %08x %08x\n",
                s->index, s->starts, R(SA_ENTRY), R(SA_PROG),
                R(SA_MBOX_A), R(SA_MBOX_A + 4), R(SA_MBOX_A + 8),
                R(SA_MBOX_A + 12));
    }
    s->completion_factor = sa_start_factor(s);
    qemu_log_mask(LOG_UNIMP, "cxd90014-sa%u: start #%u entry 0x%08x "
                  "+0x008 0x%08x mbox %08x %08x %08x %08x (null SA: "
                  "factor A 0x%02x, no data)\n",
                  s->index, s->starts, R(SA_ENTRY), R(SA_PROG),
                  R(SA_MBOX_A), R(SA_MBOX_A + 4), R(SA_MBOX_A + 8),
                  R(SA_MBOX_A + 12), s->completion_factor);
    s->running = true;
    timer_mod(s->done, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              (int64_t)s->delay_us * SCALE_US);
}

static uint32_t sa_read_word(CxdSaState *s, hwaddr w)
{
    hwaddr reg;
    int src;

    switch (w) {
    case SA_RUN:
    case SA_GO:
    case SA_PROG:
    case SA_SEND_A:
    case 0x01C:
    case SA_ENTRY:
        return R(w);
    case 0x084:
    case 0x088:
        return 0;
    case SA_ID ... SA_ID + 0xC:
        return ldl_le_p(&s->id[w - SA_ID]);
    }
    src = sa_src_reg(w, &reg);
    if (src >= 0) {
        switch (reg) {
        case 0x00:                  /* status byte */
            return 0;
        case 0x04:
            return s->factor[src];
        case 0x08:
            return s->mask[src];
        case 0x10 ... 0x1C:         /* host->SA mailbox */
            return R(w);
        case 0x20 ... 0x2C:         /* SA->host mailbox */
            if (src == 0 && s->movie_codec_hle) {
                return s->mbox_out_a[(reg - 0x20) >> 2];
            }
            return 0;
        }
    }
    qemu_log_mask(LOG_UNIMP, "cxd90014-sa%u: rd 0x%08" HWADDR_PRIx
                  " -> 0x%x\n", s->index, s->base + w, R(w));
    return R(w);
}

static void sa_write_word(CxdSaState *s, hwaddr w, uint32_t v, uint32_t m)
{
    hwaddr reg;
    int src;

    switch (w) {
    case SA_GO:
        R(w) = (R(w) & ~m) | (v & m);
        if ((m & 1) && (v & 1) && !s->running) {
            if (R(SA_RUN) & 1) {
                sa_start(s);
            } else {
                qemu_log_mask(LOG_UNIMP, "cxd90014-sa%u: GO while +0x000 "
                              "bit0 = 0 (ignored)\n", s->index);
            }
        }
        return;
    case SA_RUN:
        R(w) = (R(w) & ~m) | (v & m);
        if (!(R(w) & 1)) {
            s->zma1_ready = false;
            if (s->fill_desc || s->pending_op) {
                timer_del(s->done);
                s->fill_desc = 0;
                s->pending_op = 0;
                s->running = false;
                R(SA_SEND_A) &= ~1u;
            }
        }
        return;
    case SA_SEND_A:
        R(w) = (R(w) & ~m) | (v & m);
        if ((m & 0xff) == 0xff && (v & 0xff) == 1) {
            uint32_t command = R(SA_MBOX_A);
            uint32_t op = command & 0x00ffffffu;
            bool dgi_en = (s->zma1_jpeg_fill || s->zma1_jpeg_decode) &&
                          s->zma1_ready;
            bool decode_en = (s->zma1_jpeg_decode || s->zma1_jpeg_fill) &&
                             s->zma1_ready;

            if (!s->running && dgi_en && (command >> 24) && R(SA_MBOX_A + 4) &&
                op == SA_ZMA1_FILL_CMD) {
                s->fill_desc = R(SA_MBOX_A + 4);
                s->pending_op = op;
                s->running = true;
                timer_mod(s->done, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                          (int64_t)s->delay_us * SCALE_US);
            } else if (!s->running && decode_en && (command >> 24) &&
                       R(SA_MBOX_A + 4) &&
                       (op == SA_ZMA_DECODE_CMD || op == SA_ZMA_ENCODE_CMD)) {
                s->fill_desc = R(SA_MBOX_A + 4);
                s->pending_op = op;
                s->running = true;
                timer_mod(s->done, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                          (int64_t)s->delay_us * SCALE_US);
            } else if (!s->running && s->movie_codec_hle && (R(SA_RUN) & 1)) {
                s->fill_desc = R(SA_MBOX_A + 4);
                s->pending_op = command ? command : 1u;
                s->running = true;
                timer_mod(s->done, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                          (int64_t)s->delay_us * SCALE_US);
            } else if (dgi_en) {
                qemu_log_mask(LOG_UNIMP, "cxd90014-sa%u: unsupported or busy "
                              "source-A command 0x%08x\n", s->index, command);
            }
        }
        return;
    case SA_PROG:
    case 0x01C:
    case SA_ENTRY:
        R(w) = (R(w) & ~m) | (v & m);
        return;
    }
    src = sa_src_reg(w, &reg);
    if (src >= 0) {
        switch (reg) {
        case 0x04:                  /* factor: W1C of the low byte */
            s->factor[src] &= ~(v & m & 0xFF);
            sa_update(s);
            return;
        case 0x08:
            s->mask[src] = (s->mask[src] & ~(m & 0xFF)) | (v & m & 0xFF);
            sa_update(s);
            return;
        case 0x10 ... 0x1C:
            R(w) = (R(w) & ~m) | (v & m);
            return;
        }
    }
    qemu_log_mask(LOG_UNIMP, "cxd90014-sa%u: wr 0x%08" HWADDR_PRIx
                  " = 0x%x (mask 0x%x)\n", s->index, s->base + w, v, m);
    R(w) = (R(w) & ~m) | (v & m);
}

static void sa_log_acc(CxdSaState *s, const char *rw, hwaddr off, uint32_t v)
{
    static int left = 24;
    if (left <= 0) {
        return;
    }
    left--;
    fprintf(stderr, "[SA%u] %s +0x%03" HWADDR_PRIx " = 0x%08x\n",
            s->index, rw, off, v);
}

static uint64_t sa_read(void *opaque, hwaddr off, unsigned size)
{
    CxdSaState *s = opaque;
    unsigned sh = (off & 3) * 8;
    uint32_t v = sa_read_word(s, off & ~3);
    uint32_t out = size == 4 ? v : (v >> sh) & ((1u << (size * 8)) - 1);

    sa_log_acc(s, "rd", off, out);
    return out;
}

static void sa_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    CxdSaState *s = opaque;
    unsigned sh = (off & 3) * 8;
    uint32_t m = size == 4 ? 0xFFFFFFFFu : ((1u << (size * 8)) - 1) << sh;

    sa_log_acc(s, "wr", off, (uint32_t)v);
    sa_write_word(s, off & ~3, (uint32_t)v << sh, m);
}

static const MemoryRegionOps sa_ops = {
    .read = sa_read, .write = sa_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void sa_reset(DeviceState *dev)
{
    CxdSaState *s = CXD_SA(dev);

    timer_del(s->done);
    memset(s->r, 0, sizeof(s->r));
    memset(s->mbox_out_a, 0, sizeof(s->mbox_out_a));
    memset(s->factor, 0, sizeof(s->factor));
    memset(s->mask, 0, sizeof(s->mask));
    s->running = false;
    s->zma1_ready = false;
    s->pending_op = 0;
    s->fill_desc = 0;
    s->venc_es_pa = 0;
    s->venc_es_size = 0;
    s->venc_es_wp = 0;
    s->venc_frames = 0;
    sa_update(s);
}

static void sa_realize(DeviceState *dev, Error **errp)
{
    CxdSaState *s = CXD_SA(dev);

    if (!s->core_id || strlen(s->core_id) != SA_IDLEN) {
        error_setg(errp, "cxd90014-sa: core-id must be %d characters",
                   SA_IDLEN);
        return;
    }
    memcpy(s->id, s->core_id, SA_IDLEN);
}

static void sa_init(Object *obj)
{
    CxdSaState *s = CXD_SA(obj);

    memory_region_init_io(&s->mr, obj, &sa_ops, s, "cxd90014-sa", SA_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mr);
    for (int i = 0; i < SA_NSRC; i++) {
        sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq[i]);
    }
    s->done = timer_new_ns(QEMU_CLOCK_VIRTUAL, sa_done, s);
}

static const Property sa_props[] = {
    DEFINE_PROP_UINT32("index", CxdSaState, index, 0),
    DEFINE_PROP_UINT64("base", CxdSaState, base, 0),    /* log only */
    DEFINE_PROP_STRING("core-id", CxdSaState, core_id),
    /* hypothesis: real SA run time unknown (RE_SA_DSP §7 q1) */
    DEFINE_PROP_UINT32("delay-us", CxdSaState, delay_us, 100),
    DEFINE_PROP_BOOL("zma1-startup", CxdSaState, zma1_startup, false),
    DEFINE_PROP_BOOL("zma1-jpeg-fill", CxdSaState, zma1_jpeg_fill, false),
    DEFINE_PROP_BOOL("zma1-jpeg-decode", CxdSaState, zma1_jpeg_decode, false),
    DEFINE_PROP_BOOL("movie-codec-hle", CxdSaState, movie_codec_hle, false),
};

static const VMStateDescription vmstate_cxd_sa = {
    .name = "cxd90014-sa",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(r, CxdSaState, SA_SIZE / 4),
        VMSTATE_UINT8_ARRAY(factor, CxdSaState, SA_NSRC),
        VMSTATE_UINT8_ARRAY(mask, CxdSaState, SA_NSRC),
        VMSTATE_UINT8(completion_factor, CxdSaState),
        VMSTATE_BOOL(running, CxdSaState),
        VMSTATE_BOOL(zma1_ready, CxdSaState),
        VMSTATE_UINT32(fill_desc, CxdSaState),
        VMSTATE_UINT32(starts, CxdSaState),
        VMSTATE_TIMER_PTR(done, CxdSaState),
        VMSTATE_END_OF_LIST()
    }
};

static void sa_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = sa_realize;
    dc->vmsd = &vmstate_cxd_sa;
    device_class_set_legacy_reset(dc, sa_reset);
    device_class_set_props(dc, sa_props);
}

static const TypeInfo sa_info = {
    .name = TYPE_CXD_SA,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(CxdSaState),
    .instance_init = sa_init,
    .class_init = sa_class_init,
};

static void sa_register(void)
{
    type_register_static(&sa_info);
}
type_init(sa_register)
