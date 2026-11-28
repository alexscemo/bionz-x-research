/* Reversible CXD90045 internal eMMC Copy-on-Write write-guard kernel module.
 *
 * Intercepts emmc_seq_read and emmc_seq_write in Sony's emmc.ko (ALFReD) after
 * verifying the exact 16-byte ARM prologue, sinks all sector writes (user area,
 * WBI1 hibernation via emmc_wbi_write_sector, and B0/B1 boot partitions) into a
 * pre-allocated RAM CoW hash table without issuing hardware writes, forces
 * backup.ko's Backup_save=0, exposes /proc/emunand_status, and cleanly restores
 * every patched byte and setting on rmmod.
 */
#include <stdint.h>

typedef unsigned int size_t;
typedef int ssize_t;
typedef long long loff_t;

struct module;
struct file;
struct proc_dir_entry;
struct cpumask;

extern struct module __this_module;

/* Verified vmlinux exports on CXD90045 Linux 3.0.27_nl-rt106+. */
extern unsigned long kallsyms_lookup_name(const char *name);
extern int stop_machine(int (*fn)(void *), void *data, const struct cpumask *cpus);
extern void *vmalloc(unsigned long size);
extern void vfree(const void *addr);
extern struct proc_dir_entry *proc_create_data(
    const char *name, unsigned int mode, struct proc_dir_entry *parent,
    const void *proc_fops, void *data);
extern void remove_proc_entry(const char *name, struct proc_dir_entry *parent);
extern unsigned long __copy_to_user(void *to, const void *from, unsigned long n);
extern unsigned long __copy_from_user(void *to, const void *from, unsigned long n);
extern int scnprintf(char *buf, size_t size, const char *fmt, ...);
extern int strncmp(const char *cs, const char *ct, size_t count);
extern void *memcpy(void *dest, const void *src, size_t count);
extern void *memset(void *s, int c, size_t count);
extern int printk(const char *fmt, ...);
extern void msleep(unsigned int msecs);

/* Assembly trampolines and ARMv7 SMP helpers. */
struct al_sg;
extern int emunand_orig_seq_read_trampoline(
    struct al_sg *sg, uint32_t sector, uint32_t part_id, uint32_t flags);
extern int emunand_orig_seq_write_trampoline(
    struct al_sg *sg, uint32_t sector, uint32_t rel_wr, uint32_t part_id, uint32_t flags);
extern void emunand_spin_lock(volatile uint32_t *lock);
extern void emunand_spin_unlock(volatile uint32_t *lock);
extern void emunand_atomic_inc(volatile int32_t *v);
extern int32_t emunand_atomic_dec(volatile int32_t *v);
extern void emunand_barriers_sync(void);

uint32_t emunand_orig_seq_read_plus16 = 0;
uint32_t emunand_orig_seq_write_plus16 = 0;

/* Exact 16-byte prologue shared by emmc_seq_read and emmc_seq_write on CXD90045:
 *   e1a0c00d  mov ip, sp
 *   e92dddf0  push {r4, r5, r6, r7, r8, sl, fp, ip, lr, pc}
 *   e24cb004  sub fp, ip, #4
 *   e24dd010  sub sp, sp, #0x10
 */
#define PROLOGUE_W0 0xe1a0c00du
#define PROLOGUE_W1 0xe92dddf0u
#define PROLOGUE_W2 0xe24cb004u
#define PROLOGUE_W3 0xe24dd010u
#define ARM_LDR_PC_NEXT 0xe51ff004u /* ldr pc, [pc, #-4] */

#define SECTOR_BYTES      512u
#define COW_MAX_SECTORS   4096u  /* 2 MiB sector payload capacity */
#define COW_HASH_BUCKETS  8192u  /* Power of two >= 2 * COW_MAX_SECTORS */
#define COW_EMPTY_SLOT    0xffffffffu

/* ALFReD scatter-gather descriptor layout (verified from emmc.ko 0x5db0 & 0x9128). */
struct al_sg_entry {
    void    *buf;
    uint32_t dma_addr;
    uint32_t len;
};

