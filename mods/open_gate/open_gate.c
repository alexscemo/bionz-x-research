/* Open Gate 3:2 Recording Mod (SMOD v2 + SMNU Nested Submenu)
 *
 * Unlocks configurable 4K & HD 3:2 Open Gate Movie recording across Sony BIONZ X
 * (CXD90045 / IMX284) bodies (ILCE-6400 2.00 captured & ILCE-6400A 1.01) with
 * three selectable Sensor Gate tiers:
 *
 *   0. 5.1K True 4K (default -- 100% within the E0721-E0728 FE <= 3445 envelope):
 *      - XAVC S 4K 24p/25p stays on native unbinned IMX284 Mode 16/17 (0x1F/0x89,
 *        6000x3376 @ 11-bit 2H, stock dual-stripe FE[6]/FE[9] <= 3445 so Lost
 *        CAMOUT_V can never fire) + ExlHelper output_aspect = 0 (3:2), extracting
 *        the centered 5064x3376 (17.09 MP = 5.06K x 3.38K, 1.185x Super-35) 3:2
 *        region and supersampling 1.563x into true 3240x2160 4K 3:2.
 *      - XAVC S 4K 30p stays on unbinned Mode 15 (0x13, 4896x2760) -> 4140x2760
 *        (11.43 MP = 4.14K x 2.76K), supersampling 1.278x into 3240x2160 4K 3:2.
 *      - XAVC S HD routes to full-width Mode 10 (0x44, 1996x1332) -> 1620x1080.
 *
 *   1. 6K Reg01 Full (experimental uncropped 6000x4000 24 MP 6K->4K probe):
 *      - Applies the newly decoded IMX284 col-11 reg 0x0001: 0x41 -> 0x40 full-gate
 *        continuous bit (clearing the 16:9 vertical window bit left set in E0721-E0728)
 *        + Y_START=30, Y_SIZE=4020 + all 66x2 FE[6]/FE[9] dy=+624 offsets (4016 active)
 *        + size_rows/rt_t14 V_ACTIVE=4000 + 6 rcv_desc (+624) + lockstep 24p (731,2052)
 *        & 25p (701,2052) timings + S-Ele deliberate-fault guard (0x603F -> 0xBF00 nop)
 *        so the camera never reboots even if IMX284 silicon rejects (0x0C, 0x40).
 *
 *   2. Full-FOV 2K (100% uncropped 23.5x15.6 mm 1.00x FOV at 24p/25p/30p):
 *      - Routes 4K & HD to IMX284 Mode 10 (0x44, 1996x1332 @ 12-bit, stock single-stripe
 *        FE[4]), upscaling to 3240x2160 or downsampling to 1620x1080 with zero crop.
 *
 *   Synchronizes DMM (idx 6 3264x2160 / idx 3 1664x1080), VPI, ZIMA DVENC, and
 *   runtime-located libmpr.so (MprLibCore::EncOpen .text immediates via mprotect +
 *   ARM cacheflush plus .rodata profile tables). Restores 100% stock bytes on OFF.
 *
 * Submenu Configuration (__smod_bss + 4, 4 x uint8_t in OpenGate32 / 3 in Sensor):
 *   cfg[0] Sensor Gate   : 0 = 5.1K True 4K, 1 = 6K Reg01 Full, 2 = Full-FOV 2K
 *   cfg[1] Canvas Format : 0 = Native 3:2 4K, 1 = Native 3:2 HD,
 *                          2 = Native 4K+HD,  3 = Anamorphic
 *   cfg[2] M10 Timing    : 0 = 24p (23.98fps), 1 = 30p (29.97fps), 2 = Stock Auto
 *   cfg[3] Movie Scope   : 0 = 4K + HD,        1 = 4K Only,        2 = HD Only
 *
 * Log: /tmp_bt/mods/open_gate_32.log (or open_gate_sensor.log)
 */
#include <stdint.h>

#define OG_STATE_MAGIC      0x3233474Fu /* "OG32" */
#define AVCAM_PHYS_BASE     0x035C6000u

#define ORIL_MAGIC_0        0xEA00000Au
#define ORIL_MAGIC_1        0x4C49524Fu /* "ORIL" */
#define ORIL_VERSION        0x00000004u

#define U16_3840_2160       0x08700F00u
#define U16_3240_2160       0x08700CA8u
#define U16_1920_1080       0x04380780u
#define U16_1620_1080       0x04380654u

/* Thumb-2 immediate encodings in libmpr.so MprLibCore::EncOpen converter */
#define MPR_MOV_3840_HW0    0xF44Fu
#define MPR_MOV_3840_HW1    0x6370u
#define MPR_MOV_3240_HW0    0xF640u
#define MPR_MOV_3240_HW1    0x43A8u

#define MPR_MOV_1920_HW0    0xF44Fu
#define MPR_MOV_1920_HW1    0x63F0u
#define MPR_MOV_1620_HW0    0xF240u
#define MPR_MOV_1620_HW1    0x6354u

#ifdef TARGET_ILCE6400A
/* ILCE-6400A 1.01 av-cam.bin (3029a174..., ORIL_BASE 0x635C6000) & libmpr.so */
#define ORIL_DATA_VA_EXP    0x644F5B80u
#define ORIL_DATA_SZ_EXP    0x000D079Cu

#define PA_SEL24_4K         0x0390C184u
#define PA_SEL24_HD         0x0390C1ACu
#define PA_SEL25_4K         0x0390C28Au
#define PA_SEL25_HD         0x0390C2DEu
#define PA_SEL30_4K         0x03D5087Au
#define PA_SEL30_HD         0x03D5088Eu

#define PA_EXL_MAIN         0x037702E2u
#define PA_EXL_SUB          0x03CE501Eu
#define PA_BUF_BNE          0x03CB445Au

#define PA_T10_NTSC         0x03E282ACu
#define PA_T10_PAL          0x03E28474u
#define PA_RT_T14_M10       0x042062FCu

#define PA_REGLIST_R1_AL    0x04550F6Eu
#define PA_REGLIST_R10_AL   0x04551022u
#define PA_REGLIST_R11_AL   0x04551036u
#define PA_REGLIST_R12_AL   0x0455104Au
#define PA_REGLIST_R13_AL   0x0455105Eu
#define PA_FE6_BASE         0x03E17A24u
#define PA_FE9_BASE         0x03E189E4u
#define PA_M16_VACT         0x03E27E1Cu
#define PA_M17_VACT         0x03E27E68u
#define PA_RCV_BASE         0x03E1C2D0u
#define PA_T16_NTSC         0x03E2833Cu
#define PA_T17_NTSC         0x03E28354u
#define PA_T16_PAL          0x03E28504u
#define PA_T17_PAL          0x03E2851Cu
#define PA_RT_T14_M16       0x042065CCu
#define PA_RT_T14_M17       0x04206644u
#define PA_SELE_CRASH       0x03909BA8u

#define PA_DMM_0            0x03DF4584u
#define PA_DMM_1            0x03DF462Cu
#define PA_DMM_2            0x03DF46D4u
#define PA_VPI_0            0x03DF4B3Cu
#define PA_VPI_1            0x04548678u
#define PA_VPI_2            0x045747A8u

#define MPR_ENCOPEN_HD_OFF  0x00870696u
#define MPR_ENCOPEN_4K_OFF  0x008706A2u
#else
/* ILCE-6400 2.00 captured av-cam.bin (dd8f8d0c...) & libmpr.so (bb31351c...) */
#define ORIL_DATA_VA_EXP    0x644F1000u
#define ORIL_DATA_SZ_EXP    0x000D0798u

