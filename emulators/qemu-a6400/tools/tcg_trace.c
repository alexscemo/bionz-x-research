/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Targeted QEMU Plugin API v7 tracer for sony-a6400 / CXD90045.
 *
 * Replaces iterative blind rebuilds with single-shot structured JSONL capture:
 *   out=<path>                    Output JSONL file (default stderr)
 *   ring=<N>                      Circular basic-block ring size (default 256, max 4096)
 *   ring_range=<lo>:<hi>          Optional VA filter for ring entries
 *   probe=<id>:<va>[+<base>]      Exact PC trigger dumping ARM registers (+ deref)
 *   deref=<id>:r<0-14>+<off>:<n>  Safe vaddr read attached to named probe
 *   mem=<pa_lo>:<pa_hi>           Physical address window watch (poll-coalesced)
 *   vmem=<va_lo>:<va_hi>          Virtual address window watch (poll-coalesced)
 *   max_events=<N>                Hard event bound (default 10000)
 *   dump_ring=1                   Flush ring buffer on probe hit and atexit
 *   exit_on_hit=1                 Flush output and exit cleanly on first probe hit
 */
#include <glib.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

#define MAX_CPUS 8
#define MAX_PROBES 64
#define MAX_DEREFS 64
#define MAX_RANGES 32
#define MAX_RING 4096
#define ARM_REG_COUNT 17

static const char *const ARM_REG_NAMES[ARM_REG_COUNT] = {
    "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
    "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc", "cpsr",
};

typedef struct {
    char id[48];
    uint64_t va;
    uint64_t hits;
} ProbeSpec;

typedef struct {
    char probe_id[48];
    int reg_idx;
    int64_t offset;
    size_t len;
} DerefSpec;

typedef struct {
    uint64_t lo;
    uint64_t hi;
} AddrRange;

typedef struct {
    uint64_t pc;
    uint32_t cpu;
    uint32_t insns;
    uint64_t seq;
} RingEntry;

typedef struct {
    uint64_t pc;
    uint32_t cpu;
    uint32_t insns;
} TbMeta;

typedef struct {
    bool active;
    uint64_t pc;
    uint64_t va;
    uint64_t pa;
    uint64_t val;
    uint32_t size;
    bool is_write;
    uint64_t repeat;
    char dev[48];
} PollState;

static GMutex g_lock;
static FILE *g_out;
static bool g_owns_fp;
static uint64_t g_max_events = 10000;
static uint64_t g_event_count;
static bool g_dump_ring;
static bool g_exit_on_hit;
static bool g_ring_dumped;

static ProbeSpec g_probes[MAX_PROBES];
static size_t g_num_probes;

static DerefSpec g_derefs[MAX_DEREFS];
static size_t g_num_derefs;

static AddrRange g_mem_ranges[MAX_RANGES];
static size_t g_num_mem;

static AddrRange g_vmem_ranges[MAX_RANGES];
static size_t g_num_vmem;

static AddrRange g_ring_ranges[MAX_RANGES];
static size_t g_num_ring_ranges;

static RingEntry g_ring[MAX_RING];
static size_t g_ring_cap = 256;
static size_t g_ring_head;
static size_t g_ring_fill;
static uint64_t g_ring_seq;

static PollState g_polls[MAX_CPUS];
static struct qemu_plugin_register *g_reg_handles[MAX_CPUS][ARM_REG_COUNT];

static GPtrArray *g_tb_metas;

static bool parse_u64_sum(const char *expr, uint64_t *out)
{
    const char *plus = strchr(expr, '+');
    if (!plus) {
        char *end = NULL;
        uint64_t v = g_ascii_strtoull(expr, &end, 0);
        if (!expr[0] || !end || *end != '\0') {
            return false;
        }
        *out = v;
        return true;
    }
    g_autofree char *lhs = g_strndup(expr, (gsize)(plus - expr));
    char *e1 = NULL, *e2 = NULL;
    uint64_t a = g_ascii_strtoull(lhs, &e1, 0);
    uint64_t b = g_ascii_strtoull(plus + 1, &e2, 0);
    if (!lhs[0] || !plus[1] || !e1 || *e1 != '\0' || !e2 || *e2 != '\0') {
        return false;
    }
    *out = a + b;
    return true;
}

