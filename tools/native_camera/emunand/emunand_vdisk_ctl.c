/* Freestanding FAT32 + exFAT raw-block VDisk resolver & loop0 controller.
 *
 * Inspects /dev/mmca1 (250:11) read-only without mounting the SD card in the
 * Linux VFS (avoiding dual-FAT corruption with Sony's user-space Fsys and
 * supporting >=64 GB exFAT SDXC cards despite 0 kernel exFAT symbols).
 *
 * Modes:
 *   inspect [--dev PATH] [--image NAME]
 *   attach  [--dev PATH] [--image NAME] [--loop PATH] [--stage PATH] [--sha256 HEX] [--rw]
 *   detach  [--loop PATH] [--stage PATH]
 */
#ifndef MLV2_BARE_ARM
#define _DEFAULT_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>
#else
#include <stdint.h>
typedef int ssize_t;
struct k_stat64 {
    uint64_t st_dev;
    uint32_t __pad0;
    uint32_t __st_ino;
    uint32_t st_mode;
    uint8_t  __rest[84];
};
#define O_RDONLY 00000000
#define O_WRONLY 00000001
#define O_RDWR   00000002
#define O_CREAT  00000100
#define O_EXCL   00000200
#define O_TRUNC  00001000
#define S_IFBLK  00060000

static inline long sc1(long n, long a) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}
static inline long sc2(long n, long a, long b) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1) : "memory", "cc");
    return r0;
}
static inline long sc3(long n, long a, long b, long c) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1), "r"(r2) : "memory", "cc");
    return r0;
}
static inline long sc5(long n, long a, long b, long c, long d, long e) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c, r3 __asm__("r3") = d, r4 __asm__("r4") = e;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1), "r"(r2), "r"(r3), "r"(r4) : "memory", "cc");
    return r0;
}
static int sys_open(const char *p, int f, int m) { return (int)sc3(5, (long)p, f, m); }
static int sys_close(int fd) { return (int)sc1(6, fd); }
static int sys_unlink(const char *p) { return (int)sc1(10, (long)p); }
static int sys_mknod_blk(const char *p, int mode, unsigned major_n, unsigned minor_n) {
    unsigned dev = ((major_n & 0xffu) << 8) | (minor_n & 0xffu);
    return (int)sc3(14, (long)p, S_IFBLK | mode, (long)dev);
}
static ssize_t sys_read(int fd, void *b, unsigned n) { return (ssize_t)sc3(3, fd, (long)b, n); }
static ssize_t sys_write(int fd, const void *b, unsigned n) { return (ssize_t)sc3(4, fd, (long)b, n); }
static int sys_ioctl(int fd, unsigned long req, unsigned long arg) {
    return (int)sc3(54, fd, (long)req, (long)arg);
}
static int sys_fstat64(int fd, struct k_stat64 *st) {
    return (int)sc2(197, fd, (long)st);
}
static int sys_pread512(int fd, void *out, uint64_t off, unsigned size) {
    uint64_t res = 0;
    if (sc5(140, fd, (long)(uint32_t)(off >> 32), (long)(uint32_t)off, (long)&res, 0) < 0) return -1;
    unsigned char *p = (unsigned char *)out;
    unsigned got = 0;
    while (got < size) {
        ssize_t r = sys_read(fd, p + got, size - got);
        if (r <= 0) return -1;
        got += (unsigned)r;
    }
    return 0;
}
#endif

#define SECTOR              512u
#define IO_CHUNK            4096u
#define MAX_ZERO_COPY_BYTES (1024u * 1024u * 1024u)
#define MAX_RAM_STAGE_BYTES (32u * 1024u * 1024u)
#define MIN_MEMFREE_KB      8192u
#define MAX_EXTENTS         128u
#define FAT32_EOC           0x0ffffff8u
#define EXFAT_EOC           0xfffffff8u

#define LOOP_SET_FD       0x4C00u
#define LOOP_CLR_FD       0x4C01u
#define LOOP_SET_STATUS64 0x4C04u
#define BLKFLSBUF_IOCTL   0x1261u
#define LO_FLAGS_READ_ONLY 1u

/* Verified 232-byte (0xe8) ARM EABI struct loop_info64 (loop_set_status64 @ 0x602a6ae4). */
struct k_loop_info64 {
    uint64_t lo_device;
    uint64_t lo_inode;
    uint64_t lo_rdevice;
    uint64_t lo_offset;
    uint64_t lo_sizelimit;
    uint32_t lo_number;
    uint32_t lo_encrypt_type;
    uint32_t lo_encrypt_key_size;
    uint32_t lo_flags;
    uint8_t  lo_file_name[64];
    uint8_t  lo_crypt_name[64];
    uint8_t  lo_encrypt_key[32];
    uint64_t lo_init[2];
};

struct fs_info {
    unsigned is_exfat;
    uint32_t total_sectors;
    uint32_t fat_lba;
    uint32_t fat_sectors;
    uint32_t data_lba;
    uint32_t spc;
    uint32_t max_cluster;
    uint32_t root_cluster;
};

struct dir_entry {
    uint32_t cluster;
    uint32_t bytes;
    unsigned directory;
    unsigned no_fat_chain;
};

struct extent {
    uint32_t start_lba;
    uint32_t sectors;
};

static unsigned char io_buf[IO_CHUNK];
static unsigned char sec_buf[SECTOR];
static unsigned char head_buf[2048];
static struct extent g_extents[MAX_EXTENTS];

static void zero_mem(void *p, unsigned n) {
    unsigned char *b = (unsigned char *)p;
    for (unsigned i = 0; i < n; i++) b[i] = 0;
}

static void copy_mem(void *dst, const void *src, unsigned n) {
    unsigned char *d = (unsigned char *)dst;
    const unsigned char *s = (const unsigned char *)src;
    for (unsigned i = 0; i < n; i++) d[i] = s[i];
}