struct al_sg {
    uint32_t max_entries;
    uint32_t num_entries;
    uint32_t is_phys;
    uint32_t mapped;
    struct al_sg_entry *entries;
    uint32_t dir;
};

/* Standard ARM Linux 3.0.27 struct file_operations (28 pointers = 112 bytes;
 * verified against alignment_proc_fops @ 0x603c87d0).
 */
struct k_file_operations {
    struct module *owner;                                              /* +0x00 */
    loff_t (*llseek)(struct file *, loff_t, int);                      /* +0x04 */
    ssize_t (*read)(struct file *, char *, size_t, loff_t *);          /* +0x08 */
    ssize_t (*write)(struct file *, const char *, size_t, loff_t *);   /* +0x0c */
    void *pad[24];
};

struct cow_hash_entry {
    uint32_t slot;
    uint32_t part_id;
    uint32_t sector;
};

struct cow_pool {
    struct cow_hash_entry buckets[COW_HASH_BUCKETS];
    uint8_t data[COW_MAX_SECTORS][SECTOR_BYTES];
};

static struct cow_pool *g_pool = 0;
static volatile uint32_t g_lock = 0;
static volatile int32_t g_in_flight = 0;
static volatile uint32_t g_guard_enabled = 0;

static uint32_t *g_seq_read_va = 0;
static uint32_t *g_seq_write_va = 0;
static uint32_t g_saved_read[2];
static uint32_t g_saved_write[2];
static uint32_t g_patched = 0;

static int *g_backup_save_ptr = 0;
static int g_backup_save_orig = 0;
static void (*g_coherent_kern_range)(unsigned long start, unsigned long end) = 0;

/* Telemetry counters exposed via /proc/emunand_status. */
static uint32_t g_cow_used = 0;
static uint32_t g_writes_intercepted = 0;
static uint32_t g_sectors_written_cow = 0;
static uint32_t g_boot_writes_blocked = 0;
static uint32_t g_orig_reads = 0;
static uint32_t g_full_cow_reads = 0;
static uint32_t g_cow_sector_hits = 0;
static uint32_t g_cow_overflow_errors = 0;
static uint32_t g_wbi_dropped = 0;
static struct proc_dir_entry *g_proc_entry = 0;

static uint8_t *resolve_sg_entry_va(const struct al_sg *sg, const struct al_sg_entry *ent) {
    uint32_t addr = (uint32_t)ent->buf;
    if (!addr) return 0;
    if (!sg->is_phys) {
        return (uint8_t *)addr;
    }
    /* Inverse of al_sg_dma_map (0x91e8..0x91f4): Bank 0 PA 0..0x3fffffff -> +0x60000000,
     * Bank 1 PA 0x80000000..0x8fffffff -> +0x20000000.
     */
    if (addr < 0x40000000u) {
        return (uint8_t *)(addr + 0x60000000u);
    }
    if (addr >= 0x80000000u && addr < 0x90000000u) {
        return (uint8_t *)(addr + 0x20000000u);
    }
    return 0;
}

static uint32_t sg_total_bytes(const struct al_sg *sg) {
    if (!sg || !sg->entries || sg->num_entries > sg->max_entries) return 0;
    uint32_t total = 0;
    for (uint32_t i = 0; i < sg->num_entries; i++) {
        uint32_t len = sg->entries[i].len;
        if (len > 0x02000000u || total > 0x02000000u - len) return 0;
        total += len;
    }
    return total;
}