static bool parse_range(const char *spec, AddrRange *out)
{
    const char *colon = strchr(spec, ':');
    if (!colon) {
        return false;
    }
    g_autofree char *lo_s = g_strndup(spec, (gsize)(colon - spec));
    uint64_t lo = 0, hi = 0;
    if (!parse_u64_sum(lo_s, &lo) || !parse_u64_sum(colon + 1, &hi) || hi <= lo) {
        return false;
    }
    out->lo = lo;
    out->hi = hi;
    return true;
}

static int find_reg_index(const char *name)
{
    if (!name) {
        return -1;
    }
    for (int i = 0; i < ARM_REG_COUNT; i++) {
        if (g_ascii_strcasecmp(name, ARM_REG_NAMES[i]) == 0) {
            return i;
        }
    }
    if (g_ascii_strcasecmp(name, "r13") == 0) {
        return 13;
    }
    if (g_ascii_strcasecmp(name, "r14") == 0) {
        return 14;
    }
    if (g_ascii_strcasecmp(name, "r15") == 0) {
        return 15;
    }
    return -1;
}

static bool in_ranges(const AddrRange *ranges, size_t count, uint64_t addr)
{
    for (size_t i = 0; i < count; i++) {
        if (addr >= ranges[i].lo && addr < ranges[i].hi) {
            return true;
        }
    }
    return false;
}

static void flush_poll_locked(unsigned cpu)
{
    PollState *p = &g_polls[cpu];
    if (!p->active) {
        return;
    }
    if (g_event_count < g_max_events && g_out) {
        g_event_count++;
        fprintf(g_out,
                "{\"type\":\"mem\",\"cpu\":%u,\"pc\":\"0x%08" PRIx64 "\","
                "\"va\":\"0x%08" PRIx64 "\",\"pa\":\"0x%08" PRIx64 "\","
                "\"rw\":\"%s\",\"size\":%u,\"val\":\"0x%" PRIx64 "\","
                "\"repeat\":%" PRIu64 ",\"dev\":\"%s\"}\n",
                cpu, p->pc, p->va, p->pa,
                p->is_write ? "W" : "R", p->size, p->val,
                p->repeat, p->dev);
    }
    p->active = false;
}

static void dump_ring_locked(void)
{
    if (!g_out || g_ring_fill == 0 || g_ring_dumped) {
        return;
    }
    g_ring_dumped = true;
    size_t start = (g_ring_head + g_ring_cap - g_ring_fill) % g_ring_cap;
    for (size_t i = 0; i < g_ring_fill; i++) {
        const RingEntry *e = &g_ring[(start + i) % g_ring_cap];
        fprintf(g_out,
                "{\"type\":\"ring\",\"seq\":%" PRIu64 ",\"cpu\":%u,"
                "\"pc\":\"0x%08" PRIx64 "\",\"insns\":%u}\n",
                e->seq, e->cpu, e->pc, e->insns);
    }
}

static void vcpu_init_cb(unsigned int cpu, void *ud)
{
    if (cpu >= MAX_CPUS) {
        return;
    }
    GArray *regs = qemu_plugin_get_registers();
    if (!regs) {
        return;
    }
    g_mutex_lock(&g_lock);
    for (guint i = 0; i < regs->len; i++) {
        qemu_plugin_reg_descriptor *desc = &g_array_index(regs, qemu_plugin_reg_descriptor, i);
        int idx = find_reg_index(desc->name);
        if (idx >= 0) {
            g_reg_handles[cpu][idx] = desc->handle;
        }
    }
    g_mutex_unlock(&g_lock);
    g_array_free(regs, TRUE);
}

static void tb_exec_cb(unsigned int cpu, void *ud)
{
    const TbMeta *meta = ud;
    if (!meta || g_ring_cap == 0) {
        return;
    }
    g_mutex_lock(&g_lock);
    size_t idx = g_ring_head % g_ring_cap;
    g_ring[idx].pc = meta->pc;
    g_ring[idx].cpu = cpu;
    g_ring[idx].insns = meta->insns;
    g_ring[idx].seq = ++g_ring_seq;
    g_ring_head = (idx + 1) % g_ring_cap;
    if (g_ring_fill < g_ring_cap) {
        g_ring_fill++;
    }
    g_mutex_unlock(&g_lock);
}

