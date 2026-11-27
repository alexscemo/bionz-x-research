/* OpenGateA1: SMOD v2 Path A1 (3:2 Open Gate) mod for Sony ILCE-6400 (FW 2.00).
 *
 * 1. av-cam.bin RAM (A-prime 5064x3376 exact 3:2): patches size_rows[16/17]
 *    and rcv_desc m16 horizontal crop (10 entries, keeping FE records stock).
 * 2. libmpr.so MP4 container hooks (tkhd 0x87072C & stsd 0x870A74): sets
 *    3840x2160 display/sample-entry height to 2560 (3840 / 1.5 = 2560).
 * 3. viewUnified2.so P1 (0x77D110..0x77D5D0): changes Movie 16:9 rear LCD box
 *    (0, 60, 640, 360) to 3:2 (0, 26, 640, 428).
 *
 * Fail-closed: ON writes only if stock bytes match; OFF restores exact stock.
 * Logs to /tmp_bt/mods/opengate_a1.log.
 */
#include <stdint.h>

typedef uint32_t (*tkhd_fn_t)(uint32_t model, uint32_t w, uint32_t h, uint32_t kind);
typedef uint32_t (*stsd_fn_t)(uint32_t ctx, const void *desc, uint32_t arg2);

__attribute__((visibility("default"))) uint32_t tkhd_hook_c(
    uint32_t model, uint32_t w, uint32_t h, uint32_t kind) {
    /* r4 holds ml_orig_tkhd | 1 from trampoline */
    register uint32_t orig_addr __asm__("r4");
    tkhd_fn_t orig = (tkhd_fn_t)(uintptr_t)orig_addr;
    if (*(const volatile uint32_t *)(uintptr_t)(model + 0xd8u) != 0u) {
        return 4u;
    }
    if (kind == 5u && w == (3840u << 16) && h == (2160u << 16)) {
        h = (2560u << 16);
    }
    return orig(model, w, h, kind);
}

__attribute__((visibility("default"))) uint32_t stsd_hook_c(
    uint32_t ctx, const void *desc, uint32_t arg2) {
    register uint32_t orig_addr __asm__("r4");
    stsd_fn_t orig = (stsd_fn_t)(uintptr_t)orig_addr;
    if (desc) {
        const uint16_t *d16 = (const uint16_t *)desc;
        if (d16[4] == 3840u && d16[5] == 2160u) {
            uint32_t tmp[8];
            const uint32_t *s32 = (const uint32_t *)desc;
            for (int i = 0; i < 8; i++) tmp[i] = s32[i];
            ((uint16_t *)tmp)[5] = 2560u;
            return orig(ctx, tmp, arg2);
        }
    }
    return orig(ctx, desc, arg2);
}

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

static uint32_t find_lib_base(const char *suffix, int slen) {
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
                uint32_t d = (c >= '0' && c <= '9') ? (uint32_t)(c - '0') : (uint32_t)((c | 0x20) - 'a' + 10);
                lo = (lo << 4) | d;
            }
            while (line[i] && line[i] != ' ') i++;
            while (line[i] == ' ') i++;
            while (line[i] && line[i] != ' ') i++;
            while (line[i] == ' ') i++;
            while (line[i] && line[i] != ' ') {
                char c = line[i++];
                uint32_t d = (c >= '0' && c <= '9') ? (uint32_t)(c - '0') : (uint32_t)((c | 0x20) - 'a' + 10);
                off = (off << 4) | d;
            }
            if (off == 0u && lpos >= slen) {
                int match = 1;
                for (int s = 0; s < slen; s++) {
                    if (line[lpos - slen + s] != suffix[s]) { match = 0; break; }
                }
                if (match) base = lo;
            }
            lpos = 0;
        }
    }
    sc3(6 /* close */, fd, 0, 0);
    return base;
}

static char swap_phys_u16(long fd, uint32_t pa, uint16_t exp_v, uint16_t tgt_v) {
    long base = sc6(192 /* mmap2 */, 0, 4096, 3 /* PROT_READ|PROT_WRITE */, 1 /* MAP_SHARED */,
                    fd, (long)(pa >> 12));
    if (base < 0 && base > -4096) return 'm';
    volatile uint16_t *p = (volatile uint16_t *)(base + (pa & 0xfffu));
    uint16_t cur = *p;
    char c = 'x';
    if (cur == tgt_v) c = 'a';
    else if (cur == exp_v) {
        *p = tgt_v;
        __asm__ volatile("dsb" ::: "memory");
        cur = *p;
        c = (cur == tgt_v) ? 'w' : 'v';
    }
    sc3(91 /* munmap */, base, 4096, 0);
    return c;
}