/* Copy one 512-byte sector into or out of a potentially multi-entry al_sg. */
static int sg_xfer_sector(const struct al_sg *sg, uint32_t sector_idx, uint8_t *sector_buf, int to_sg) {
    uint32_t target_off = sector_idx * SECTOR_BYTES;
    uint32_t rem = SECTOR_BYTES;
    uint32_t cur = 0;
    uint32_t buf_off = 0;

    for (uint32_t i = 0; i < sg->num_entries && rem > 0; i++) {
        uint32_t elen = sg->entries[i].len;
        if (cur + elen <= target_off) {
            cur += elen;
            continue;
        }
        uint8_t *eva = resolve_sg_entry_va(sg, &sg->entries[i]);
        if (!eva) return -14; /* -EFAULT */
        uint32_t eoff = (target_off > cur) ? (target_off - cur) : 0;
        uint32_t avail = elen - eoff;
        uint32_t chunk = (avail < rem) ? avail : rem;
        if (to_sg) {
            memcpy(eva + eoff, sector_buf + buf_off, chunk);
        } else {
            memcpy(sector_buf + buf_off, eva + eoff, chunk);
        }
        buf_off += chunk;
        target_off += chunk;
        rem -= chunk;
        cur += elen;
    }
    return (rem == 0) ? 0 : -22; /* -EINVAL */
}

static uint32_t cow_hash(uint32_t part_id, uint32_t sector) {
    uint32_t x = sector ^ (part_id * 0x9e3779b9u);
    x ^= x >> 16;
    x *= 0x7feb352du;
    x ^= x >> 15;
    return x & (COW_HASH_BUCKETS - 1u);
}

static void cow_reset_locked(void) {
    for (uint32_t i = 0; i < COW_HASH_BUCKETS; i++) {
        g_pool->buckets[i].slot = COW_EMPTY_SLOT;
        g_pool->buckets[i].part_id = 0;
        g_pool->buckets[i].sector = 0;
    }
    g_cow_used = 0;
}

static int32_t cow_find_slot_locked(uint32_t part_id, uint32_t sector) {
    uint32_t idx = cow_hash(part_id, sector);
    for (uint32_t probe = 0; probe < COW_HASH_BUCKETS; probe++) {
        const struct cow_hash_entry *e = &g_pool->buckets[idx];
        if (e->slot == COW_EMPTY_SLOT) {
            return -1;
        }
        if (e->part_id == part_id && e->sector == sector) {
            return (int32_t)e->slot;
        }
        idx = (idx + 1u) & (COW_HASH_BUCKETS - 1u);
    }
    return -1;
}

static int32_t cow_alloc_slot_locked(uint32_t part_id, uint32_t sector) {
    uint32_t idx = cow_hash(part_id, sector);
    for (uint32_t probe = 0; probe < COW_HASH_BUCKETS; probe++) {
        struct cow_hash_entry *e = &g_pool->buckets[idx];
        if (e->slot == COW_EMPTY_SLOT) {
            if (g_cow_used >= COW_MAX_SECTORS) {
                return -1;
            }
            uint32_t slot = g_cow_used++;
            e->slot = slot;
            e->part_id = part_id;
            e->sector = sector;
            return (int32_t)slot;
        }
        if (e->part_id == part_id && e->sector == sector) {
            return (int32_t)e->slot;
        }
        idx = (idx + 1u) & (COW_HASH_BUCKETS - 1u);
    }
    return -1;
}

int emunand_hook_seq_write(
    struct al_sg *sg, uint32_t sector, uint32_t rel_wr, uint32_t part_id, uint32_t flags) {
    uint8_t staging[SECTOR_BYTES];
    emunand_atomic_inc(&g_in_flight);

    if (!g_guard_enabled) {
        int rc = emunand_orig_seq_write_trampoline(sg, sector, rel_wr, part_id, flags);
        emunand_atomic_dec(&g_in_flight);
        return rc;
    }

    uint32_t bytes = sg_total_bytes(sg);
    if (bytes == 0 || (bytes & (SECTOR_BYTES - 1u)) != 0) {
        emunand_atomic_dec(&g_in_flight);
        return -22; /* -EINVAL */
    }
    uint32_t nsec = bytes / SECTOR_BYTES;

    for (uint32_t s = 0; s < nsec; s++) {
        int xrc = sg_xfer_sector(sg, s, staging, 0);
        if (xrc < 0) {
            emunand_atomic_dec(&g_in_flight);
            return xrc;
        }
        emunand_spin_lock(&g_lock);
        int32_t slot = cow_alloc_slot_locked(part_id, sector + s);
        if (slot < 0) {
            g_cow_overflow_errors++;
            emunand_spin_unlock(&g_lock);
            emunand_atomic_dec(&g_in_flight);
            /* Fail-closed: never fall back to writing physical eMMC on CoW exhaustion. */
            return -28; /* -ENOSPC */
        }
        memcpy(g_pool->data[(uint32_t)slot], staging, SECTOR_BYTES);
        emunand_spin_unlock(&g_lock);
    }

    emunand_spin_lock(&g_lock);
    g_writes_intercepted++;
    g_sectors_written_cow += nsec;
    if (part_id != 0) {
        g_boot_writes_blocked++;
    }
    emunand_spin_unlock(&g_lock);

    emunand_atomic_dec(&g_in_flight);
    return 0;
}