static int eq_bytes(const void *a, const void *b, unsigned n) {
    const unsigned char *pa = (const unsigned char *)a, *pb = (const unsigned char *)b;
    for (unsigned i = 0; i < n; i++) if (pa[i] != pb[i]) return 0;
    return 1;
}

static unsigned str_len(const char *s) {
    unsigned n = 0;
    while (s[n]) n++;
    return n;
}

static char lower_ascii(char c) { return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c; }

static int streq_ci(const char *a, const char *b) {
    while (*a && *b) {
        if (lower_ascii(*a) != lower_ascii(*b)) return 0;
        a++;
        b++;
    }
    return *a == 0 && *b == 0;
}

static int streq(const char *a, const char *b) {
    while (*a && *b) {
        if (*a != *b) return 0;
        a++;
        b++;
    }
    return *a == 0 && *b == 0;
}

static void write_str(int fd, const char *s) {
    unsigned n = str_len(s);
#ifdef MLV2_BARE_ARM
    (void)sys_write(fd, s, n);
#else
    if (write(fd, s, n) < 0) {}
#endif
}

static void fmt_u32(char *out, uint32_t v) {
    static const uint32_t pow10[10] = {
        1000000000u, 100000000u, 10000000u, 1000000u, 100000u,
        10000u, 1000u, 100u, 10u, 1u
    };
    if (v == 0) {
        out[0] = '0';
        out[1] = '\0';
        return;
    }
    unsigned p = 0;
    int started = 0;
    for (unsigned i = 0; i < 10; i++) {
        unsigned d = 0;
        while (v >= pow10[i]) {
            v -= pow10[i];
            d++;
        }
        if (d > 0 || started) {
            out[p++] = (char)('0' + d);
            started = 1;
        }
    }
    out[p] = '\0';
}

static void fmt_hex(char *out, const uint8_t *bytes, unsigned n) {
    static const char hex[] = "0123456789abcdef";
    for (unsigned i = 0; i < n; i++) {
        out[i * 2] = hex[bytes[i] >> 4];
        out[i * 2 + 1] = hex[bytes[i] & 0x0fu];
    }
    out[n * 2] = '\0';
}

/* Minimal FIPS 180-4 SHA-256 implementation for freestanding image attestation. */
struct sha256_ctx {
    uint32_t state[8];
    uint64_t total_bytes;
    uint32_t buf_len;
    uint8_t  buf[64];
};

static const uint32_t sha256_k[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
};

static inline uint32_t rotr32(uint32_t x, unsigned n) { return (x >> n) | (x << (32u - n)); }