#define PA_SEL24_4K         0x03909E4Cu
#define PA_SEL24_HD         0x03909E74u
#define PA_SEL25_4K         0x03909F52u
#define PA_SEL25_HD         0x03909FA6u
#define PA_SEL30_4K         0x03D4DAB4u
#define PA_SEL30_HD         0x03D4DAC8u

#define PA_EXL_MAIN         0x0376E23Eu
#define PA_EXL_SUB          0x03CE22CCu
#define PA_BUF_BNE          0x03CAEAE4u

#define PA_T10_NTSC         0x03E258FCu
#define PA_T10_PAL          0x03E25AC4u
#define PA_RT_T14_M10       0x0420173Cu

#define PA_REGLIST_R1_AL    0x0454C4A6u
#define PA_REGLIST_R10_AL   0x0454C55Au
#define PA_REGLIST_R11_AL   0x0454C56Eu
#define PA_REGLIST_R12_AL   0x0454C582u
#define PA_REGLIST_R13_AL   0x0454C596u
#define PA_FE6_BASE         0x03E14E94u
#define PA_FE9_BASE         0x03E15E54u
#define PA_M16_VACT         0x03E2546Cu
#define PA_M17_VACT         0x03E254B8u
#define PA_RCV_BASE         0x03E19740u
#define PA_T16_NTSC         0x03E2598Cu
#define PA_T17_NTSC         0x03E259A4u
#define PA_T16_PAL          0x03E25B54u
#define PA_T17_PAL          0x03E25B6Cu
#define PA_RT_T14_M16       0x04201A0Cu
#define PA_RT_T14_M17       0x04201A84u
#define PA_SELE_CRASH       0x03907870u

#define PA_DMM_0            0x03DF199Cu
#define PA_DMM_1            0x03DF1A44u
#define PA_DMM_2            0x03DF1AECu
#define PA_VPI_0            0x03DF1EC0u
#define PA_VPI_1            0x0454388Cu
#define PA_VPI_2            0x04570198u

#define MPR_ENCOPEN_HD_OFF  0x0087025Au
#define MPR_ENCOPEN_4K_OFF  0x00870266u
#endif

struct og_bss_state {
    uint32_t magic;
    uint8_t  cfg[8];          /* offset 4: synced by Mod Loader v2 SMNU (cfg[0..2]) */
    uint32_t active;
    uint32_t apply_count;
    uint32_t last_written;
    uint32_t last_unchanged;
    uint32_t last_error_code; /* 0 = ok, 1 = open, 2 = hdr, 3 = preflight, 4 = mpr_miss, 5 = write */
    uint32_t last_error_idx;
    uint32_t rt_saved_valid;
    uint32_t rt_orig_h;
    uint32_t rt_orig_v;
    uint32_t last_mpr_base;
};

extern uint8_t __smod_bss[] __attribute__((visibility("hidden")));
#define OG_BSS ((struct og_bss_state *)__smod_bss)

typedef struct {
    uint32_t pa;
    uint16_t stock;
    uint16_t patched;
} u16_site_t;

typedef struct {
    uint32_t pa;
    uint32_t stock;
    uint32_t patched;
} u32_site_t;

/* ---- Raw Linux ARM EABI syscall wrappers (freestanding, zero libc) ---- */
static long sc3(long n, long a, long b, long c) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static long sc6(long n, long a, long b, long c, long d, long e, long f) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c,
                  r3 __asm__("r3") = d, r4 __asm__("r4") = e, r5 __asm__("r5") = f;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7) : "memory");
    return r0;
}

#ifndef OG_SENSOR_ONLY
static void arm_cacheflush(uint32_t start, uint32_t end) {
    register long r0 __asm__("r0") = (long)start;
    register long r1 __asm__("r1") = (long)end;
    register long r2 __asm__("r2") = 0;
    register long r7 __asm__("r7") = 0x0f0002L; /* __ARM_NR_cacheflush */
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
}
#endif

/* ---- Bounded ASCII diagnostic logger ---- */
typedef struct {
    char b[256];
    int  n;
} lbuf_t;

static void lb_ch(lbuf_t *l, char c) {
    if (l->n < (int)sizeof(l->b) - 1) l->b[l->n++] = c;
}

static void lb_str(lbuf_t *l, const char *s) {
    while (*s) lb_ch(l, *s++);
}

static void lb_hex(lbuf_t *l, uint32_t v, int digits) {
    for (int sh = (digits - 1) * 4; sh >= 0; sh -= 4) {
        uint32_t nib = (v >> sh) & 0xfu;
        lb_ch(l, (char)(nib < 10u ? ('0' + nib) : ('a' + nib - 10u)));
    }
}

static void lb_flush(const lbuf_t *l) {
#ifdef OG_SENSOR_ONLY
    const char *path = "/tmp_bt/mods/open_gate_sensor.log";
#else
    const char *path = "/tmp_bt/mods/open_gate_32.log";
#endif
    long fd = sc3(5 /* open */, (long)path,
                  01 | 0100 | 02000 /* O_WRONLY | O_CREAT | O_APPEND */, 0644);
    if (fd >= 0) {
        sc3(4 /* write */, fd, (long)l->b, (long)l->n);
        sc3(6 /* close */, fd, 0, 0);
    }
}

/* ---- Single-page /dev/mem helpers (strict 16-bit and 32-bit alignment) ---- */
static int phys_read_u16(long mem_fd, uint32_t pa, uint16_t *out) {
    if ((pa & 1u) != 0u) return -1;
    long page = sc6(192 /* mmap2 */, 0, 4096, 1 /* PROT_READ */,
                    1 /* MAP_SHARED */, mem_fd, (long)(pa >> 12));
    if (page < 0 && page > -4096) return -1;
    *out = *(const volatile uint16_t *)((uint32_t)page + (pa & 0xfffu));
    sc3(91 /* munmap */, page, 4096, 0);
    return 0;
}

static int phys_write_u16(long mem_fd, uint32_t pa, uint16_t want, int *wrote) {
    if ((pa & 1u) != 0u) return -1;
    long page = sc6(192 /* mmap2 */, 0, 4096, 3 /* PROT_READ | PROT_WRITE */,
                    1 /* MAP_SHARED */, mem_fd, (long)(pa >> 12));
    if (page < 0 && page > -4096) return -1;
    volatile uint16_t *p = (volatile uint16_t *)((uint32_t)page + (pa & 0xfffu));
    int rc = 0;
    if (*p == want) {
        *wrote = 0;
    } else {
        *p = want;
        __asm__ volatile("dsb sy" ::: "memory");
        if (*p == want) {
            *wrote = 1;
        } else {
            rc = -1;
        }
    }
    sc3(91 /* munmap */, page, 4096, 0);
    return rc;
}

static int phys_read_u32(long mem_fd, uint32_t pa, uint32_t *out) {
    if ((pa & 3u) != 0u) return -1;
    long page = sc6(192 /* mmap2 */, 0, 4096, 1 /* PROT_READ */,
                    1 /* MAP_SHARED */, mem_fd, (long)(pa >> 12));
    if (page < 0 && page > -4096) return -1;
    *out = *(const volatile uint32_t *)((uint32_t)page + (pa & 0xfffu));
    sc3(91 /* munmap */, page, 4096, 0);
    return 0;
}

static int phys_write_u32(long mem_fd, uint32_t pa, uint32_t want, int *wrote) {
    if ((pa & 3u) != 0u) return -1;
    long page = sc6(192 /* mmap2 */, 0, 4096, 3 /* PROT_READ | PROT_WRITE */,
                    1 /* MAP_SHARED */, mem_fd, (long)(pa >> 12));
    if (page < 0 && page > -4096) return -1;
    volatile uint32_t *p = (volatile uint32_t *)((uint32_t)page + (pa & 0xfffu));
    int rc = 0;
    if (*p == want) {
        *wrote = 0;
    } else {
        *p = want;
        __asm__ volatile("dsb sy" ::: "memory");
        if (*p == want) {
            *wrote = 1;
        } else {
            rc = -1;
        }
    }
    sc3(91 /* munmap */, page, 4096, 0);
    return rc;
}