int emunand_hook_seq_read(
    struct al_sg *sg, uint32_t sector, uint32_t part_id, uint32_t flags) {
    uint8_t staging[SECTOR_BYTES];
    emunand_atomic_inc(&g_in_flight);

    uint32_t bytes = sg_total_bytes(sg);
    if (!g_guard_enabled || bytes == 0 || (bytes & (SECTOR_BYTES - 1u)) != 0) {
        int rc = emunand_orig_seq_read_trampoline(sg, sector, part_id, flags);
        emunand_atomic_dec(&g_in_flight);
        return rc;
    }
    uint32_t nsec = bytes / SECTOR_BYTES;

    /* Fast path: if every requested sector is already in RAM CoW, skip hardware I/O. */
    uint32_t present = 0;
    emunand_spin_lock(&g_lock);
    for (uint32_t s = 0; s < nsec; s++) {
        if (cow_find_slot_locked(part_id, sector + s) >= 0) {
            present++;
        } else {
            break;
        }
    }
    if (present == nsec) {
        g_full_cow_reads++;
        g_cow_sector_hits += nsec;
        emunand_spin_unlock(&g_lock);
        for (uint32_t s = 0; s < nsec; s++) {
            emunand_spin_lock(&g_lock);
            int32_t slot = cow_find_slot_locked(part_id, sector + s);
            if (slot >= 0) {
                memcpy(staging, g_pool->data[(uint32_t)slot], SECTOR_BYTES);
            }
            emunand_spin_unlock(&g_lock);
            if (slot >= 0) {
                int xrc = sg_xfer_sector(sg, s, staging, 1);
                if (xrc < 0) {
                    emunand_atomic_dec(&g_in_flight);
                    return xrc;
                }
            }
        }
        emunand_atomic_dec(&g_in_flight);
        return 0;
    }
    g_orig_reads++;
    emunand_spin_unlock(&g_lock);

    int rc = emunand_orig_seq_read_trampoline(sg, sector, part_id, flags);
    if (rc < 0) {
        emunand_atomic_dec(&g_in_flight);
        return rc;
    }

    /* Overlay any CoW-modified sectors onto the read result. */
    uint32_t hits = 0;
    for (uint32_t s = 0; s < nsec; s++) {
        int have = 0;
        emunand_spin_lock(&g_lock);
        int32_t slot = cow_find_slot_locked(part_id, sector + s);
        if (slot >= 0) {
            memcpy(staging, g_pool->data[(uint32_t)slot], SECTOR_BYTES);
            have = 1;
        }
        emunand_spin_unlock(&g_lock);
        if (have) {
            int xrc = sg_xfer_sector(sg, s, staging, 1);
            if (xrc < 0) {
                emunand_atomic_dec(&g_in_flight);
                return xrc;
            }
            hits++;
        }
    }
    if (hits > 0) {
        emunand_spin_lock(&g_lock);
        g_cow_sector_hits += hits;
        emunand_spin_unlock(&g_lock);
    }

    emunand_atomic_dec(&g_in_flight);
    return 0;
}