static char swap_proc_u32(uint32_t va, uint32_t exp_v, uint32_t tgt_v) {
    volatile uint32_t *p = (volatile uint32_t *)(uintptr_t)va;
    uint32_t cur = p[0];
    if (cur == tgt_v) return 'a';
    if (cur != exp_v) return 'x';
    uint32_t page = va & ~0xfffu;
    if (sc3(125 /* mprotect */, (long)page, 4096, 7 /* RWX */) < 0) return 'p';
    p[0] = tgt_v;
    __asm__ volatile("dsb" ::: "memory");
    cur = p[0];
    sc3(125 /* mprotect */, (long)page, 4096, 5 /* RX */);
    return (cur == tgt_v) ? 'w' : 'v';
}

typedef struct { uint32_t pa; uint16_t stock; uint16_t patched; } av_patch_t;
static const av_patch_t AV_PATCHES[10] = {
    {0x03E25444u,    4u,  472u},
    {0x03E2544Cu, 6000u, 5064u},
    {0x03E25454u,   20u,  488u},
    {0x03E25490u,    4u,  472u},
    {0x03E25498u, 6000u, 5064u},
    {0x03E254A0u,   20u,  488u},
    {0x03E1979Au,   16u,  484u},
    {0x03E1979Cu, 6000u, 5064u},
    {0x03E197ACu,    4u,  472u},
    {0x03E197AEu, 6024u, 5088u},
};

static const uint32_t VU_CELL_OFFS[8] = {
    0x77D110u, 0x77D150u, 0x77D230u, 0x77D270u,
    0x77D470u, 0x77D4B0u, 0x77D590u, 0x77D5D0u,
};

typedef struct { char b[128]; int n; } lbuf_t;
static void pc(lbuf_t *l, char c) { if (l->n < (int)sizeof(l->b) - 1) l->b[l->n++] = c; }
static void ps(lbuf_t *l, const char *s) { while (*s) pc(l, *s++); }
static void ph(lbuf_t *l, uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) {
        uint32_t d = (v >> i) & 0xfu;
        pc(l, (char)(d < 10 ? '0' + d : 'a' + d - 10));
    }
}

static void run_mod(int enable) {
    lbuf_t l;
    l.n = 0;
    ps(&l, enable ? "ON av=" : "OFF av=");
    int ok = 0;

    long fd = sc3(5 /* open */, (long)"/dev/mem", 2 | 04010000 /* O_RDWR | O_SYNC */, 0);
    if (fd < 0) {
        ps(&l, "err");
    } else {
        for (int i = 0; i < 10; i++) {
            uint16_t e = enable ? AV_PATCHES[i].stock : AV_PATCHES[i].patched;
            uint16_t t = enable ? AV_PATCHES[i].patched : AV_PATCHES[i].stock;
            char c = swap_phys_u16(fd, AV_PATCHES[i].pa, e, t);
            pc(&l, c);
            if (c == 'w' || c == 'a') ok++;
        }
        sc3(6 /* close */, fd, 0, 0);
    }

    ps(&l, " vu=");
    uint32_t vu_base = find_lib_base("/viewUnified2.so", 16);
    if (!vu_base) {
        ps(&l, "nomap");
    } else {
        for (int i = 0; i < 8; i++) {
            uint32_t cell = vu_base + VU_CELL_OFFS[i];
            char c1 = swap_proc_u32(cell + 0x0Cu,
                                    enable ? 0x404E0000u : 0x403A0000u,
                                    enable ? 0x403A0000u : 0x404E0000u);
            char c2 = swap_proc_u32(cell + 0x1Cu,
                                    enable ? 0x40768000u : 0x407A8000u,
                                    enable ? 0x407A8000u : 0x40768000u);
            pc(&l, c1);
            pc(&l, c2);
            if (c1 == 'w' || c1 == 'a') ok++;
            if (c2 == 'w' || c2 == 'a') ok++;
        }
    }
    ps(&l, " ok=");
    ph(&l, (uint32_t)ok);
    pc(&l, '\n');

    long logfd = sc3(5 /* open */, (long)"/tmp_bt/mods/opengate_a1.log",
                     01 | 0100 | 02000 /* O_WRONLY|O_CREAT|O_APPEND */, 0644);
    if (logfd >= 0) {
        sc3(4 /* write */, logfd, (long)l.b, (long)l.n);
        sc3(6 /* close */, logfd, 0, 0);
    }
}

__attribute__((visibility("default"))) void oga1_on(void) { run_mod(1); }
__attribute__((visibility("default"))) void oga1_off(void) { run_mod(0); }