static int check_oril_identity(long mem_fd) {
    long page = sc6(192 /* mmap2 */, 0, 4096, 1 /* PROT_READ */,
                    1 /* MAP_SHARED */, mem_fd, (long)(AVCAM_PHYS_BASE >> 12));
    if (page < 0 && page > -4096) return -1;
    const volatile uint32_t *w = (const volatile uint32_t *)(uint32_t)page;
    int ok = (w[0] == ORIL_MAGIC_0 &&
              w[1] == ORIL_MAGIC_1 &&
              w[2] == ORIL_VERSION &&
              w[0xB8u / 4u] == ORIL_DATA_VA_EXP &&
              w[0xBCu / 4u] == ORIL_DATA_SZ_EXP);
    sc3(91 /* munmap */, page, 4096, 0);
    return ok ? 0 : -1;
}

/* ---- Runtime /proc/self/maps lookup and user-space libmpr.so helpers ---- */
#ifndef OG_SENSOR_ONLY
static uint32_t find_libmpr_base(void) {
    long fd = sc3(5 /* open */, (long)"/proc/self/maps", 0 /* O_RDONLY */, 0);
    if (fd < 0) return 0u;
    char buf[256], line[192];
    int lpos = 0, got;
    uint32_t base = 0u;
    while (!base && (got = (int)sc3(3 /* read */, fd, (long)buf, sizeof(buf))) > 0) {
        for (int k = 0; k < got; k++) {
            char ch = buf[k];
            if (ch != '\n') {
                if (lpos + 1 < (int)sizeof(line)) line[lpos++] = ch;
                continue;
            }
            line[lpos] = 0;
            uint32_t lo = 0u, off = 0u;
            int i = 0;
            while (line[i] && line[i] != '-') {
                char c = line[i++];
                uint32_t d = (c >= '0' && c <= '9') ? (uint32_t)(c - '0')
                                                    : (uint32_t)((c | 0x20) - 'a' + 10);
                lo = (lo << 4) | d;
            }
            while (line[i] && line[i] != ' ') i++;
            while (line[i] == ' ') i++;
            while (line[i] && line[i] != ' ') i++;
            while (line[i] == ' ') i++;
            while (line[i] && line[i] != ' ') {
                char c = line[i++];
                uint32_t d = (c >= '0' && c <= '9') ? (uint32_t)(c - '0')
                                                    : (uint32_t)((c | 0x20) - 'a' + 10);
                off = (off << 4) | d;
            }
            if (off == 0u && lpos >= 10) {
                const char *suf = "/libmpr.so";
                int match = 1;
                for (int s = 0; s < 10; s++) {
                    if (line[lpos - 10 + s] != suf[s]) {
                        match = 0;
                        break;
                    }
                }
                if (match) base = lo;
            }
            lpos = 0;
        }
    }
    sc3(6 /* close */, fd, 0, 0);
    return base;
}

static int proc_write_u32(uint32_t va, uint32_t want, int *wrote) {
    if ((va & 3u) != 0u) return -1;
    volatile uint32_t *p = (volatile uint32_t *)(uintptr_t)va;
    if (*p == want) {
        *wrote = 0;
        return 0;
    }
    uint32_t page = va & ~0xfffu;
    if (sc3(125 /* mprotect */, (long)page, 4096, 7 /* PROT_READ|WRITE|EXEC */) < 0) return -1;
    *p = want;
    __asm__ volatile("dsb sy" ::: "memory");
    int ok = (*p == want);
    sc3(125 /* mprotect */, (long)page, 4096, 5 /* PROT_READ|EXEC */);
    if (!ok) return -1;
    *wrote = 1;
    return 0;
}

static int proc_write_hw2_code(uint32_t va, uint16_t hw0, uint16_t hw1, int *wrote) {
    if ((va & 1u) != 0u) return -1;
    volatile uint16_t *p = (volatile uint16_t *)(uintptr_t)va;
    if (p[0] == hw0 && p[1] == hw1) {
        *wrote = 0;
        return 0;
    }
    uint32_t page = va & ~0xfffu;
    uint32_t span = ((va & 0xfffu) > 0xffcu) ? 8192u : 4096u;
    if (sc3(125 /* mprotect */, (long)page, (long)span, 7 /* PROT_READ|WRITE|EXEC */) < 0) return -1;
    p[0] = hw0;
    p[1] = hw1;
    __asm__ volatile("dsb sy" ::: "memory");
    arm_cacheflush(page, page + span);
    int ok = (p[0] == hw0 && p[1] == hw1);
    sc3(125 /* mprotect */, (long)page, (long)span, 5 /* PROT_READ|EXEC */);
    if (!ok) return -1;
    *wrote = 1;
    return 0;
}
#endif /* !OG_SENSOR_ONLY */

/* ---- Static target coordinate tables ---- */
static const u16_site_t SENSOR_4K_SITES[3] = {
    { PA_SEL24_4K, 0x201Fu, 0x2044u },
    { PA_SEL25_4K, 0x2089u, 0x2044u },
    { PA_SEL30_4K, 0x2013u, 0x2044u },
};

static const u16_site_t SENSOR_HD_SITES[3] = {
    { PA_SEL24_HD, 0x2007u, 0x2044u },
    { PA_SEL25_HD, 0x2005u, 0x2044u },
    { PA_SEL30_HD, 0x2005u, 0x2044u },
};

static const u16_site_t EXL_SITES[2] = {
    { PA_EXL_MAIN, 0x6F05u, 0x2500u },
    { PA_EXL_SUB,  0x6F19u, 0x2100u },
};

/* 6K Reg01 Full-Gate (6000x4000 @ 24p/25p) coordinate tables */
typedef struct {
    uint16_t off;
    uint16_t stock;
    uint16_t patched;
} off16_site_t;

static const u16_site_t REG01_COL11_SITES[6] = {
    { PA_REGLIST_R1_AL,  0x4141u, 0x4041u }, /* reg 0x0001 col 11: 0x41 (16:9 win) -> 0x40 (3:2 full gate) */
    { PA_REGLIST_R10_AL, 0x568Au, 0x1E8Au }, /* Y_START_LO: 342 -> 30 */
    { PA_REGLIST_R11_AL, 0x0102u, 0x0002u }, /* Y_START_HI: 342 -> 30 */
    { PA_REGLIST_R12_AL, 0x44DCu, 0xB4DCu }, /* Y_SIZE_LO:  3396 -> 4020 */
    { PA_REGLIST_R13_AL, 0x0D0Au, 0x0F0Au }, /* Y_SIZE_HI:  3396 -> 4020 */
    { PA_SELE_CRASH,     0x603Fu, 0xBF00u }, /* S-Ele str r7,[r7] -> nop crash guard */
};

static const off16_site_t RCV_6K_OFFS[6] = {
    { 0x014u, 3396u, 4020u },
    { 0x028u, 3392u, 4016u },
    { 0x054u, 3396u, 4020u },
    { 0x058u, 3376u, 4000u },
    { 0x06Au, 3392u, 4016u },
    { 0x070u, 3447u, 4071u },
};