static void sync_patched_code(uint32_t *va) {
    if (g_coherent_kern_range) {
        g_coherent_kern_range((unsigned long)va, (unsigned long)(va + 2));
    }
    emunand_barriers_sync();
}

static int patch_stop_machine_cb(void *arg) {
    (void)arg;
    g_seq_read_va[0] = ARM_LDR_PC_NEXT;
    g_seq_read_va[1] = (uint32_t)&emunand_hook_seq_read;
    sync_patched_code(g_seq_read_va);

    g_seq_write_va[0] = ARM_LDR_PC_NEXT;
    g_seq_write_va[1] = (uint32_t)&emunand_hook_seq_write;
    sync_patched_code(g_seq_write_va);
    return 0;
}

static int unpatch_stop_machine_cb(void *arg) {
    (void)arg;
    g_seq_read_va[0] = g_saved_read[0];
    g_seq_read_va[1] = g_saved_read[1];
    sync_patched_code(g_seq_read_va);

    g_seq_write_va[0] = g_saved_write[0];
    g_seq_write_va[1] = g_saved_write[1];
    sync_patched_code(g_seq_write_va);
    return 0;
}

static ssize_t emunand_proc_read(struct file *filp, char *ubuf, size_t count, loff_t *ppos) {
    char kbuf[512];
    (void)filp;
    if (!ppos || *ppos > 0) return 0;

    emunand_spin_lock(&g_lock);
    uint32_t enabled = g_guard_enabled;
    uint32_t used = g_cow_used;
    uint32_t wr_int = g_writes_intercepted;
    uint32_t wr_sec = g_sectors_written_cow;
    uint32_t boot_blk = g_boot_writes_blocked;
    uint32_t orig_rd = g_orig_reads;
    uint32_t full_rd = g_full_cow_reads;
    uint32_t hits = g_cow_sector_hits;
    uint32_t ovf = g_cow_overflow_errors;
    emunand_spin_unlock(&g_lock);

    int bsave_guarded = (g_backup_save_ptr != 0) ? 1 : 0;
    int bsave_now = g_backup_save_ptr ? *g_backup_save_ptr : -1;

    int len = scnprintf(
        kbuf, sizeof(kbuf),
        "guard_enabled=%u\n"
        "backup_save_guarded=%d\n"
        "backup_save_orig=%d\n"
        "backup_save_now=%d\n"
        "wbi_dropped=%u\n"
        "cow_used_sectors=%u\n"
        "cow_max_sectors=%u\n"
        "writes_intercepted=%u\n"
        "sectors_written_cow=%u\n"
        "boot_writes_blocked=%u\n"
        "orig_reads=%u\n"
        "full_cow_reads=%u\n"
        "cow_sector_hits=%u\n"
        "cow_overflow_errors=%u\n",
        enabled, bsave_guarded, g_backup_save_orig, bsave_now, g_wbi_dropped,
        used, (unsigned)COW_MAX_SECTORS, wr_int, wr_sec, boot_blk,
        orig_rd, full_rd, hits, ovf);

    if (len <= 0) return 0;
    size_t to_copy = ((size_t)len < count) ? (size_t)len : count;
    if (__copy_to_user(ubuf, kbuf, to_copy) != 0) return -14;
    *ppos = (loff_t)to_copy;
    return (ssize_t)to_copy;
}

static ssize_t emunand_proc_write(struct file *filp, const char *ubuf, size_t count, loff_t *ppos) {
    char cmd[32];
    (void)filp;
    (void)ppos;
    if (count == 0 || count >= sizeof(cmd)) return -22;
    if (__copy_from_user(cmd, ubuf, count) != 0) return -14;
    cmd[count] = '\0';

    if (strncmp(cmd, "clear_cow", 9) == 0) {
        emunand_spin_lock(&g_lock);
        cow_reset_locked();
        emunand_spin_unlock(&g_lock);
        return (ssize_t)count;
    }
    return -22;
}

static const struct k_file_operations g_proc_fops = {
    .owner = &__this_module,
    .llseek = 0,
    .read = emunand_proc_read,
    .write = emunand_proc_write,
};