static void probe_exec_cb(unsigned int cpu, void *ud)
{
    ProbeSpec *probe = ud;
    uint32_t reg_vals[ARM_REG_COUNT] = {0};
    bool reg_ok[ARM_REG_COUNT] = {false};

    if (cpu < MAX_CPUS) {
        g_autoptr(GByteArray) buf = g_byte_array_sized_new(8);
        for (int i = 0; i < ARM_REG_COUNT; i++) {
            struct qemu_plugin_register *h = g_reg_handles[cpu][i];
            if (!h) {
                continue;
            }
            g_byte_array_set_size(buf, 0);
            if (qemu_plugin_read_register(h, buf) && buf->len >= 4) {
                memcpy(&reg_vals[i], buf->data, 4);
                reg_ok[i] = true;
            }
        }
    }

    g_mutex_lock(&g_lock);
    probe->hits++;
    if (cpu < MAX_CPUS) {
        flush_poll_locked(cpu);
    }
    if (g_out && g_event_count < g_max_events) {
        g_event_count++;
        fprintf(g_out, "{\"type\":\"probe_hit\",\"id\":\"%s\",\"cpu\":%u,"
                "\"va\":\"0x%08" PRIx64 "\",\"hit\":%" PRIu64 ",\"regs\":{",
                probe->id, cpu, probe->va, probe->hits);
        bool first = true;
        for (int i = 0; i < ARM_REG_COUNT; i++) {
            if (!reg_ok[i]) {
                continue;
            }
            fprintf(g_out, "%s\"%s\":\"0x%08x\"", first ? "" : ",", ARM_REG_NAMES[i], reg_vals[i]);
            first = false;
        }
        fprintf(g_out, "}");

        bool started_deref = false;
        for (size_t d = 0; d < g_num_derefs; d++) {
            const DerefSpec *ds = &g_derefs[d];
            if (strcmp(ds->probe_id, probe->id) != 0 || ds->reg_idx < 0 || !reg_ok[ds->reg_idx]) {
                continue;
            }
            uint64_t addr = (uint64_t)((int64_t)reg_vals[ds->reg_idx] + ds->offset) & 0xFFFFFFFFULL;
            g_autoptr(GByteArray) mem = g_byte_array_sized_new((guint)ds->len);
            if (qemu_plugin_read_memory_vaddr(addr, mem, ds->len) && mem->len > 0) {
                if (!started_deref) {
                    fprintf(g_out, ",\"deref\":[");
                    started_deref = true;
                } else {
                    fprintf(g_out, ",");
                }
                fprintf(g_out, "{\"addr\":\"0x%08" PRIx64 "\",\"hex\":\"", addr);
                for (guint k = 0; k < mem->len; k++) {
                    fprintf(g_out, "%02x", mem->data[k]);
                }
                fprintf(g_out, "\"}");
            }
        }
        if (started_deref) {
            fprintf(g_out, "]");
        }
        fprintf(g_out, "}\n");
    }

    if (g_dump_ring) {
        dump_ring_locked();
    }
    if (g_out) {
        fflush(g_out);
    }
    bool do_exit = g_exit_on_hit;
    g_mutex_unlock(&g_lock);

    if (do_exit) {
        _exit(0);
    }
}