static const off16_site_t FE_6K_OFFS[66] = {
    { 0x002u, 3442u, 4066u }, { 0x00Au, 3392u, 4016u }, { 0x012u, 3376u, 4000u }, { 0x016u, 3392u, 4016u },
    { 0x01Eu, 3392u, 4016u }, { 0x026u, 3376u, 4000u }, { 0x02Au, 3392u, 4016u }, { 0x032u, 3392u, 4016u },
    { 0x03Au, 3376u, 4000u }, { 0x03Eu, 3392u, 4016u }, { 0x046u, 3392u, 4016u }, { 0x04Eu, 3376u, 4000u },
    { 0x082u, 3392u, 4016u }, { 0x08Au, 3392u, 4016u }, { 0x092u, 3376u, 4000u }, { 0x0AAu, 3392u, 4016u },
    { 0x0B2u, 3392u, 4016u }, { 0x0BAu, 3376u, 4000u }, { 0x0BEu, 3392u, 4016u }, { 0x0C6u, 3392u, 4016u },
    { 0x0CEu, 3376u, 4000u }, { 0x0FAu, 3442u, 4066u }, { 0x108u, 3442u, 4066u }, { 0x114u, 3441u, 4065u },
    { 0x11Cu, 3441u, 4065u }, { 0x144u, 3442u, 4066u }, { 0x14Cu, 3441u, 4065u }, { 0x154u, 3441u, 4065u },
    { 0x15Cu, 3441u, 4065u }, { 0x160u, 3442u, 4066u }, { 0x190u, 3445u, 4069u }, { 0x198u, 3441u, 4065u },
    { 0x1A0u, 3441u, 4065u }, { 0x1A8u, 3441u, 4065u }, { 0x1B0u, 3441u, 4065u }, { 0x1B8u, 3441u, 4065u },
    { 0x1BCu, 3443u, 4067u }, { 0x1C0u, 3445u, 4069u }, { 0x1C4u, 3445u, 4069u }, { 0x1CCu, 3441u, 4065u },
    { 0x1D4u, 3445u, 4069u }, { 0x1E4u, 1711u, 2023u }, { 0x1ECu, 1695u, 2007u }, { 0x202u, 3392u, 4016u },
    { 0x206u, 3392u, 4016u }, { 0x20Eu, 5818u, 6888u }, { 0x218u, 3407u, 4031u }, { 0x220u, 3391u, 4015u },
    { 0x228u, 3391u, 4015u }, { 0x22Cu, 3407u, 4031u }, { 0x230u, 3401u, 4025u }, { 0x242u, 3392u, 4016u },
    { 0x250u,  848u, 1004u }, { 0x254u,  848u, 1004u }, { 0x3B8u, 3398u, 4022u }, { 0x3C0u, 3391u, 4015u },
    { 0x3C8u, 3391u, 4015u }, { 0x3CCu, 3394u, 4018u }, { 0x3E6u, 3392u, 4016u }, { 0x3F4u, 3398u, 4022u },
    { 0x3FCu, 3391u, 4015u }, { 0x404u, 3391u, 4015u }, { 0x408u, 3392u, 4016u }, { 0x40Cu, 3394u, 4018u },
    { 0x426u, 3391u, 4015u }, { 0x432u, 3391u, 4015u },
};

static const u32_site_t M16_M17_U32_SITES[18] = {
    { PA_M16_VACT,          3376u, 4000u },
    { PA_M17_VACT,          3376u, 4000u },
    { PA_T16_NTSC + 0x00u,   862u,  731u },
    { PA_T16_NTSC + 0x04u,  1740u, 2052u },
    { PA_T16_NTSC + 0x0Cu,   862u,  731u },
    { PA_T16_NTSC + 0x10u,  1740u, 2052u },
    { PA_T16_PAL  + 0x00u,   862u,  731u },
    { PA_T16_PAL  + 0x04u,  1740u, 2052u },
    { PA_T16_PAL  + 0x0Cu,   862u,  731u },
    { PA_T16_PAL  + 0x10u,  1740u, 2052u },
    { PA_T17_NTSC + 0x00u,   827u,  701u },
    { PA_T17_NTSC + 0x04u,  1740u, 2052u },
    { PA_T17_NTSC + 0x0Cu,   827u,  701u },
    { PA_T17_NTSC + 0x10u,  1740u, 2052u },
    { PA_T17_PAL  + 0x00u,   827u,  701u },
    { PA_T17_PAL  + 0x04u,  1740u, 2052u },
    { PA_T17_PAL  + 0x0Cu,   827u,  701u },
    { PA_T17_PAL  + 0x10u,  1740u, 2052u },
};

#ifndef OG_SENSOR_ONLY
static const u32_site_t AVCAM_4K_U32_SITES[11] = {
    { PA_DMM_0 + 4u * 24u + 0x00u, 3840u, 3240u },
    { PA_DMM_0 + 4u * 24u + 0x08u, 3840u, 3264u },
    { PA_DMM_1 + 4u * 24u + 0x00u, 3840u, 3240u },
    { PA_DMM_1 + 4u * 24u + 0x08u, 3840u, 3264u },
    { PA_DMM_2 + 4u * 24u + 0x00u, 3840u, 3240u },
    { PA_DMM_2 + 4u * 24u + 0x08u, 3840u, 3264u },
    { PA_VPI_0 + 0x00u,            3840u, 3240u },
    { PA_VPI_0 + 0x0Cu,            3840u, 3240u },
    { PA_VPI_1 + 0x00u,            3840u, 3240u },
    { PA_VPI_1 + 0x08u,            3840u, 3240u },
    { PA_VPI_2 + 0x00u,            3840u, 3240u },
};

static const u32_site_t AVCAM_HD_U32_SITES[9] = {
    { PA_DMM_0 + 1u * 24u + 0x00u, 1920u, 1620u },
    { PA_DMM_0 + 1u * 24u + 0x08u, 1920u, 1664u },
    { PA_DMM_1 + 1u * 24u + 0x00u, 1920u, 1620u },
    { PA_DMM_1 + 1u * 24u + 0x08u, 1920u, 1664u },
    { PA_DMM_2 + 1u * 24u + 0x00u, 1920u, 1620u },
    { PA_DMM_2 + 1u * 24u + 0x08u, 1920u, 1664u },
    { PA_VPI_0 + 0x18u,            1920u, 1620u },
    { PA_VPI_1 + 0x14u,            1920u, 1620u },
    { PA_VPI_1 + 0x1Cu,            1920u, 1620u },
};