static int verify_prologue(const uint32_t *fn) {
    return fn &&
           fn[0] == PROLOGUE_W0 &&
           fn[1] == PROLOGUE_W1 &&
           fn[2] == PROLOGUE_W2 &&
           fn[3] == PROLOGUE_W3;
}

int init_module(void) {
    unsigned long r_va = kallsyms_lookup_name("emmc_seq_read");
    unsigned long w_va = kallsyms_lookup_name("emmc_seq_write");
    if (!r_va || !w_va) {
        printk("<3>emunand_guard: missing emmc_seq_read/write symbols\n");
        return -2; /* -ENOENT */
    }

    g_seq_read_va = (uint32_t *)r_va;
    g_seq_write_va = (uint32_t *)w_va;
    if (!verify_prologue(g_seq_read_va) || !verify_prologue(g_seq_write_va)) {
        printk("<3>emunand_guard: prologue mismatch, refusing load\n");
        return -22; /* -EINVAL */
    }

    g_coherent_kern_range = (void (*)(unsigned long, unsigned long))
        kallsyms_lookup_name("v7_coherent_kern_range");

    g_pool = (struct cow_pool *)vmalloc(sizeof(struct cow_pool));
    if (!g_pool) {
        printk("<3>emunand_guard: vmalloc(%u) failed\n", (unsigned)sizeof(struct cow_pool));
        return -12; /* -ENOMEM */
    }
    memset(g_pool, 0, sizeof(struct cow_pool));
    cow_reset_locked();

    g_saved_read[0] = g_seq_read_va[0];
    g_saved_read[1] = g_seq_read_va[1];
    g_saved_write[0] = g_seq_write_va[0];
    g_saved_write[1] = g_seq_write_va[1];
    emunand_orig_seq_read_plus16 = (uint32_t)(r_va + 16u);
    emunand_orig_seq_write_plus16 = (uint32_t)(w_va + 16u);

    /* Guard backup.ko shutdown persistence if loaded. */
    unsigned long bsave = kallsyms_lookup_name("Backup_save");
    if (bsave) {
        g_backup_save_ptr = (int *)bsave;
        g_backup_save_orig = *g_backup_save_ptr;
        *g_backup_save_ptr = 0;
    }

    /* Drop stale WBI snapshot flag in RAM if ssboot exports wbi_drop. */
    void (*fn_wbi_drop)(void) = (void (*)(void))kallsyms_lookup_name("wbi_drop");
    if (fn_wbi_drop) {
        fn_wbi_drop();
        g_wbi_dropped = 1;
    }

    g_guard_enabled = 1;
    int rc = stop_machine(patch_stop_machine_cb, 0, 0);
    if (rc != 0) {
        g_guard_enabled = 0;
        if (g_backup_save_ptr) *g_backup_save_ptr = g_backup_save_orig;
        vfree(g_pool);
        g_pool = 0;
        printk("<3>emunand_guard: stop_machine failed (%d)\n", rc);
        return rc;
    }
    g_patched = 1;

    g_proc_entry = proc_create_data("emunand_status", 0600, 0, &g_proc_fops, 0);
    printk("<6>emunand_guard: active (cow_sectors=%u backup_save=%d->0)\n",
           (unsigned)COW_MAX_SECTORS, g_backup_save_orig);
    return 0;
}

void cleanup_module(void) {
    g_guard_enabled = 0;
    if (g_proc_entry) {
        remove_proc_entry("emunand_status", 0);
        g_proc_entry = 0;
    }
    if (g_patched) {
        stop_machine(unpatch_stop_machine_cb, 0, 0);
        g_patched = 0;
    }
    while (g_in_flight > 0) {
        msleep(1);
    }
    if (g_backup_save_ptr) {
        *g_backup_save_ptr = g_backup_save_orig;
        g_backup_save_ptr = 0;
    }
    if (g_pool) {
        vfree(g_pool);
        g_pool = 0;
    }
    printk("<6>emunand_guard: unloaded cleanly, original eMMC prologues restored\n");
}