static void mem_cb(unsigned int cpu, qemu_plugin_meminfo_t info, uint64_t va, void *ud)
{
    if (cpu >= MAX_CPUS) {
        return;
    }
    uint64_t pc = (uintptr_t)ud;
    struct qemu_plugin_hwaddr *hw = qemu_plugin_get_hwaddr(info, va);
    uint64_t pa = hw ? qemu_plugin_hwaddr_phys_addr(hw) : UINT64_MAX;
    const char *dev = hw ? qemu_plugin_hwaddr_device_name(hw) : NULL;

    bool match_vmem = g_num_vmem > 0 && in_ranges(g_vmem_ranges, g_num_vmem, va);
    bool match_pmem = g_num_mem > 0 && hw != NULL && in_ranges(g_mem_ranges, g_num_mem, pa);
    if (!match_vmem && !match_pmem) {
        return;
    }

    bool is_write = qemu_plugin_mem_is_store(info);
    uint32_t size = 1U << qemu_plugin_mem_size_shift(info);
    qemu_plugin_mem_value mv = qemu_plugin_mem_get_value(info);
    uint64_t val = 0;
    switch (mv.type) {
    case QEMU_PLUGIN_MEM_VALUE_U8:  val = mv.data.u8;  break;
    case QEMU_PLUGIN_MEM_VALUE_U16: val = mv.data.u16; break;
    case QEMU_PLUGIN_MEM_VALUE_U32: val = mv.data.u32; break;
    case QEMU_PLUGIN_MEM_VALUE_U64: val = mv.data.u64; break;
    default: break;
    }

    g_mutex_lock(&g_lock);
    PollState *p = &g_polls[cpu];
    if (p->active && p->pc == pc && p->va == va && p->pa == pa &&
        p->val == val && p->is_write == is_write && p->size == size) {
        p->repeat++;
        g_mutex_unlock(&g_lock);
        return;
    }
    flush_poll_locked(cpu);
    p->active = true;
    p->pc = pc;
    p->va = va;
    p->pa = pa;
    p->val = val;
    p->size = size;
    p->is_write = is_write;
    p->repeat = 1;
    g_strlcpy(p->dev, dev ? dev : "ram", sizeof(p->dev));
    g_mutex_unlock(&g_lock);
}

static void vcpu_tb_trans_cb(struct qemu_plugin_tb *tb, void *ud)
{
    uint64_t tb_pc = qemu_plugin_tb_vaddr(tb);
    size_t n = qemu_plugin_tb_n_insns(tb);

    if (g_ring_cap > 0 && (g_num_ring_ranges == 0 || in_ranges(g_ring_ranges, g_num_ring_ranges, tb_pc))) {
        TbMeta *meta = g_new(TbMeta, 1);
        meta->pc = tb_pc;
        meta->cpu = 0;
        meta->insns = (uint32_t)n;
        g_mutex_lock(&g_lock);
        g_ptr_array_add(g_tb_metas, meta);
        g_mutex_unlock(&g_lock);
        qemu_plugin_register_vcpu_tb_exec_cb(tb, tb_exec_cb, QEMU_PLUGIN_CB_NO_REGS, meta);
    }

    for (size_t i = 0; i < n; i++) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
        uint64_t va = qemu_plugin_insn_vaddr(insn);
        for (size_t p = 0; p < g_num_probes; p++) {
            if ((va & ~1ULL) == (g_probes[p].va & ~1ULL)) {
                qemu_plugin_register_vcpu_insn_exec_cb(
                    insn, probe_exec_cb, QEMU_PLUGIN_CB_R_REGS, &g_probes[p]);
            }
        }
        if (g_num_mem > 0 || g_num_vmem > 0) {
            qemu_plugin_register_vcpu_mem_cb(
                insn, mem_cb, QEMU_PLUGIN_CB_NO_REGS, QEMU_PLUGIN_MEM_RW,
                (void *)(uintptr_t)va);
        }
    }
}