#ifdef TARGET_ILCE6400A
static const uint32_t VENC_4K_PAS[14] = {
    0x03E3959Cu, 0x03E395FCu, 0x03E3977Cu, 0x03E397E0u, 0x03E39844u, 0x03E398A8u, 0x03E3990Cu,
    0x03E39970u, 0x03E3A5F0u, 0x03E3A654u, 0x03E3A6B8u, 0x03E3A71Cu, 0x03E3A780u, 0x03E3A7E4u,
};
static const uint32_t VENC_HD_PAS[28] = {
    0x03E3965Cu, 0x03E396BCu, 0x03E399D4u, 0x03E39A38u, 0x03E39A9Cu, 0x03E39B00u, 0x03E39B64u,
    0x03E39BC8u, 0x03E39C2Cu, 0x03E39C90u, 0x03E39CF4u, 0x03E39D58u, 0x03E39DBCu, 0x03E39E20u,
    0x03E39E84u, 0x03E39EE8u, 0x03E39F4Cu, 0x03E39FB0u, 0x03E3A014u, 0x03E3A078u, 0x03E3A0DCu,
    0x03E3A140u, 0x03E3A1A4u, 0x03E3A208u, 0x03E3A26Cu, 0x03E3A2D0u, 0x03E3A334u, 0x03E3A848u,
};
static const uint32_t MPR_4K_U32_OFFS[6] = {
    0x00A2F3D4u, 0x00A2F3E4u, 0x00A2F3F4u, 0x00A2F404u, 0x00A2F414u, 0x00A5A130u,
};
static const uint32_t MPR_4K_U16_OFFS[4] = {
    0x007C81E0u, 0x007C9A78u, 0x008EBE20u, 0x00A5A37Cu,
};
static const uint32_t MPR_HD_U32_OFFS[6] = {
    0x00A2F4B4u, 0x00A2F4D4u, 0x00A2F4F4u, 0x00A2F504u, 0x00A2F514u, 0x00A5A094u,
};
static const uint32_t MPR_HD_U16_OFFS[3] = {
    0x007C9A74u, 0x008EBE1Cu, 0x00A5A2BCu,
};
#else
static const uint32_t VENC_4K_PAS[14] = {
    0x03E369F4u, 0x03E36A54u, 0x03E36BD4u, 0x03E36C38u, 0x03E36C9Cu, 0x03E36D00u, 0x03E36D64u,
    0x03E36DC8u, 0x03E37A48u, 0x03E37AACu, 0x03E37B10u, 0x03E37B74u, 0x03E37BD8u, 0x03E37C3Cu,
};
static const uint32_t VENC_HD_PAS[28] = {
    0x03E36AB4u, 0x03E36B14u, 0x03E36E2Cu, 0x03E36E90u, 0x03E36EF4u, 0x03E36F58u, 0x03E36FBCu,
    0x03E37020u, 0x03E37084u, 0x03E370E8u, 0x03E3714Cu, 0x03E371B0u, 0x03E37214u, 0x03E37278u,
    0x03E372DCu, 0x03E37340u, 0x03E373A4u, 0x03E37408u, 0x03E3746Cu, 0x03E374D0u, 0x03E37534u,
    0x03E37598u, 0x03E375FCu, 0x03E37660u, 0x03E376C4u, 0x03E37728u, 0x03E3778Cu, 0x03E37CA0u,
};
static const uint32_t MPR_4K_U32_OFFS[6] = {
    0x00A2F114u, 0x00A2F124u, 0x00A2F134u, 0x00A2F144u, 0x00A2F154u, 0x00A59E78u,
};
static const uint32_t MPR_4K_U16_OFFS[4] = {
    0x007C7FD0u, 0x007C9868u, 0x008EBC28u, 0x00A5A0C4u,
};
static const uint32_t MPR_HD_U32_OFFS[6] = {
    0x00A2F1F4u, 0x00A2F214u, 0x00A2F234u, 0x00A2F244u, 0x00A2F254u, 0x00A59DDCu,
};
static const uint32_t MPR_HD_U16_OFFS[3] = {
    0x007C9864u, 0x008EBC24u, 0x00A5A004u,
};
#endif
#endif /* !OG_SENSOR_ONLY */

static int valid_t10_hv(uint32_t h, uint32_t v) {
    return (h == 660u && v == 1820u) ||
           (h == 660u && v == 2180u) ||
           (h == 660u && v == 2275u) ||
           (h == 600u && v == 2400u);
}

static void og_init_bss_if_needed(struct og_bss_state *s) {
    if (s->magic != OG_STATE_MAGIC) {
        s->magic = OG_STATE_MAGIC;
        s->active = 0u;
        s->apply_count = 0u;
        s->last_written = 0u;
        s->last_unchanged = 0u;
        s->last_error_code = 0u;
        s->last_error_idx = 0u;
        s->rt_saved_valid = 0u;
        s->rt_orig_h = 0u;
        s->rt_orig_v = 0u;
        s->last_mpr_base = 0u;
    }
}

static int valid_rt_m16(uint32_t h, uint32_t v, uint32_t vact) {
    return (h == 862u && v == 1740u && vact == 3376u) ||
           (h == 731u && v == 2052u && vact == 4000u);
}

static int valid_rt_m17(uint32_t h, uint32_t v, uint32_t vact) {
    return (h == 827u && v == 1740u && vact == 3376u) ||
           (h == 701u && v == 2052u && vact == 4000u);
}