static void sha256_block(struct sha256_ctx *ctx, const uint8_t blk[64]) {
    uint32_t w[64];
    for (unsigned i = 0; i < 16; i++) {
        w[i] = ((uint32_t)blk[i * 4] << 24) | ((uint32_t)blk[i * 4 + 1] << 16) |
               ((uint32_t)blk[i * 4 + 2] << 8) | (uint32_t)blk[i * 4 + 3];
    }
    for (unsigned i = 16; i < 64; i++) {
        uint32_t s0 = rotr32(w[i - 15], 7) ^ rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = rotr32(w[i - 2], 17) ^ rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = ctx->state[0], b = ctx->state[1], c = ctx->state[2], d = ctx->state[3];
    uint32_t e = ctx->state[4], f = ctx->state[5], g = ctx->state[6], h = ctx->state[7];
    for (unsigned i = 0; i < 64; i++) {
        uint32_t S1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
        uint32_t ch = (e & f) ^ ((~e) & g);
        uint32_t temp1 = h + S1 + ch + sha256_k[i] + w[i];
        uint32_t S0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t temp2 = S0 + maj;
        h = g; g = f; f = e; e = d + temp1;
        d = c; c = b; b = a; a = temp1 + temp2;
    }
    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

static void sha256_init(struct sha256_ctx *ctx) {
    zero_mem(ctx, sizeof(*ctx));
    ctx->state[0] = 0x6a09e667u; ctx->state[1] = 0xbb67ae85u;
    ctx->state[2] = 0x3c6ef372u; ctx->state[3] = 0xa54ff53au;
    ctx->state[4] = 0x510e527fu; ctx->state[5] = 0x9b05688cu;
    ctx->state[6] = 0x1f83d9abu; ctx->state[7] = 0x5be0cd19u;
}

static void sha256_update(struct sha256_ctx *ctx, const uint8_t *data, unsigned len) {
    ctx->total_bytes += len;
    unsigned off = 0;
    while (off < len) {
        unsigned take = 64u - ctx->buf_len;
        if (take > len - off) take = len - off;
        copy_mem(ctx->buf + ctx->buf_len, data + off, take);
        ctx->buf_len += take;
        off += take;
        if (ctx->buf_len == 64u) {
            sha256_block(ctx, ctx->buf);
            ctx->buf_len = 0;
        }
    }
}

static void sha256_final(struct sha256_ctx *ctx, uint8_t out[32]) {
    uint64_t bits = ctx->total_bytes << 3;
    ctx->buf[ctx->buf_len++] = 0x80u;
    if (ctx->buf_len > 56u) {
        while (ctx->buf_len < 64u) ctx->buf[ctx->buf_len++] = 0;
        sha256_block(ctx, ctx->buf);
        ctx->buf_len = 0;
    }
    while (ctx->buf_len < 56u) ctx->buf[ctx->buf_len++] = 0;
    for (unsigned i = 0; i < 8; i++) {
        ctx->buf[56 + i] = (uint8_t)(bits >> (56u - i * 8u));
    }
    sha256_block(ctx, ctx->buf);
    for (unsigned i = 0; i < 8; i++) {
        out[i * 4]     = (uint8_t)(ctx->state[i] >> 24);
        out[i * 4 + 1] = (uint8_t)(ctx->state[i] >> 16);
        out[i * 4 + 2] = (uint8_t)(ctx->state[i] >> 8);
        out[i * 4 + 3] = (uint8_t)(ctx->state[i]);
    }
}

static uint32_t le16(const unsigned char *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8); }
static uint32_t le32(const unsigned char *p) { return le16(p) | (le16(p + 2) << 16); }
static uint64_t le64(const unsigned char *p) { return (uint64_t)le32(p) | ((uint64_t)le32(p + 4) << 32); }

static int read_sectors(int fd, void *out, uint32_t lba, uint32_t sectors, uint32_t limit_sectors) {
    if (!sectors || sectors > (IO_CHUNK / SECTOR)) return -1;
    if (limit_sectors && (lba >= limit_sectors || sectors > limit_sectors - lba)) return -1;
    uint64_t off = (uint64_t)lba * SECTOR;
    unsigned bytes = sectors * SECTOR;
#ifdef MLV2_BARE_ARM
    return sys_pread512(fd, out, off, bytes);
#else
    return pread(fd, out, bytes, (off_t)off) == (ssize_t)bytes ? 0 : -1;
#endif
}

static int valid_cluster(const struct fs_info *fs, uint32_t c) {
    return c >= 2u && c <= fs->max_cluster;
}

static uint32_t cluster_lba(const struct fs_info *fs, uint32_t c) {
    return fs->data_lba + (c - 2u) * fs->spc;
}

static int next_cluster(int fd, const struct fs_info *fs, uint32_t *cluster) {
    uint32_t fat_sec_idx = (*cluster) >> 7;
    uint32_t ent_off = ((*cluster) & 127u) * 4u;
    if (fat_sec_idx >= fs->fat_sectors) return -1;
    if (read_sectors(fd, sec_buf, fs->fat_lba + fat_sec_idx, 1, fs->total_sectors)) return -1;
    uint32_t v = le32(sec_buf + ent_off);
    *cluster = fs->is_exfat ? v : (v & 0x0fffffffu);
    return 0;
}

static int parse_fs(int fd, struct fs_info *fs) {
    zero_mem(fs, sizeof(*fs));
    if (read_sectors(fd, sec_buf, 0, 1, 0) || le16(sec_buf + 510) != 0xaa55u) return -1;

    if (eq_bytes(sec_buf + 3, "EXFAT   ", 8)) {
        unsigned bps_shift = sec_buf[108], spc_shift = sec_buf[109], nfats = sec_buf[110];
        uint64_t vol_len = le64(sec_buf + 72);
        uint32_t fat_off = le32(sec_buf + 80), fat_len = le32(sec_buf + 84);
        uint32_t heap_off = le32(sec_buf + 88), clu_cnt = le32(sec_buf + 92), root_clu = le32(sec_buf + 96);
        if (bps_shift != 9u || spc_shift > 12u || (nfats != 1u && nfats != 2u)) return -1;
        if (fat_off < 24u || !fat_len || !clu_cnt || root_clu < 2u || root_clu > clu_cnt + 1u) return -1;
        uint32_t spc = 1u << spc_shift;
        uint64_t min_heap = (uint64_t)fat_off + (uint64_t)nfats * fat_len;
        uint64_t end_lba = (uint64_t)heap_off + (uint64_t)clu_cnt * spc;
        if (heap_off < min_heap || end_lba > 0xffffffffULL || (vol_len && end_lba > vol_len)) return -1;
        fs->is_exfat = 1;
        fs->total_sectors = vol_len && vol_len <= 0xffffffffULL ? (uint32_t)vol_len : (uint32_t)end_lba;
        fs->fat_lba = fat_off;
        fs->fat_sectors = fat_len;
        fs->data_lba = heap_off;
        fs->spc = spc;
        fs->max_cluster = clu_cnt + 1u;
        fs->root_cluster = root_clu;
        return 0;
    }

    uint32_t bps = le16(sec_buf + 11), spc = sec_buf[13], rsvd = le16(sec_buf + 14);
    uint32_t nfats = sec_buf[16], root_ent = le16(sec_buf + 17), tot16 = le16(sec_buf + 19);
    uint32_t fatsz16 = le16(sec_buf + 22), tot32 = le32(sec_buf + 32);
    uint32_t tot_sec = tot32 ? tot32 : tot16;
    uint32_t fatsz32 = le32(sec_buf + 36), root_clu = le32(sec_buf + 44);
    if (bps != SECTOR || !spc || (spc & (spc - 1u)) || spc > 128u) return -1;
    if (!rsvd || (nfats != 1u && nfats != 2u) || root_ent != 0u || (tot16 && tot32) || fatsz16 != 0u) return -1;
    if (tot_sec < 2048u || !fatsz32 || root_clu < 2u) return -1;
    uint64_t data_lba = (uint64_t)rsvd + (uint64_t)nfats * fatsz32;
    if (data_lba >= tot_sec) return -1;
    uint32_t data_secs = tot_sec - (uint32_t)data_lba;
    for (uint32_t s = spc; s > 1u; s >>= 1) data_secs >>= 1;
    if (data_secs < 1u || root_clu > data_secs + 1u) return -1;
    fs->is_exfat = 0;
    fs->total_sectors = tot_sec;
    fs->fat_lba = rsvd;
    fs->fat_sectors = fatsz32;
    fs->data_lba = (uint32_t)data_lba;
    fs->spc = spc;
    fs->max_cluster = data_secs + 1u;
    fs->root_cluster = root_clu;
    return 0;
}

static unsigned char fat_sfn_checksum(const unsigned char sfn[11]) {
    unsigned char sum = 0;
    for (unsigned i = 0; i < 11; i++) {
        sum = (unsigned char)(((sum & 1u) ? 0x80u : 0u) + (sum >> 1) + sfn[i]);
    }
    return sum;
}

static void format_sfn(const unsigned char d[32], char out[13]) {
    unsigned p = 0;
    unsigned char nt = d[12];
    for (unsigned i = 0; i < 8 && d[i] != ' '; i++) {
        char c = (char)d[i];
        if (i == 0 && d[0] == 0x05) c = (char)0xe5;
        if ((nt & 0x08u) && c >= 'A' && c <= 'Z') c = (char)(c + 32);
        out[p++] = c;
    }
    if (d[8] != ' ') {
        out[p++] = '.';
        for (unsigned i = 8; i < 11 && d[i] != ' '; i++) {
            char c = (char)d[i];
            if ((nt & 0x10u) && c >= 'A' && c <= 'Z') c = (char)(c + 32);
            out[p++] = c;
        }
    }
    out[p] = '\0';
}

static int lfn_put(char lfn[66], unsigned idx, uint16_t ch) {
    if (ch == 0x0000u || ch == 0xffffu) {
        if (idx < 65u) lfn[idx] = '\0';
        return 0;
    }
    if (ch < 0x20u || ch > 0x7eu || idx >= 65u) return -1;
    lfn[idx] = (char)ch;
    return 0;
}

static int find_fat32(int fd, const struct fs_info *fs, const char *wanted, struct dir_entry *match) {
    char lfn[66];
    zero_mem(lfn, sizeof(lfn));
    unsigned remaining = 0, sequence = 0;
    unsigned char checksum = 0;
    uint32_t clu = fs->root_cluster;

    for (unsigned iter = 0; iter < 512u; iter++) {
        if (!valid_cluster(fs, clu)) return -1;
        uint32_t base_lba = cluster_lba(fs, clu);
        for (uint32_t s = 0; s < fs->spc; s++) {
            if (read_sectors(fd, io_buf, base_lba + s, 1, fs->total_sectors)) return -1;
            for (unsigned off = 0; off < SECTOR; off += 32u) {
                const unsigned char *d = io_buf + off;
                if (d[0] == 0x00u) return 1;
                if (d[0] == 0xe5u) { remaining = 0; continue; }
                if (d[11] == 0x0fu) {
                    unsigned ord = d[0] & 0x1fu;
                    if (!ord || ord > 5u) { remaining = 0; continue; }
                    if (d[0] & 0x40u) {
                        zero_mem(lfn, sizeof(lfn));
                        remaining = ord;
                        sequence = ord;
                        checksum = d[13];
                    } else if (remaining != ord || checksum != d[13]) {
                        remaining = 0;
                        continue;
                    }
                    unsigned base = (ord - 1u) * 13u;
                    static const unsigned pos[13] = {1,3,5,7,9,14,16,18,20,22,24,28,30};
                    int bad = 0;
                    for (unsigned k = 0; k < 13; k++) {
                        if (lfn_put(lfn, base + k, (uint16_t)le16(d + pos[k]))) bad = 1;
                    }
                    if (bad) remaining = 0;
                    else remaining--;
                    continue;
                }
                if (d[11] & 0x08u) { remaining = 0; continue; }
                char sfn[13];
                format_sfn(d, sfn);
                int has_lfn = (sequence > 0 && remaining == 0 && checksum == fat_sfn_checksum(d));
                remaining = 0;
                sequence = 0;
                if (streq_ci(sfn, wanted) || (has_lfn && streq_ci(lfn, wanted))) {
                    match->cluster = (le16(d + 20) << 16) | le16(d + 26);
                    match->bytes = le32(d + 28);
                    match->directory = (d[11] & 0x10u) ? 1u : 0u;
                    match->no_fat_chain = 0u;
                    return 0;
                }
            }
        }
        if (next_cluster(fd, fs, &clu)) return -1;
        if (clu >= FAT32_EOC) return 1;
    }
    return -1;
}

static int find_exfat(int fd, const struct fs_info *fs, const char *wanted, struct dir_entry *match) {
    char name_buf[66];
    unsigned sec_need = 0, name_need = 0, name_pos = 0, is_dir = 0, no_chain = 0, bad_set = 0;
    uint32_t start_clu = 0;
    uint64_t data_len = 0;
    uint32_t clu = fs->root_cluster;

    for (unsigned iter = 0; iter < 512u; iter++) {
        if (!valid_cluster(fs, clu)) return -1;
        uint32_t base_lba = cluster_lba(fs, clu);
        for (uint32_t s = 0; s < fs->spc; s++) {
            if (read_sectors(fd, io_buf, base_lba + s, 1, fs->total_sectors)) return -1;
            for (unsigned off = 0; off < SECTOR; off += 32u) {
                const unsigned char *d = io_buf + off;
                unsigned char t = d[0];
                if (t == 0x00u) return 1;
                if (t == 0x85u) {
                    sec_need = d[1];
                    is_dir = (le16(d + 4) & 0x10u) ? 1u : 0u;
                    name_need = 0; name_pos = 0; bad_set = (sec_need < 2u || sec_need > 18u);
                    zero_mem(name_buf, sizeof(name_buf));
                } else if (t == 0xc0u && sec_need > 0) {
                    no_chain = (d[1] & 0x02u) ? 1u : 0u;
                    name_need = d[3];
                    start_clu = le32(d + 20);
                    data_len = le64(d + 24);
                    if (!name_need || name_need > 64u) bad_set = 1;
                    sec_need--;
                } else if (t == 0xc1u && sec_need > 0) {
                    for (unsigned k = 0; k < 15u && name_pos < name_need; k++) {
                        uint16_t ch = (uint16_t)le16(d + 2 + k * 2);
                        if (ch < 0x20u || ch > 0x7eu || name_pos >= 65u) bad_set = 1;
                        else name_buf[name_pos++] = (char)ch;
                    }
                    sec_need--;
                    if (sec_need == 0 && !bad_set && name_pos == name_need) {
                        name_buf[name_pos] = '\0';
                        if (streq_ci(name_buf, wanted)) {
                            if (data_len > 0xffffffffULL) return -1;
                            match->cluster = start_clu;
                            match->bytes = (uint32_t)data_len;
                            match->directory = is_dir;
                            match->no_fat_chain = no_chain;
                            return 0;
                        }
                    }
                } else if (sec_need > 0) {
                    sec_need--;
                }
            }
        }
        if (next_cluster(fd, fs, &clu)) return -1;
        if (clu >= EXFAT_EOC) return 1;
    }
    return -1;
}

static int lookup_root(int fd, const struct fs_info *fs, const char *wanted, struct dir_entry *match) {
    return fs->is_exfat ? find_exfat(fd, fs, wanted, match) : find_fat32(fd, fs, wanted, match);
}

static int detect_inner_fstype(const unsigned char h[2048], const char **out_fstype) {
    if (le16(h + 1080) == 0xef53u) {
        *out_fstype = "ext2";
        return 0;
    }
    if (le16(h + 510) == 0xaa55u) {
        *out_fstype = "vfat";
        return 0;
    }
    if (le32(h + 0) == 0x28cd3d45u || eq_bytes(h + 16, "Compressed ROMFS", 16)) {
        *out_fstype = "cramfs";
        return 0;
    }
    return -2;
}

/* Resolve cluster chain to coalesced LBA extents, optionally hash contents, and optionally stage. */
static int scan_and_stage_vdisk(
    int dev_fd,
    const struct fs_info *fs,
    const struct dir_entry *ent,
    int out_fd,
    int compute_hash,
    uint32_t *out_extents,
    uint8_t out_sha256[32],
    const char **out_fstype) {
    uint32_t max_bytes = (out_fd >= 0) ? MAX_RAM_STAGE_BYTES : MAX_ZERO_COPY_BYTES;
    if (ent->directory || ent->bytes < 2048u || ent->bytes > max_bytes) return -1;
    if ((ent->bytes & (SECTOR - 1u)) != 0) return -1;
    if (!valid_cluster(fs, ent->cluster)) return -1;

    uint32_t clu_bytes = fs->spc * SECTOR;

    /* Fast metadata-only path for large zero-copy flashable partition images when SHA-256 is omitted. */
    if (!compute_hash && out_fd < 0) {
        zero_mem(out_sha256, 32);
        uint32_t first_lba = cluster_lba(fs, ent->cluster);
        if (read_sectors(dev_fd, head_buf, first_lba, 4u, fs->total_sectors)) return -1;
        if (detect_inner_fstype(head_buf, out_fstype) != 0) return -2;

        if (ent->no_fat_chain) {
            uint64_t end_lba = (uint64_t)first_lba + (uint64_t)(ent->bytes >> 9);
            if (end_lba > fs->total_sectors) return -1;
            g_extents[0].start_lba = first_lba;
            g_extents[0].sectors = ent->bytes >> 9;
            *out_extents = 1u;
            return 0;
        }

        uint32_t max_clusters = max_bytes;
        for (uint32_t cb = clu_bytes; cb > 1u; cb >>= 1) max_clusters >>= 1;
        max_clusters += 2u;
        uint32_t ext_cnt = 0;
        uint32_t rem = ent->bytes;
        uint32_t clu = ent->cluster;
        for (uint32_t seen = 0; rem > 0; seen++) {
            if (seen >= max_clusters || !valid_cluster(fs, clu)) return -1;
            uint32_t lba = cluster_lba(fs, clu);
            uint32_t take_bytes = (rem < clu_bytes) ? rem : clu_bytes;
            uint32_t take_secs = take_bytes >> 9;
            if (ext_cnt > 0 && g_extents[ext_cnt - 1u].start_lba + g_extents[ext_cnt - 1u].sectors == lba) {
                g_extents[ext_cnt - 1u].sectors += take_secs;
            } else {
                if (ext_cnt >= MAX_EXTENTS) return -1;
                g_extents[ext_cnt].start_lba = lba;
                g_extents[ext_cnt].sectors = take_secs;
                ext_cnt++;
            }
            rem -= take_bytes;
            if (rem == 0) break;
            if (next_cluster(dev_fd, fs, &clu)) return -1;
            uint32_t eoc = fs->is_exfat ? EXFAT_EOC : FAT32_EOC;
            if (clu >= eoc) return -1;
        }
        *out_extents = ext_cnt;
        return 0;
    }

    struct sha256_ctx sha;
    sha256_init(&sha);
    uint32_t ext_cnt = 0;
    uint32_t rem = ent->bytes;
    uint32_t clu = ent->cluster;
    uint32_t max_clusters = max_bytes;
    for (uint32_t cb = clu_bytes; cb > 1u; cb >>= 1) max_clusters >>= 1;
    max_clusters += 2u;
    unsigned head_copied = 0;

    for (uint32_t seen = 0; rem > 0; seen++) {
        if (seen >= max_clusters || !valid_cluster(fs, clu)) return -1;
        uint32_t lba = cluster_lba(fs, clu);
        uint32_t take_bytes = (rem < clu_bytes) ? rem : clu_bytes;
        uint32_t take_secs = take_bytes >> 9;

        if (ext_cnt > 0 && g_extents[ext_cnt - 1u].start_lba + g_extents[ext_cnt - 1u].sectors == lba) {
            g_extents[ext_cnt - 1u].sectors += take_secs;
        } else {
            if (ext_cnt >= MAX_EXTENTS) return -1;
            g_extents[ext_cnt].start_lba = lba;
            g_extents[ext_cnt].sectors = take_secs;
            ext_cnt++;
        }

        uint32_t clu_rem = take_bytes;
        uint32_t sec_off = 0;
        while (clu_rem > 0) {
            uint32_t chunk = (clu_rem > IO_CHUNK) ? IO_CHUNK : clu_rem;
            uint32_t chunk_secs = chunk >> 9;
            if (read_sectors(dev_fd, io_buf, lba + sec_off, chunk_secs, fs->total_sectors)) return -1;
            if (head_copied < sizeof(head_buf)) {
                unsigned need = sizeof(head_buf) - head_copied;
                if (need > chunk) need = chunk;
                copy_mem(head_buf + head_copied, io_buf, need);
                head_copied += need;
            }
            sha256_update(&sha, io_buf, chunk);
            if (out_fd >= 0) {
                unsigned wr = 0;
                while (wr < chunk) {
#ifdef MLV2_BARE_ARM
                    ssize_t w = sys_write(out_fd, io_buf + wr, chunk - wr);
#else
                    ssize_t w = write(out_fd, io_buf + wr, chunk - wr);
#endif
                    if (w <= 0) return -1;
                    wr += (unsigned)w;
                }
            }
            clu_rem -= chunk;
            sec_off += chunk_secs;
        }

        rem -= take_bytes;
        if (rem == 0) break;
        if (ent->no_fat_chain) {
            clu += 1u;
        } else {
            if (next_cluster(dev_fd, fs, &clu)) return -1;
            uint32_t eoc = fs->is_exfat ? EXFAT_EOC : FAT32_EOC;
            if (clu >= eoc) return -1;
        }
    }

    sha256_final(&sha, out_sha256);
    if (detect_inner_fstype(head_buf, out_fstype) != 0) {
        return -2;
    }
    *out_extents = ext_cnt;
    return 0;
}

static int check_memfree_guard(uint32_t stage_bytes) {
#ifdef MLV2_BARE_ARM
    int fd = sys_open("/proc/meminfo", O_RDONLY, 0);
#else
    int fd = open("/proc/meminfo", O_RDONLY);
#endif
    if (fd < 0) return -1;
    char buf[512];
#ifdef MLV2_BARE_ARM
    ssize_t n = sys_read(fd, buf, sizeof(buf) - 1u);
    sys_close(fd);
#else
    ssize_t n = read(fd, buf, sizeof(buf) - 1u);
    close(fd);
#endif
    if (n <= 0) return -1;
    buf[n] = '\0';

    for (unsigned i = 0; buf[i]; i++) {
        if ((i == 0 || buf[i - 1] == '\n') && eq_bytes(buf + i, "MemFree:", 8)) {
            unsigned p = i + 8u;
            while (buf[p] == ' ' || buf[p] == '\t') p++;
            uint32_t kb = 0;
            unsigned digits = 0;
            while (buf[p] >= '0' && buf[p] <= '9') {
                kb = kb * 10u + (uint32_t)(buf[p] - '0');
                p++;
                digits++;
            }
            if (!digits) return -1;
            uint32_t stage_kb = (stage_bytes + 1023u) >> 10;
            return (kb >= stage_kb + MIN_MEMFREE_KB) ? 0 : -1;
        }
    }
    return -1;
}

static int parse_loop_minor(const char *loop_path) {
    unsigned len = str_len(loop_path);
    if (!len) return -1;
    char last = loop_path[len - 1u];
    if (last >= '0' && last <= '7') return (int)(last - '0');
    if (last >= '8' && last <= '9') return -1; /* Linux 3.0 default max_loop is 8 (0..7) */
    return 0;
}

static int ensure_loop_node(const char *loop_path, unsigned minor_n) {
#ifdef MLV2_BARE_ARM
    struct k_stat64 st;
    int fd = sys_open(loop_path, O_RDONLY, 0);
    if (fd >= 0) {
        int ok = (sys_fstat64(fd, &st) == 0 && ((st.st_mode & 0170000) == S_IFBLK));
        sys_close(fd);
        return ok ? 0 : -1;
    }
    return sys_mknod_blk(loop_path, 0600, 7u, minor_n) == 0 ? 0 : -1;
#else
    struct stat st;
    if (stat(loop_path, &st) == 0) {
        return S_ISBLK(st.st_mode) ? 0 : -1;
    }
    return mknod(loop_path, S_IFBLK | 0600, makedev(7, minor_n)) == 0 ? 0 : -1;
#endif
}

static int attach_loop_fd(const char *loop_path, int backing_fd, uint64_t offset, uint64_t sizelimit, int ro) {
    int minor = parse_loop_minor(loop_path);
    if (minor < 0) return -1;
    if (ensure_loop_node(loop_path, (unsigned)minor) != 0) return -1;
#ifdef MLV2_BARE_ARM
    int lfd = sys_open(loop_path, ro ? O_RDONLY : O_RDWR, 0);
#else
    int lfd = open(loop_path, ro ? O_RDONLY : O_RDWR);
#endif
    if (lfd < 0) return -1;

#ifdef MLV2_BARE_ARM
    (void)sys_ioctl(lfd, LOOP_CLR_FD, 0);
    if (sys_ioctl(lfd, LOOP_SET_FD, (unsigned long)backing_fd) < 0) {
        sys_close(lfd);
        return -1;
    }
#else
    (void)ioctl(lfd, LOOP_CLR_FD, 0);
    if (ioctl(lfd, LOOP_SET_FD, backing_fd) < 0) {
        close(lfd);
        return -1;
    }
#endif

    struct k_loop_info64 info;
    zero_mem(&info, sizeof(info));
    info.lo_offset = offset;
    info.lo_sizelimit = sizelimit;
    info.lo_number = (uint32_t)minor;
    info.lo_flags = ro ? LO_FLAGS_READ_ONLY : 0u;

#ifdef MLV2_BARE_ARM
    int rc = sys_ioctl(lfd, LOOP_SET_STATUS64, (unsigned long)&info);
    if (rc < 0) (void)sys_ioctl(lfd, LOOP_CLR_FD, 0);
    sys_close(lfd);
#else
    int rc = ioctl(lfd, LOOP_SET_STATUS64, &info);
    if (rc < 0) (void)ioctl(lfd, LOOP_CLR_FD, 0);
    close(lfd);
#endif
    return (rc < 0) ? -1 : 0;
}

static int run_main(int argc, char **argv) {
    if (argc < 2) return 2;
    const char *cmd = argv[1];
    const char *dev_path = "/dev/mmca1";
    const char *img_name = "CFW_VDISK.IMG";
    const char *loop_path = "/tmp_bt/emunand/loop0";
    const char *stage_path = "/tmp_bt/emunand/vdisk.img";
    const char *vfs_file = 0;
    const char *expected_sha = 0;
    int rw_mode = 0;
    int stage_only = 0;
    int fast_mode = 0;

    for (int i = 2; i < argc; i++) {
        if (streq(argv[i], "--dev") && i + 1 < argc) dev_path = argv[++i];
        else if (streq(argv[i], "--image") && i + 1 < argc) img_name = argv[++i];
        else if (streq(argv[i], "--file") && i + 1 < argc) vfs_file = argv[++i];
        else if (streq(argv[i], "--loop") && i + 1 < argc) loop_path = argv[++i];
        else if (streq(argv[i], "--stage") && i + 1 < argc) stage_path = argv[++i];
        else if (streq(argv[i], "--sha256") && i + 1 < argc) expected_sha = argv[++i];
        else if (streq(argv[i], "--rw")) rw_mode = 1;
        else if (streq(argv[i], "--stage-only")) stage_only = 1;
        else if (streq(argv[i], "--fast")) fast_mode = 1;
        else return 2;
    }

    if (streq(cmd, "attach-file")) {
        if (!vfs_file) return 2;
#ifdef MLV2_BARE_ARM
        int ffd = sys_open(vfs_file, rw_mode ? O_RDWR : O_RDONLY, 0);
        if (ffd < 0) return 3;
        uint64_t fsz = 0;
        if (sc5(140, ffd, 0, 0, (long)&fsz, 2 /* SEEK_END */) < 0 || fsz < 2048ULL) {
            sys_close(ffd);
            return 6;
        }
        if (sys_pread512(ffd, head_buf, 0, sizeof(head_buf)) != 0) {
            sys_close(ffd);
            return 6;
        }
        const char *fst = 0;
        if (detect_inner_fstype(head_buf, &fst) != 0) {
            sys_close(ffd);
            return 6;
        }
        int arc = attach_loop_fd(loop_path, ffd, 0, fsz, rw_mode ? 0 : 1);
        sys_close(ffd);
#else
        int ffd = open(vfs_file, rw_mode ? O_RDWR : O_RDONLY);
        if (ffd < 0) return 3;
        off_t fsz = lseek(ffd, 0, SEEK_END);
        if (fsz < 2048 || pread(ffd, head_buf, sizeof(head_buf), 0) != (ssize_t)sizeof(head_buf)) {
            close(ffd);
            return 6;
        }
        const char *fst = 0;
        if (detect_inner_fstype(head_buf, &fst) != 0) {
            close(ffd);
            return 6;
        }
        int arc = attach_loop_fd(loop_path, ffd, 0, (uint64_t)fsz, rw_mode ? 0 : 1);
        close(ffd);
#endif
        if (arc != 0) return 8;
        write_str(1, "ATTACHED mode=vfs_file fstype=");
        write_str(1, fst);
        write_str(1, "\n");
        return 0;
    }

    if (streq(cmd, "detach")) {
        static const char * const raw_bdevs[] = {
            "/dev/nflasha4", "/dev/nflashaB0", "/dev/nflashaB1"
        };
#ifdef MLV2_BARE_ARM
        int lfd = sys_open(loop_path, O_RDONLY, 0);
        if (lfd >= 0) {
            (void)sys_ioctl(lfd, LOOP_CLR_FD, 0);
            sys_close(lfd);
        }
        (void)sys_unlink(stage_path);
        (void)sys_unlink(loop_path);
        for (unsigned k = 0; k < 3u; k++) {
            int bfd = sys_open(raw_bdevs[k], O_RDONLY, 0);
            if (bfd >= 0) {
                (void)sys_ioctl(bfd, BLKFLSBUF_IOCTL, 0);
                sys_close(bfd);
            }
        }
#else
        int lfd = open(loop_path, O_RDONLY);
        if (lfd >= 0) {
            (void)ioctl(lfd, LOOP_CLR_FD, 0);
            close(lfd);
        }
        (void)unlink(stage_path);
        (void)unlink(loop_path);
        for (unsigned k = 0; k < 3u; k++) {
            int bfd = open(raw_bdevs[k], O_RDONLY);
            if (bfd >= 0) {
                (void)ioctl(bfd, BLKFLSBUF_IOCTL, 0);
                close(bfd);
            }
        }
#endif
        write_str(1, "DETACHED\n");
        return 0;
    }

    if (!streq(cmd, "inspect") && !streq(cmd, "attach")) return 2;

#ifdef MLV2_BARE_ARM
    int dfd = sys_open(dev_path, O_RDONLY, 0);
#else
    int dfd = open(dev_path, O_RDONLY);
#endif
    if (dfd < 0) return 3;

    struct fs_info fs;
    if (parse_fs(dfd, &fs) != 0) {
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        return 3;
    }

    /* Respect physical SD kill-switches before reading any VDisk payload. */
    struct dir_entry dummy;
    if (lookup_root(dfd, &fs, "EMUNAND.OFF", &dummy) == 0 ||
        lookup_root(dfd, &fs, "disable_hook", &dummy) == 0) {
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        write_str(2, "EMUNAND_KILLSWITCH\n");
        return 4;
    }

    struct dir_entry ent;
    if (lookup_root(dfd, &fs, img_name, &ent) != 0) {
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        return 3;
    }

    uint32_t ext_cnt = 0;
    uint8_t digest[32];
    const char *fstype = 0;
    int need_hash = (expected_sha != 0) || (streq(cmd, "inspect") && !fast_mode);
    if (scan_and_stage_vdisk(dfd, &fs, &ent, -1, need_hash, &ext_cnt, digest, &fstype) != 0) {
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        return 6;
    }

    char sha_hex[65];
    if (need_hash) {
        fmt_hex(sha_hex, digest, 32);
    } else {
        copy_mem(sha_hex, "skipped", 8);
    }
    if (expected_sha && !streq_ci(sha_hex, expected_sha)) {
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        write_str(2, "EMUNAND_SHA256_MISMATCH\n");
        return 7;
    }

    char num_buf[16];
    if (streq(cmd, "inspect")) {
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        write_str(1, "FORMAT="); write_str(1, fstype);
        write_str(1, " SIZE="); fmt_u32(num_buf, ent.bytes); write_str(1, num_buf);
        write_str(1, " EXTENTS="); fmt_u32(num_buf, ext_cnt); write_str(1, num_buf);
        write_str(1, " FIRST_LBA="); fmt_u32(num_buf, g_extents[0].start_lba); write_str(1, num_buf);
        write_str(1, " SHA256="); write_str(1, sha_hex);
        write_str(1, "\n");
        return 0;
    }

    /* attach mode */
    if (!rw_mode && !stage_only && ext_cnt == 1u) {
        uint64_t off = (uint64_t)g_extents[0].start_lba * SECTOR;
        int rc = attach_loop_fd(loop_path, dfd, off, ent.bytes, 1);
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        if (rc != 0) return 8;
        write_str(1, "ATTACHED mode=zero_copy_ro fstype="); write_str(1, fstype);
        write_str(1, " size="); fmt_u32(num_buf, ent.bytes); write_str(1, num_buf);
        write_str(1, " extents=1 sha256="); write_str(1, sha_hex); write_str(1, "\n");
        return 0;
    }

    /* Fragmented clusters or --rw requested: enforce 32 MiB cap + 8 MiB MemFree guard. */
    if (ent.bytes > MAX_RAM_STAGE_BYTES) {
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        write_str(2, "EMUNAND_STAGE_TOO_LARGE\n");
        return 5;
    }
    if (!stage_only && check_memfree_guard(ent.bytes) != 0) {
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        write_str(2, "EMUNAND_LOW_MEM\n");
        return 5;
    }

#ifdef MLV2_BARE_ARM
    (void)sys_unlink(stage_path);
    int sfd = sys_open(stage_path, O_CREAT | O_EXCL | O_RDWR, 0600);
#else
    (void)unlink(stage_path);
    int sfd = open(stage_path, O_CREAT | O_EXCL | O_RDWR, 0600);
#endif
    if (sfd < 0) {
#ifdef MLV2_BARE_ARM
        sys_close(dfd);
#else
        close(dfd);
#endif
        return 8;
    }

    uint32_t ext2 = 0;
    uint8_t digest2[32];
    const char *fstype2 = 0;
    int s_rc = scan_and_stage_vdisk(dfd, &fs, &ent, sfd, 1, &ext2, digest2, &fstype2);
#ifdef MLV2_BARE_ARM
    sys_close(dfd);
#else
    close(dfd);
#endif
    if (s_rc != 0 || (need_hash && !eq_bytes(digest, digest2, 32))) {
#ifdef MLV2_BARE_ARM
        sys_close(sfd);
        (void)sys_unlink(stage_path);
#else
        close(sfd);
        (void)unlink(stage_path);
#endif
        return 7;
    }

    if (stage_only) {
#ifdef MLV2_BARE_ARM
        sys_close(sfd);
#else
        close(sfd);
#endif
        write_str(1, "STAGED fstype="); write_str(1, fstype);
        write_str(1, " size="); fmt_u32(num_buf, ent.bytes); write_str(1, num_buf);
        write_str(1, " extents="); fmt_u32(num_buf, ext_cnt); write_str(1, num_buf);
        write_str(1, " sha256="); write_str(1, sha_hex); write_str(1, "\n");
        return 0;
    }

    int l_rc = attach_loop_fd(loop_path, sfd, 0, ent.bytes, rw_mode ? 0 : 1);
#ifdef MLV2_BARE_ARM
    sys_close(sfd);
#else
    close(sfd);
#endif
    if (l_rc != 0) {
#ifdef MLV2_BARE_ARM
        (void)sys_unlink(stage_path);
#else
        (void)unlink(stage_path);
#endif
        return 8;
    }

    write_str(1, rw_mode ? "ATTACHED mode=staged_ram_rw fstype=" : "ATTACHED mode=staged_ram_ro fstype=");
    write_str(1, fstype);
    write_str(1, " size="); fmt_u32(num_buf, ent.bytes); write_str(1, num_buf);
    write_str(1, " extents="); fmt_u32(num_buf, ext_cnt); write_str(1, num_buf);
    write_str(1, " sha256="); write_str(1, sha_hex); write_str(1, "\n");
    return 0;
}

#ifdef MLV2_BARE_ARM
__attribute__((used)) static int c_entry(long *sp) {
    int argc = (int)sp[0];
    char **argv = (char **)&sp[1];
    return run_main(argc, argv);
}
__asm__(
    ".text\n"
    ".global _start\n"
    ".type _start, %function\n"
    "_start:\n"
    "    mov r0, sp\n"
    "    bic sp, sp, #7\n"
    "    bl  c_entry\n"
    "    mov r7, #1\n"
    "    svc 0\n"
);
#else
int main(int argc, char **argv) { return run_main(argc, argv); }
#endif