static void plugin_exit_cb(void *ud)
{
    g_mutex_lock(&g_lock);
    for (unsigned cpu = 0; cpu < MAX_CPUS; cpu++) {
        flush_poll_locked(cpu);
    }
    if (g_dump_ring) {
        dump_ring_locked();
    }
    if (g_out) {
        fflush(g_out);
        if (g_owns_fp) {
            fclose(g_out);
        }
        g_out = NULL;
    }
    if (g_tb_metas) {
        g_ptr_array_free(g_tb_metas, TRUE);
        g_tb_metas = NULL;
    }
    g_mutex_unlock(&g_lock);
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(
    qemu_plugin_id_t id, const qemu_info_t *info, int argc, char **argv)
{
    g_out = stderr;
    g_tb_metas = g_ptr_array_new_with_free_func(g_free);

    for (int i = 0; i < argc; i++) {
        g_auto(GStrv) kv = g_strsplit(argv[i], "=", 2);
        const char *key = kv[0];
        const char *val = (kv[0] && kv[1]) ? kv[1] : "";
        if (strcmp(key, "out") == 0 && val[0]) {
            FILE *fp = fopen(val, "w");
            if (!fp) {
                fprintf(stderr, "tcg_trace: cannot open out=%s\n", val);
                return -1;
            }
            g_out = fp;
            g_owns_fp = true;
        } else if (strcmp(key, "ring") == 0) {
            uint64_t n = 0;
            if (!parse_u64_sum(val, &n) || n > MAX_RING) {
                fprintf(stderr, "tcg_trace: invalid ring=%s\n", val);
                return -1;
            }
            g_ring_cap = (size_t)n;
        } else if (strcmp(key, "ring_range") == 0 && g_num_ring_ranges < MAX_RANGES) {
            if (!parse_range(val, &g_ring_ranges[g_num_ring_ranges++])) {
                fprintf(stderr, "tcg_trace: invalid ring_range=%s\n", val);
                return -1;
            }
        } else if (strcmp(key, "probe") == 0 && g_num_probes < MAX_PROBES) {
            const char *colon = strchr(val, ':');
            if (!colon) {
                fprintf(stderr, "tcg_trace: invalid probe=%s\n", val);
                return -1;
            }
            ProbeSpec *ps = &g_probes[g_num_probes++];
            size_t len = (size_t)(colon - val);
            if (len >= sizeof(ps->id)) {
                len = sizeof(ps->id) - 1;
            }
            memcpy(ps->id, val, len);
            ps->id[len] = '\0';
            if (!parse_u64_sum(colon + 1, &ps->va)) {
                fprintf(stderr, "tcg_trace: invalid probe address %s\n", val);
                return -1;
            }
        } else if (strcmp(key, "deref") == 0 && g_num_derefs < MAX_DEREFS) {
            /* Format: <id>:r<N>+<off>:<len> or <id>:r<N>:<len> */
            g_auto(GStrv) parts = g_strsplit(val, ":", 3);
            if (!parts[0] || !parts[1] || !parts[2]) {
                fprintf(stderr, "tcg_trace: invalid deref=%s\n", val);
                return -1;
            }
            DerefSpec *ds = &g_derefs[g_num_derefs++];
            g_strlcpy(ds->probe_id, parts[0], sizeof(ds->probe_id));
            char *plus = strchr(parts[1], '+');
            g_autofree char *rname = plus ? g_strndup(parts[1], (gsize)(plus - parts[1])) : g_strdup(parts[1]);
            ds->reg_idx = find_reg_index(rname);
            ds->offset = plus ? g_ascii_strtoll(plus + 1, NULL, 0) : 0;
            ds->len = (size_t)g_ascii_strtoull(parts[2], NULL, 0);
            if (ds->reg_idx < 0 || ds->len == 0 || ds->len > 256) {
                fprintf(stderr, "tcg_trace: invalid deref reg/len %s\n", val);
                return -1;
            }
        } else if (strcmp(key, "mem") == 0 && g_num_mem < MAX_RANGES) {
            if (!parse_range(val, &g_mem_ranges[g_num_mem++])) {
                fprintf(stderr, "tcg_trace: invalid mem=%s\n", val);
                return -1;
            }
        } else if (strcmp(key, "vmem") == 0 && g_num_vmem < MAX_RANGES) {
            if (!parse_range(val, &g_vmem_ranges[g_num_vmem++])) {
                fprintf(stderr, "tcg_trace: invalid vmem=%s\n", val);
                return -1;
            }
        } else if (strcmp(key, "max_events") == 0) {
            if (!parse_u64_sum(val, &g_max_events)) {
                fprintf(stderr, "tcg_trace: invalid max_events=%s\n", val);
                return -1;
            }
        } else if (strcmp(key, "dump_ring") == 0) {
            g_dump_ring = atoi(val) != 0;
        } else if (strcmp(key, "exit_on_hit") == 0) {
            g_exit_on_hit = atoi(val) != 0;
        } else {
            fprintf(stderr, "tcg_trace: unknown or overflowing arg %s\n", argv[i]);
            return -1;
        }
    }

    qemu_plugin_register_vcpu_init_cb(id, vcpu_init_cb, NULL);
    qemu_plugin_register_vcpu_tb_trans_cb(id, vcpu_tb_trans_cb, NULL);
    qemu_plugin_register_atexit_cb(id, plugin_exit_cb, NULL);
    return 0;
}