static void og_apply(int enable, int reason) {
    struct og_bss_state *s = OG_BSS;
    og_init_bss_if_needed(s);

#ifdef OG_SENSOR_ONLY
    uint8_t gate_mode  = (s->cfg[0] <= 2u) ? s->cfg[0] : 0u;
    uint8_t canvas_fmt = 3u;
    uint8_t fps_mode   = (s->cfg[1] <= 2u) ? s->cfg[1] : 0u;
    uint8_t scope_mode = (s->cfg[2] <= 2u) ? s->cfg[2] : 0u;
#else
    uint8_t gate_mode  = (s->cfg[0] <= 2u) ? s->cfg[0] : 0u;
    uint8_t canvas_fmt = (s->cfg[1] <= 3u) ? s->cfg[1] : 0u;
    uint8_t fps_mode   = (s->cfg[2] <= 2u) ? s->cfg[2] : 0u;
    uint8_t scope_mode = (s->cfg[3] <= 2u) ? s->cfg[3] : 0u;
#endif

    int scope_has_4k   = (scope_mode == 0u || scope_mode == 1u);
    int scope_has_hd   = (scope_mode == 0u || scope_mode == 2u);

    /* gate_mode == 0: 5.1K True 4K keeps 4K on stock Mode 16/17/15 (5064x3376 / 4140x2760), HD on Mode 10
     * gate_mode == 1: 6K Reg01 Full keeps 4K on Mode 16/17/15 + applies reg 0x0001=0x40 & dy=+624 (6000x4000)
     * gate_mode == 2: Full-FOV 2K routes both 4K and HD (per scope) to Mode 10 (1996x1332 uncropped) */
    int want_sensor_4k = enable && scope_has_4k && (gate_mode == 2u);
    int want_6k_reg01  = enable && scope_has_4k && (gate_mode == 1u);
    int want_sensor_hd = enable && scope_has_hd;
    int want_exl       = enable ? 1 : 0;
    int want_m10_tim   = enable && (want_sensor_4k || want_sensor_hd) && (fps_mode <= 1u);

    uint32_t want_ntsc_h = 660u;
    uint32_t want_ntsc_v = (want_m10_tim && fps_mode == 0u) ? 2275u : 1820u;
    uint32_t want_pal_h  = want_m10_tim ? 600u  : 660u;
    uint32_t want_pal_v  = want_m10_tim ? 2400u : 2180u;

#ifndef OG_SENSOR_ONLY
    int want_canvas_4k = enable && (canvas_fmt == 0u || canvas_fmt == 2u);
    int want_canvas_hd = enable && (canvas_fmt == 1u || canvas_fmt == 2u);
    int want_buf_sel   = want_canvas_4k || want_canvas_hd;
#endif

    lbuf_t l;
    l.n = 0;
    lb_str(&l, enable ? "OG32_ON rsn=" : "OG32_OFF rsn=");
    lb_hex(&l, (uint32_t)reason, 1);
    lb_str(&l, " cfg=");
    lb_hex(&l, gate_mode, 1);
    lb_hex(&l, canvas_fmt, 1);
    lb_hex(&l, fps_mode, 1);
    lb_hex(&l, scope_mode, 1);

    long mem_fd = sc3(5 /* open */, (long)"/dev/mem", 02 | 04010000 /* O_RDWR | O_SYNC */, 0);
    if (mem_fd < 0) {
        s->last_error_code = 1u;
        s->last_error_idx = 0u;
        lb_str(&l, " err=open\n");
        lb_flush(&l);
        return;
    }

    if (check_oril_identity(mem_fd) != 0) {
        sc3(6 /* close */, mem_fd, 0, 0);
        s->last_error_code = 2u;
        s->last_error_idx = 0u;
        lb_str(&l, " err=oril_hdr\n");
        lb_flush(&l);
        return;
    }

    /* ---- Phase 1: Complete read-only preflight across all target sites ---- */
    uint32_t idx = 0u;
    uint16_t cur16 = 0u;
    uint32_t cur32 = 0u;
    int pre_fail = 0;

    for (int i = 0; i < 3 && !pre_fail; i++, idx++) {
        if (phys_read_u16(mem_fd, SENSOR_4K_SITES[i].pa, &cur16) != 0 ||
            (cur16 != SENSOR_4K_SITES[i].stock && cur16 != SENSOR_4K_SITES[i].patched))
            pre_fail = 1;
    }
    for (int i = 0; i < 3 && !pre_fail; i++, idx++) {
        if (phys_read_u16(mem_fd, SENSOR_HD_SITES[i].pa, &cur16) != 0 ||
            (cur16 != SENSOR_HD_SITES[i].stock && cur16 != SENSOR_HD_SITES[i].patched))
            pre_fail = 1;
    }
    for (int i = 0; i < 2 && !pre_fail; i++, idx++) {
        if (phys_read_u16(mem_fd, EXL_SITES[i].pa, &cur16) != 0 ||
            (cur16 != EXL_SITES[i].stock && cur16 != EXL_SITES[i].patched))
            pre_fail = 1;
    }

    uint32_t nh1 = 0, nv1 = 0, nh2 = 0, nv2 = 0;
    uint32_t ph1 = 0, pv1 = 0, ph2 = 0, pv2 = 0;
    if (!pre_fail) {
        idx = 0x10u;
        if (phys_read_u32(mem_fd, PA_T10_NTSC + 0x00u, &nh1) != 0 ||
            phys_read_u32(mem_fd, PA_T10_NTSC + 0x04u, &nv1) != 0 ||
            phys_read_u32(mem_fd, PA_T10_NTSC + 0x0Cu, &nh2) != 0 ||
            phys_read_u32(mem_fd, PA_T10_NTSC + 0x10u, &nv2) != 0 ||
            !valid_t10_hv(nh1, nv1) || !valid_t10_hv(nh2, nv2))
            pre_fail = 1;
    }
    if (!pre_fail) {
        idx = 0x11u;
        if (phys_read_u32(mem_fd, PA_T10_PAL + 0x00u, &ph1) != 0 ||
            phys_read_u32(mem_fd, PA_T10_PAL + 0x04u, &pv1) != 0 ||
            phys_read_u32(mem_fd, PA_T10_PAL + 0x0Cu, &ph2) != 0 ||
            phys_read_u32(mem_fd, PA_T10_PAL + 0x10u, &pv2) != 0 ||
            !valid_t10_hv(ph1, pv1) || !valid_t10_hv(ph2, pv2))
            pre_fail = 1;
    }

    uint32_t rth1 = 0, rtv1 = 0, rth2 = 0, rtv2 = 0;
    int rt_populated = 0;
    if (!pre_fail) {
        idx = 0x12u;
        if (phys_read_u32(mem_fd, PA_RT_T14_M10 + 0x08u, &rth1) != 0 ||
            phys_read_u32(mem_fd, PA_RT_T14_M10 + 0x0Cu, &rtv1) != 0 ||
            phys_read_u32(mem_fd, PA_RT_T14_M10 + 0x68u, &rth2) != 0 ||
            phys_read_u32(mem_fd, PA_RT_T14_M10 + 0x6Cu, &rtv2) != 0) {
            pre_fail = 1;
        } else if (rth1 == 0u && rtv1 == 0u && rth2 == 0u && rtv2 == 0u) {
            rt_populated = 0;
        } else if (valid_t10_hv(rth1, rtv1) && valid_t10_hv(rth2, rtv2)) {
            rt_populated = 1;
        } else {
            pre_fail = 1;
        }
    }

    int rt16_populated = 0;
    if (!pre_fail) {
        for (int i = 0; i < 6 && !pre_fail; i++) {
            idx = 0x13u;
            if (phys_read_u16(mem_fd, REG01_COL11_SITES[i].pa, &cur16) != 0 ||
                (cur16 != REG01_COL11_SITES[i].stock && cur16 != REG01_COL11_SITES[i].patched))
                pre_fail = 1;
        }
        for (int i = 0; i < 6 && !pre_fail; i++) {
            idx = 0x14u;
            if (phys_read_u16(mem_fd, PA_RCV_BASE + RCV_6K_OFFS[i].off, &cur16) != 0 ||
                (cur16 != RCV_6K_OFFS[i].stock && cur16 != RCV_6K_OFFS[i].patched))
                pre_fail = 1;
        }
        for (int i = 0; i < 66 && !pre_fail; i++) {
            idx = 0x15u;
            uint16_t f6 = 0u, f9 = 0u;
            if (phys_read_u16(mem_fd, PA_FE6_BASE + FE_6K_OFFS[i].off, &f6) != 0 ||
                phys_read_u16(mem_fd, PA_FE9_BASE + FE_6K_OFFS[i].off, &f9) != 0 ||
                (f6 != FE_6K_OFFS[i].stock && f6 != FE_6K_OFFS[i].patched) ||
                (f9 != FE_6K_OFFS[i].stock && f9 != FE_6K_OFFS[i].patched))
                pre_fail = 1;
        }
        for (int i = 0; i < 18 && !pre_fail; i++) {
            idx = 0x16u;
            if (phys_read_u32(mem_fd, M16_M17_U32_SITES[i].pa, &cur32) != 0 ||
                (cur32 != M16_M17_U32_SITES[i].stock && cur32 != M16_M17_U32_SITES[i].patched))
                pre_fail = 1;
        }
        if (!pre_fail) {
            idx = 0x17u;
            uint32_t m16h1 = 0, m16v1 = 0, m16va = 0, m16h2 = 0, m16v2 = 0;
            uint32_t m17h1 = 0, m17v1 = 0, m17va = 0, m17h2 = 0, m17v2 = 0;
            if (phys_read_u32(mem_fd, PA_RT_T14_M16 + 0x08u, &m16h1) != 0 ||
                phys_read_u32(mem_fd, PA_RT_T14_M16 + 0x0Cu, &m16v1) != 0 ||
                phys_read_u32(mem_fd, PA_RT_T14_M16 + 0x50u, &m16va) != 0 ||
                phys_read_u32(mem_fd, PA_RT_T14_M16 + 0x68u, &m16h2) != 0 ||
                phys_read_u32(mem_fd, PA_RT_T14_M16 + 0x6Cu, &m16v2) != 0 ||
                phys_read_u32(mem_fd, PA_RT_T14_M17 + 0x08u, &m17h1) != 0 ||
                phys_read_u32(mem_fd, PA_RT_T14_M17 + 0x0Cu, &m17v1) != 0 ||
                phys_read_u32(mem_fd, PA_RT_T14_M17 + 0x50u, &m17va) != 0 ||
                phys_read_u32(mem_fd, PA_RT_T14_M17 + 0x68u, &m17h2) != 0 ||
                phys_read_u32(mem_fd, PA_RT_T14_M17 + 0x6Cu, &m17v2) != 0) {
                pre_fail = 1;
            } else if (!m16h1 && !m16v1 && !m16va && !m16h2 && !m16v2 &&
                       !m17h1 && !m17v1 && !m17va && !m17h2 && !m17v2) {
                rt16_populated = 0;
            } else if (valid_rt_m16(m16h1, m16v1, m16va) && valid_rt_m16(m16h2, m16v2, m16va) &&
                       valid_rt_m17(m17h1, m17v1, m17va) && valid_rt_m17(m17h2, m17v2, m17va)) {
                rt16_populated = 1;
            } else {
                pre_fail = 1;
            }
        }
    }

#ifndef OG_SENSOR_ONLY
    if (!pre_fail) {
        idx = 0x20u;
        if (phys_read_u16(mem_fd, PA_BUF_BNE, &cur16) != 0 ||
            (cur16 != 0xD112u && cur16 != 0xBF00u))
            pre_fail = 1;
    }
    for (int i = 0; i < 11 && !pre_fail; i++) {
        idx = 0x30u + (uint32_t)i;
        if (phys_read_u32(mem_fd, AVCAM_4K_U32_SITES[i].pa, &cur32) != 0 ||
            (cur32 != AVCAM_4K_U32_SITES[i].stock && cur32 != AVCAM_4K_U32_SITES[i].patched))
            pre_fail = 1;
    }
    for (int i = 0; i < 14 && !pre_fail; i++) {
        idx = 0x40u + (uint32_t)i;
        if (phys_read_u32(mem_fd, VENC_4K_PAS[i], &cur32) != 0 ||
            (cur32 != U16_3840_2160 && cur32 != U16_3240_2160))
            pre_fail = 1;
    }
    for (int i = 0; i < 9 && !pre_fail; i++) {
        idx = 0x50u + (uint32_t)i;
        if (phys_read_u32(mem_fd, AVCAM_HD_U32_SITES[i].pa, &cur32) != 0 ||
            (cur32 != AVCAM_HD_U32_SITES[i].stock && cur32 != AVCAM_HD_U32_SITES[i].patched))
            pre_fail = 1;
    }
    for (int i = 0; i < 28 && !pre_fail; i++) {
        idx = 0x60u + (uint32_t)i;
        if (phys_read_u32(mem_fd, VENC_HD_PAS[i], &cur32) != 0 ||
            (cur32 != U16_1920_1080 && cur32 != U16_1620_1080))
            pre_fail = 1;
    }

    uint32_t mpr_base = find_libmpr_base();
    s->last_mpr_base = mpr_base;
    if (!pre_fail && !mpr_base && (want_canvas_4k || want_canvas_hd || !enable)) {
        /* If disabling while mpr_base is unmapped, fail closed only if canvas was needed;
         * in live im.elf /proc/self/maps always maps libmpr.so. */
        if (want_canvas_4k || want_canvas_hd) {
            sc3(6 /* close */, mem_fd, 0, 0);
            s->last_error_code = 4u;
            s->last_error_idx = 0x80u;
            lb_str(&l, " err=mpr_map\n");
            lb_flush(&l);
            return;
        }
    }
    if (!pre_fail && mpr_base) {
        const volatile uint32_t *ehdr = (const volatile uint32_t *)(uintptr_t)mpr_base;
        if (ehdr[0] != 0x464C457Fu /* "\x7fELF" */) {
            pre_fail = 1;
            idx = 0x81u;
        }
        if (!pre_fail) {
            const volatile uint16_t *p4k = (const volatile uint16_t *)(uintptr_t)(mpr_base + MPR_ENCOPEN_4K_OFF);
            if (!((p4k[0] == MPR_MOV_3840_HW0 && p4k[1] == MPR_MOV_3840_HW1) ||
                  (p4k[0] == MPR_MOV_3240_HW0 && p4k[1] == MPR_MOV_3240_HW1))) {
                pre_fail = 1;
                idx = 0x82u;
            }
        }
        if (!pre_fail) {
            const volatile uint16_t *phd = (const volatile uint16_t *)(uintptr_t)(mpr_base + MPR_ENCOPEN_HD_OFF);
            if (!((phd[0] == MPR_MOV_1920_HW0 && phd[1] == MPR_MOV_1920_HW1) ||
                  (phd[0] == MPR_MOV_1620_HW0 && phd[1] == MPR_MOV_1620_HW1))) {
                pre_fail = 1;
                idx = 0x83u;
            }
        }
        for (int i = 0; i < 6 && !pre_fail; i++) {
            uint32_t v = *(const volatile uint32_t *)(uintptr_t)(mpr_base + MPR_4K_U32_OFFS[i]);
            if (v != 3840u && v != 3240u) { pre_fail = 1; idx = 0x90u + (uint32_t)i; }
        }
        for (int i = 0; i < 4 && !pre_fail; i++) {
            uint32_t v = *(const volatile uint32_t *)(uintptr_t)(mpr_base + MPR_4K_U16_OFFS[i]);
            if (v != U16_3840_2160 && v != U16_3240_2160) { pre_fail = 1; idx = 0x98u + (uint32_t)i; }
        }
        for (int i = 0; i < 6 && !pre_fail; i++) {
            uint32_t v = *(const volatile uint32_t *)(uintptr_t)(mpr_base + MPR_HD_U32_OFFS[i]);
            if (v != 1920u && v != 1620u) { pre_fail = 1; idx = 0xA0u + (uint32_t)i; }
        }
        for (int i = 0; i < 3 && !pre_fail; i++) {
            uint32_t v = *(const volatile uint32_t *)(uintptr_t)(mpr_base + MPR_HD_U16_OFFS[i]);
            if (v != U16_1920_1080 && v != U16_1620_1080) { pre_fail = 1; idx = 0xA8u + (uint32_t)i; }
        }
    }
#endif /* !OG_SENSOR_ONLY */

    if (pre_fail) {
        sc3(6 /* close */, mem_fd, 0, 0);
        s->last_error_code = 3u;
        s->last_error_idx = idx;
        lb_str(&l, " err=preflight idx=");
        lb_hex(&l, idx, 2);
        lb_ch(&l, '\n');
        lb_flush(&l);
        return;
    }

    /* ---- Phase 2: Commit validated target state with readback ---- */
    if (rt_populated && !s->rt_saved_valid &&
        ((rth1 == 660u && rtv1 == 1820u) || (rth1 == 660u && rtv1 == 2180u))) {
        s->rt_orig_h = rth1;
        s->rt_orig_v = rtv1;
        s->rt_saved_valid = 1u;
    }

    uint32_t written = 0u, unchanged = 0u;
    int wr_err = 0, w = 0;

#define ACC_W(call_expr) do { \
    if ((call_expr) != 0) { wr_err = 1; } \
    else if (w) { written++; } else { unchanged++; } \
} while (0)

    for (int i = 0; i < 3 && !wr_err; i++) {
        uint16_t want = want_sensor_4k ? SENSOR_4K_SITES[i].patched : SENSOR_4K_SITES[i].stock;
        ACC_W(phys_write_u16(mem_fd, SENSOR_4K_SITES[i].pa, want, &w));
    }
    for (int i = 0; i < 3 && !wr_err; i++) {
        uint16_t want = want_sensor_hd ? SENSOR_HD_SITES[i].patched : SENSOR_HD_SITES[i].stock;
        ACC_W(phys_write_u16(mem_fd, SENSOR_HD_SITES[i].pa, want, &w));
    }
    for (int i = 0; i < 2 && !wr_err; i++) {
        uint16_t want = want_exl ? EXL_SITES[i].patched : EXL_SITES[i].stock;
        ACC_W(phys_write_u16(mem_fd, EXL_SITES[i].pa, want, &w));
    }

    if (!wr_err) {
        ACC_W(phys_write_u32(mem_fd, PA_T10_NTSC + 0x00u, want_ntsc_h, &w));
        ACC_W(phys_write_u32(mem_fd, PA_T10_NTSC + 0x04u, want_ntsc_v, &w));
        ACC_W(phys_write_u32(mem_fd, PA_T10_NTSC + 0x0Cu, want_ntsc_h, &w));
        ACC_W(phys_write_u32(mem_fd, PA_T10_NTSC + 0x10u, want_ntsc_v, &w));
        ACC_W(phys_write_u32(mem_fd, PA_T10_PAL  + 0x00u, want_pal_h,  &w));
        ACC_W(phys_write_u32(mem_fd, PA_T10_PAL  + 0x04u, want_pal_v,  &w));
        ACC_W(phys_write_u32(mem_fd, PA_T10_PAL  + 0x0Cu, want_pal_h,  &w));
        ACC_W(phys_write_u32(mem_fd, PA_T10_PAL  + 0x10u, want_pal_v,  &w));
    }

    if (!wr_err && rt_populated) {
        uint32_t base_h = s->rt_saved_valid ? s->rt_orig_h : 660u;
        uint32_t base_v = s->rt_saved_valid ? s->rt_orig_v : 1820u;
        uint32_t want_rt_h = base_h;
        uint32_t want_rt_v = base_v;
        if (want_m10_tim) {
            if (base_v == 2180u) {
                want_rt_h = want_pal_h;
                want_rt_v = want_pal_v;
            } else {
                want_rt_h = want_ntsc_h;
                want_rt_v = want_ntsc_v;
            }
        }
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M10 + 0x08u, want_rt_h, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M10 + 0x0Cu, want_rt_v, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M10 + 0x68u, want_rt_h, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M10 + 0x6Cu, want_rt_v, &w));
    }

    for (int i = 0; i < 6 && !wr_err; i++) {
        uint16_t want = want_6k_reg01 ? REG01_COL11_SITES[i].patched : REG01_COL11_SITES[i].stock;
        ACC_W(phys_write_u16(mem_fd, REG01_COL11_SITES[i].pa, want, &w));
    }
    for (int i = 0; i < 6 && !wr_err; i++) {
        uint16_t want = want_6k_reg01 ? RCV_6K_OFFS[i].patched : RCV_6K_OFFS[i].stock;
        ACC_W(phys_write_u16(mem_fd, PA_RCV_BASE + RCV_6K_OFFS[i].off, want, &w));
    }
    for (int i = 0; i < 66 && !wr_err; i++) {
        uint16_t want = want_6k_reg01 ? FE_6K_OFFS[i].patched : FE_6K_OFFS[i].stock;
        ACC_W(phys_write_u16(mem_fd, PA_FE6_BASE + FE_6K_OFFS[i].off, want, &w));
        if (!wr_err)
            ACC_W(phys_write_u16(mem_fd, PA_FE9_BASE + FE_6K_OFFS[i].off, want, &w));
    }
    for (int i = 0; i < 18 && !wr_err; i++) {
        uint32_t want = want_6k_reg01 ? M16_M17_U32_SITES[i].patched : M16_M17_U32_SITES[i].stock;
        ACC_W(phys_write_u32(mem_fd, M16_M17_U32_SITES[i].pa, want, &w));
    }
    if (!wr_err && rt16_populated) {
        uint32_t m16h = want_6k_reg01 ? 731u  : 862u;
        uint32_t m16v = want_6k_reg01 ? 2052u : 1740u;
        uint32_t m17h = want_6k_reg01 ? 701u  : 827u;
        uint32_t m17v = want_6k_reg01 ? 2052u : 1740u;
        uint32_t vact = want_6k_reg01 ? 4000u : 3376u;
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M16 + 0x08u, m16h, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M16 + 0x0Cu, m16v, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M16 + 0x50u, vact, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M16 + 0x68u, m16h, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M16 + 0x6Cu, m16v, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M17 + 0x08u, m17h, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M17 + 0x0Cu, m17v, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M17 + 0x50u, vact, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M17 + 0x68u, m17h, &w));
        ACC_W(phys_write_u32(mem_fd, PA_RT_T14_M17 + 0x6Cu, m17v, &w));
    }

#ifndef OG_SENSOR_ONLY
    if (!wr_err) {
        ACC_W(phys_write_u16(mem_fd, PA_BUF_BNE, want_buf_sel ? 0xBF00u : 0xD112u, &w));
    }
    for (int i = 0; i < 11 && !wr_err; i++) {
        uint32_t want = want_canvas_4k ? AVCAM_4K_U32_SITES[i].patched : AVCAM_4K_U32_SITES[i].stock;
        ACC_W(phys_write_u32(mem_fd, AVCAM_4K_U32_SITES[i].pa, want, &w));
    }
    for (int i = 0; i < 14 && !wr_err; i++) {
        uint32_t want = want_canvas_4k ? U16_3240_2160 : U16_3840_2160;
        ACC_W(phys_write_u32(mem_fd, VENC_4K_PAS[i], want, &w));
    }
    for (int i = 0; i < 9 && !wr_err; i++) {
        uint32_t want = want_canvas_hd ? AVCAM_HD_U32_SITES[i].patched : AVCAM_HD_U32_SITES[i].stock;
        ACC_W(phys_write_u32(mem_fd, AVCAM_HD_U32_SITES[i].pa, want, &w));
    }
    for (int i = 0; i < 28 && !wr_err; i++) {
        uint32_t want = want_canvas_hd ? U16_1620_1080 : U16_1920_1080;
        ACC_W(phys_write_u32(mem_fd, VENC_HD_PAS[i], want, &w));
    }
#endif

    sc3(6 /* close */, mem_fd, 0, 0);

#ifndef OG_SENSOR_ONLY
    if (!wr_err && mpr_base) {
        ACC_W(proc_write_hw2_code(
            mpr_base + MPR_ENCOPEN_4K_OFF,
            want_canvas_4k ? MPR_MOV_3240_HW0 : MPR_MOV_3840_HW0,
            want_canvas_4k ? MPR_MOV_3240_HW1 : MPR_MOV_3840_HW1,
            &w));
        ACC_W(proc_write_hw2_code(
            mpr_base + MPR_ENCOPEN_HD_OFF,
            want_canvas_hd ? MPR_MOV_1620_HW0 : MPR_MOV_1920_HW0,
            want_canvas_hd ? MPR_MOV_1620_HW1 : MPR_MOV_1920_HW1,
            &w));
        for (int i = 0; i < 6 && !wr_err; i++) {
            ACC_W(proc_write_u32(mpr_base + MPR_4K_U32_OFFS[i], want_canvas_4k ? 3240u : 3840u, &w));
        }
        for (int i = 0; i < 4 && !wr_err; i++) {
            ACC_W(proc_write_u32(mpr_base + MPR_4K_U16_OFFS[i],
                                 want_canvas_4k ? U16_3240_2160 : U16_3840_2160, &w));
        }
        for (int i = 0; i < 6 && !wr_err; i++) {
            ACC_W(proc_write_u32(mpr_base + MPR_HD_U32_OFFS[i], want_canvas_hd ? 1620u : 1920u, &w));
        }
        for (int i = 0; i < 3 && !wr_err; i++) {
            ACC_W(proc_write_u32(mpr_base + MPR_HD_U16_OFFS[i],
                                 want_canvas_hd ? U16_1620_1080 : U16_1920_1080, &w));
        }
    }
#endif

#undef ACC_W

    s->active = enable ? 1u : 0u;
    s->apply_count++;
    s->last_written = written;
    s->last_unchanged = unchanged;
    s->last_error_code = wr_err ? 5u : 0u;
    s->last_error_idx = 0u;

    lb_str(&l, wr_err ? " st=write_err w=" : " st=ok w=");
    lb_hex(&l, written, 3);
    lb_str(&l, " u=");
    lb_hex(&l, unchanged, 3);
    lb_str(&l, " nhv=");
    lb_hex(&l, want_ntsc_h, 3);
    lb_ch(&l, 'x');
    lb_hex(&l, want_ntsc_v, 3);
#ifndef OG_SENSOR_ONLY
    lb_str(&l, " mpr=");
    lb_hex(&l, mpr_base, 8);
#endif
    lb_ch(&l, '\n');
    lb_flush(&l);
}

__attribute__((visibility("default"))) void og_on(int reason) {
    (void)reason;
    og_apply(1, 1);
}

__attribute__((visibility("default"))) void og_off(int reason) {
    (void)reason;
    og_apply(0, 0);
}

__attribute__((visibility("default"))) void og_cfg(int reason) {
    if (OG_BSS->active) {
        og_apply(1, reason);
    }
}
