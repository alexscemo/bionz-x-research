/* SilentFlashUnlock: SMOD v2 RAM-only mod for Sony ILCE-6400 (2.00 captured layout).
 *
 * Experimental. Hooks ExcMgr's master exclusion evaluator in libObj.so
 * (+0x00c708a0, ref VA 0x069888a0; same target as SilentShutterUnlock, so the two
 * mods must not be loaded together) and returns "no exclusion" for pairs between
 * Silent Shooting (0x9983 PARAMID_SILENT_SHUTTER_MODE, 0xa013 PARAMID_EXC_SILENT_SHUTTER_MODE)
 * and the user-settable flash (strobe) parameters, in both directions.
 *
 * Strobe ObjMsg ParamIDs (0x9500 + libObj PARAMID table index, table at ref VA 0x0130904c):
 *   0x956e CA_EXT_STROBE_MODE      0x956f..0x9571 STROBE_MODE (+_SET, _ACT)
 *   0x95f7..0x95f9 STROBE_CHARGE_ENABLE (+_SET, _ACT)
 *   0x95fa..0x95fc STROBE_POPUP (+_SET, _ACT)
 *   0x95ff..0x9604 STROBE_EV_SHIFT / EV_STEP (+_SET, _ACT)
 *   0x96f0..0x96f2 STROBE_CONTROL_MODE (+_SET, _ACT)
 *   0x972a E_MNT_EXT_STROBE_MODE   0x972c..0x972f E_MNT_STROBE_FUNCTION_*
 *   0x978f..0x9791 MANUAL_STROBE_LEVEL (+_SET, _ACT)
 *   0x9925..0x9927 STROBE_FEL (+_SET, _ACT)
 *   0x9f62..0x9f64 STROBE_WIRELESS_MODE (+_SET, _ACT)
 *
 * v3 also clears the five Silent Shooting FactorIds in libSysDef DefInh::sm_refTbl
 * (ref VA 0x056918b8) on 9 inferred flash FeatureIds (0x180..0x187 except 0x183, 0x769,
 * 0x76b; v4) and restores the saved bits on OFF.
 * Whether av-cam.bin fires the strobe during an electronic-shutter exposure is unverified.
 *
 * Diagnostics: every distinct (src, dst) pair that involves a Silent Shooting param is recorded
 * in .bss (up to 24 pairs, bit i of pair_bypassed set when bypassed) so it can be read back
 * through process memory; ON/OFF append one line to /tmp_bt/mods/silent_flash_unlock.log.
 */
#include <stdint.h>

#define PARAM_SILENT_SHUTTER_MODE      0x9983u
#define PARAM_EXC_SILENT_SHUTTER_MODE  0xa013u

#define MAX_PAIRS 24u
#define DIAG_MAX 160u
#define QMAX 256u
#define PRING 64u

struct silent_flash_state {
    uint32_t magic;         /* 'SFLU' once ON ran */
    uint32_t enabled;
    uint32_t bypass_count;
    uint32_t total_count;
    uint32_t pair_count;
    uint32_t pair_bypassed; /* bit i: pairs[i] was bypassed */
    uint32_t pairs[MAX_PAIRS]; /* (src << 16) | dst */
    uint32_t last_src_val;
    uint32_t last_dst_val;
    /* Diagnostic trace (v2): distinct (src, dst) pairs that pass through the original
     * evaluator, with the last return value and dst_val seen for each. */
    uint32_t diag_count;
    uint32_t diag_overflow;
    uint32_t diag_pair[DIAG_MAX];
    uint32_t diag_ret[DIAG_MAX];
    uint32_t diag_hits[DIAG_MAX];
    /* v3: DefInh::sm_refTbl grey-out bits cleared on ON, restored on OFF. */
    uint32_t inh_patched;
    uint32_t inh_cleared;
    uint32_t inh_saved[3]; /* 9 features * 5 factors = 45 bits */
    /* v5: MWF::InhInfo::GetInhInfo query trace: distinct (type << 16 | FeatureId, caller LR). */
    uint32_t q_count;
    uint32_t q_overflow;
    uint32_t q_key[QMAX];
    uint32_t q_lr[QMAX];
    uint32_t q_hits[QMAX];
    /* v6: libObj ParamDB::Set (+0xa11d28) ring of writes to strobe / silent params:
     * {seq, param, caller LR, Param words 0..3}. */
    uint32_t p_seq;
    uint32_t p_ring[PRING][7];
    /* v7: STROBE_MODE_ACT override state (last values seen through ParamDB::Set). */
    uint32_t user_strobe;   /* PARAMID_STROBE_MODE (0x956f) */
    uint32_t silent_act;    /* PARAMID_SILENT_SHUTTER_MODE_ACT (0x9985) */
    uint32_t popup_act;     /* PARAMID_STROBE_POPUP_ACT (0x95fc) */
    uint32_t act_overrides;
    /* v8 */
    uint32_t popup_seen;     /* 0x95fc observed since load */
    uint32_t mget_overrides; /* StillRec model message-getter overrides */
    /* v9 */
    uint32_t avcam_code;     /* last av-cam swap result character (v10: charge branch) */
};

_Static_assert(sizeof(struct silent_flash_state) == 196u + 12u * DIAG_MAX + 12u * QMAX + 28u * PRING,
               "state size must match --bss");

#define VALUEID_SILENT_SHUTTER_ON  0x0451u
#define VALUEID_STROBE_MODE_AUTO   0x00a9u
#define VALUEID_STROBE_MODE_OFF    0x00abu
#define VALUEID_STROBE_REAR_SYNC   0x00afu

#define DEF_INH_WORDS_PER_FEAT 160u
#define INH_FEATS 9u

extern volatile uint32_t *sflash_get_inh_ref_tbl(void);

/* Flash FeatureIds (v4, inferred from live data, not yet confirmed by name):
 * 0x180..0x187 except 0x183, plus 0x769 and 0x76b. With Silent Shooting ON (active factor
 * 0x0de2) these were inhibited, and they share the Silent Shooting factor signature
 * {0x0a83, 0x0de2, 0x114f, 0x13bb}. 0x183 carries no Silent Shooting factor and fits
 * "Flash Off". The v3 candidates (0x118..0x121, 0x146, 0x148, 0x51b..0x520) were wrong:
 * live, 0x118..0x148 were not inhibited and 0x51b..0x520 are blocked by 0x114d.
 * Computed at run time (no data tables, keeps the image free of absolute relocations). */
static uint32_t inh_feat(uint32_t i) {
    if (i < 3u) return 0x0180u + i;          /* 0x180..0x182 */
    if (i < 7u) return 0x0184u + (i - 3u);   /* 0x184..0x187 */
    return (i == 7u) ? 0x0769u : 0x076bu;
}

static uint32_t silent_factor(uint32_t f) {
    switch (f) {
    case 0u: return 0x0a83u;
    case 1u: return 0x0de2u;
    case 2u: return 0x114fu;
    case 3u: return 0x1296u;
    default: return 0x13bbu;
    }
}

static void patch_inh(volatile struct silent_flash_state *st) {
    if (st->inh_patched) return;
    volatile uint32_t *tbl = sflash_get_inh_ref_tbl();
    uint32_t idx = 0u, cleared = 0u;
    st->inh_saved[0] = st->inh_saved[1] = st->inh_saved[2] = 0u;
    for (uint32_t i = 0u; i < INH_FEATS; i++) {
        uint32_t feat = inh_feat(i);
        for (uint32_t f = 0u; f < 5u; f++, idx++) {
            uint32_t fac = silent_factor(f);
            volatile uint32_t *cell = tbl + feat * DEF_INH_WORDS_PER_FEAT + (fac >> 5);
            uint32_t mask = 1u << (fac & 31u);
            if (*cell & mask) {
                st->inh_saved[idx >> 5] |= 1u << (idx & 31u);
                *cell &= ~mask;
                cleared++;
            }
        }
    }
    st->inh_cleared = cleared;
    st->inh_patched = 1u;
}

static void restore_inh(volatile struct silent_flash_state *st) {
    if (!st->inh_patched) return;
    volatile uint32_t *tbl = sflash_get_inh_ref_tbl();
    uint32_t idx = 0u;
    for (uint32_t i = 0u; i < INH_FEATS; i++) {
        uint32_t feat = inh_feat(i);
        for (uint32_t f = 0u; f < 5u; f++, idx++) {
            if (st->inh_saved[idx >> 5] & (1u << (idx & 31u))) {
                uint32_t fac = silent_factor(f);
                tbl[feat * DEF_INH_WORDS_PER_FEAT + (fac >> 5)] |= 1u << (fac & 31u);
            }
        }
    }
    st->inh_saved[0] = st->inh_saved[1] = st->inh_saved[2] = 0u;
    st->inh_cleared = 0u;
    st->inh_patched = 0u;
}

extern uint8_t __smod_bss[] __attribute__((visibility("hidden")));
#define S_STATE ((volatile struct silent_flash_state *)__smod_bss)

extern uint32_t sflash_orig_check(
    void *self,
    uint32_t src_param,
    uint32_t src_val,
    uint32_t dst_param,
    uint32_t dst_val,
    uint32_t *out_buf);

static inline int is_silent_param(uint32_t p) {
    return (p == PARAM_SILENT_SHUTTER_MODE) || (p == PARAM_EXC_SILENT_SHUTTER_MODE);
}

static inline int is_strobe_param(uint32_t p) {
    return (p >= 0x956eu && p <= 0x9571u) ||
           (p >= 0x95f7u && p <= 0x95fcu) ||
           (p >= 0x95ffu && p <= 0x9604u) ||
           (p >= 0x96f0u && p <= 0x96f2u) ||
           (p == 0x972au) ||
           (p >= 0x972cu && p <= 0x972fu) ||
           (p >= 0x978fu && p <= 0x9791u) ||
           (p >= 0x9925u && p <= 0x9927u) ||
           (p >= 0x9f62u && p <= 0x9f64u);
}

static void record_pair(volatile struct silent_flash_state *st, uint32_t src, uint32_t dst, int bypassed) {
    uint32_t key = ((src & 0xffffu) << 16) | (dst & 0xffffu);
    uint32_t n = st->pair_count;
    for (uint32_t i = 0u; i < n && i < MAX_PAIRS; i++) {
        if (st->pairs[i] == key) {
            if (bypassed) {
                st->pair_bypassed |= (1u << i);
            }
            return;
        }
    }
    if (n < MAX_PAIRS) {
        st->pairs[n] = key;
        if (bypassed) {
            st->pair_bypassed |= (1u << n);
        }
        st->pair_count = n + 1u;
    }
}

__attribute__((visibility("default")))
uint32_t sflash_hook(
    void *self,
    uint32_t src_param,
    uint32_t src_val,
    uint32_t dst_param,
    uint32_t dst_val,
    uint32_t *out_buf) {
    volatile struct silent_flash_state *st = S_STATE;
    if (st->enabled) {
        st->total_count++;
        int s_src = is_silent_param(src_param);
        int s_dst = is_silent_param(dst_param);
        if (s_src || s_dst) {
            int hit = (s_src && is_strobe_param(dst_param)) || (s_dst && is_strobe_param(src_param));
            record_pair(st, src_param, dst_param, hit);
            if (hit) {
                st->bypass_count++;
                st->last_src_val = src_val;
                st->last_dst_val = dst_val;
                return 0u;
            }
        }
    }
    uint32_t ret = sflash_orig_check(self, src_param, src_val, dst_param, dst_val, out_buf);
    if (st->enabled) {
        uint32_t key = ((src_param & 0xffffu) << 16) | (dst_param & 0xffffu);
        uint32_t n = st->diag_count;
        uint32_t i = 0u;
        while (i < n && st->diag_pair[i] != key) i++;
        if (i < n) {
            st->diag_ret[i] = ret;
            st->diag_hits[i]++;
        } else if (n < DIAG_MAX) {
            st->diag_pair[n] = key;
            st->diag_ret[n] = ret;
            st->diag_hits[n] = 1u;
            st->diag_count = n + 1u;
        } else {
            st->diag_overflow++;
        }
    }
    return ret;
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

/* v10: av-cam strobe charge gate (ILCE-6400 av-cam.bin dd8f8d0c..., ORIL VA = PA + 0x60000000).
 * In the executed branch of the charge controller 0x63bcfb40 (taken because *(0x6444C78C) == 1,
 * verified live), 0x63bcfd22 `bl 0x638b4864` reads the global flag 0x644AC6AA and
 * 0x63bcfd26 `cbz r0, 0x63bcfd2e` skips `movs r3,#3; strb r3,[r4,#0x88]` only when it is clear.
 * With Silent Shooting ON obj5+0x88 was 3 and charging off (+0x7f/+0x80 = 0); with Silent OFF
 * +0x88 was 0 and charging on (read-only, 2026-11-27). The patch turns that cbz into an
 * unconditional `b 0x63bcfd2e` (0xb110 -> 0xe002), so the flag no longer forces state 3.
 * The (v9) inhibit table is not used on this path and is no longer touched.
 * Guarded swap of one halfword: both directions require the surrounding halfwords
 * (bl 0x638b4864 before, `movs r3,#3; strb.w` after) and the exact source opcode;
 * anything else is left untouched.
 * Codes: w written, a already in target state, x foreign content (untouched), v verify failed,
 * o open failed, m mmap failed. */
#define AVCAM_PAGE_PA   0x03BCF000u
#define AVCAM_BR_OFF    0xd26u    /* VA 0x63bcfd26 */
#define AVCAM_BR_STOCK  0xb110u   /* cbz r0, #+4 */
#define AVCAM_BR_PATCH  0xe002u   /* b   #+4     */
/* v11: second gate in the same branch. 0x63bcfdec loads av-cam's shutter mode [r5+0xc]
 * (2 with Silent Shooting ON, 1 with it OFF; verified live). Modes in mask 0x7b set
 * obj5+0x86 = 1 (charge allowed); mode 2 (tst #4) clears r6, r7 and +0x86. Widening the mask of
 * `ands r2, r3, #0x7b` at 0x63bcfdf8 to 0x7f makes mode 2 take the mode-1 path.
 * Second halfword of that Thumb-2 instruction: 0x027b -> 0x027f. */
#define AVCAM_MASK_OFF   0xdfau   /* VA 0x63bcfdfa */
#define AVCAM_MASK_STOCK 0x027bu
#define AVCAM_MASK_PATCH 0x027fu

static char hw_swap(volatile uint16_t *p, int ctx_ok, uint16_t stock, uint16_t patch, int enable) {
    if (!ctx_ok) return 'x';
    uint16_t from = enable ? stock : patch;
    uint16_t to = enable ? patch : stock;
    if (p[0] == to) return 'a';
    if (p[0] != from) return 'x';
    p[0] = to;
    __asm__ volatile("dsb" ::: "memory");
    return (p[0] == to) ? 'w' : 'v';
}

/* Returns two result characters: low byte = charge-state branch, high byte = mode mask. */
static uint32_t avcam_branch_swap(int enable_charge) {
    long fd = sc3(5 /* open */, (long)"/dev/mem", 02 | 04010000 /* O_RDWR | O_SYNC */, 0);
    if (fd < 0) return 'o' | ('o' << 8);
    long base = sc6(192 /* mmap2 */, 0, 4096, 3 /* PROT_READ | PROT_WRITE */, 1 /* MAP_SHARED */, fd,
                    (long)(AVCAM_PAGE_PA >> 12));
    if (base < 0 && base > -4096) { sc3(6, fd, 0, 0); return 'm' | ('m' << 8); }
    volatile uint16_t *b = (volatile uint16_t *)(base + AVCAM_BR_OFF);
    volatile uint16_t *k = (volatile uint16_t *)(base + AVCAM_MASK_OFF);
    /* context: `bl 0x638b4864` before, `movs r3,#3; strb.w` after the branch */
    int bctx = b[-2] == 0xf4e4u && b[-1] == 0xfd9fu && b[1] == 0x2303u && b[2] == 0xf884u;
    /* context: `lsls.w r3, r1, r3` before, `ands` first halfword, `bne` after */
    int kctx = k[-3] == 0xfa11u && k[-2] == 0xf303u && k[-1] == 0xf013u && k[1] == 0xd103u;
    char c1, c2;
    if (enable_charge) {
        c1 = hw_swap(b, bctx, AVCAM_BR_STOCK, AVCAM_BR_PATCH, 1);
        c2 = hw_swap(k, kctx, AVCAM_MASK_STOCK, AVCAM_MASK_PATCH, 1);
    } else {
        c2 = hw_swap(k, kctx, AVCAM_MASK_STOCK, AVCAM_MASK_PATCH, 0);
        c1 = hw_swap(b, bctx, AVCAM_BR_STOCK, AVCAM_BR_PATCH, 0);
    }
    sc3(91 /* munmap */, base, 4096, 0);
    sc3(6 /* close */, fd, 0, 0);
    return (uint32_t)(uint8_t)c1 | ((uint32_t)(uint8_t)c2 << 8);
}

static char *put_str(char *dst, const char *s) {
    while (*s) *dst++ = *s++;
    return dst;
}

static char *put_hex(char *dst, uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) {
        uint32_t nib = (v >> i) & 0xfu;
        *dst++ = (char)(nib < 10 ? ('0' + nib) : ('a' + nib - 10));
    }
    return dst;
}

static void write_log(int on, volatile struct silent_flash_state *st) {
    char buf[112];
    char *p = buf;
    p = put_str(p, "silent_flash on=");
    *p++ = on ? '1' : '0';
    p = put_str(p, " bypass=0x");
    p = put_hex(p, st->bypass_count);
    p = put_str(p, " total=0x");
    p = put_hex(p, st->total_count);
    p = put_str(p, " pairs=0x");
    p = put_hex(p, st->pair_count);
    p = put_str(p, " inh=0x");
    p = put_hex(p, st->inh_cleared);
    p = put_str(p, " avcam=");
    *p++ = (st->avcam_code & 0xffu) ? (char)(st->avcam_code & 0xffu) : '-';
    *p++ = (st->avcam_code >> 8) ? (char)(st->avcam_code >> 8) : '-';
    *p++ = '\n';

    long fd = sc3(5 /* open */, (long)"/tmp_bt/mods/silent_flash_unlock.log",
                  01 | 0100 | 02000 /* O_WRONLY | O_CREAT | O_APPEND */, 0644);
    if (fd >= 0) {
        sc3(4 /* write */, fd, (long)buf, (long)(p - buf));
        sc3(6 /* close */, fd, 0, 0);
    }
}

/* v5: trace hooks on libMWF MWF::InhInfo::GetInhInfo (two overloads). Observation only:
 * arguments are passed through unchanged to the replayed originals. */
extern uint32_t sflash_orig_inh3(uint32_t feat, void *msg, uint32_t type);
extern uint32_t sflash_orig_inh2(uint32_t feat, void *msg);

static void record_query(uint32_t feat, uint32_t type, uint32_t lr) {
    volatile struct silent_flash_state *st = S_STATE;
    if (!st->enabled) return;
    uint32_t key = ((type & 0xffffu) << 16) | (feat & 0xffffu);
    uint32_t n = st->q_count;
    for (uint32_t i = 0u; i < n && i < QMAX; i++) {
        if (st->q_key[i] == key && st->q_lr[i] == lr) {
            st->q_hits[i]++;
            return;
        }
    }
    if (n < QMAX) {
        st->q_key[n] = key;
        st->q_lr[n] = lr;
        st->q_hits[n] = 1u;
        st->q_count = n + 1u;
    } else {
        st->q_overflow++;
    }
}

__attribute__((visibility("default")))
uint32_t sflash_inh3_hook(uint32_t feat, void *msg, uint32_t type) {
    record_query(feat, type, (uint32_t)__builtin_return_address(0));
    return sflash_orig_inh3(feat, msg, type);
}

__attribute__((visibility("default")))
uint32_t sflash_inh2_hook(uint32_t feat, void *msg) {
    record_query(feat, 0xffffu, (uint32_t)__builtin_return_address(0));
    return sflash_orig_inh2(feat, msg);
}

/* v6: observation hook on libObj ParamDB::Set(obj, param_id, const MWF::Param *). */
extern uint32_t sflash_orig_pset(void *obj, uint32_t param, const uint32_t *p);

static inline int is_traced_param(uint32_t p) {
    return is_strobe_param(p) || is_silent_param(p) ||
           p == 0x9984u || p == 0x9985u || p == 0xa014u || p == 0xa015u ||
           p == 0x95feu || p == 0x95fdu || p == 0x9a76u;
}

/* v8: read a value straight from libObj's ParamDB. The camera-state singleton pointer sits in
 * the GOT slot at libObj +0x137257c (ref VA 0x0708a57c); ParamDB = *(singleton + 0x1a20) is an
 * array of MWF::Param * indexed by (param - 0x9500); word 0 of the Param holds the value
 * (all verified read-only on the live camera). Returns 0xffffffff when unavailable. */
extern volatile uint32_t *sflash_get_state_got(void);

static uint32_t paramdb_value(uint32_t param) {
    volatile uint32_t *got = sflash_get_state_got();
    uint32_t single = *got;
    if (!single) return 0xffffffffu;
    uint32_t db = *(volatile uint32_t *)(single + 0x1a20u);
    if (!db || param < 0x9500u || param > 0x9500u + 0xb4au) return 0xffffffffu;
    uint32_t slot = *(volatile uint32_t *)(db + 4u * (param - 0x9500u));
    if (!slot) return 0xffffffffu;
    return *(volatile uint32_t *)slot;
}

/* User flash mode to restore when Silent Shooting forced the actual mode OFF, or 0. */
static uint32_t override_mode(uint32_t silent_act) {
    if (silent_act != VALUEID_SILENT_SHUTTER_ON) return 0u;
    if (paramdb_value(0x95fcu) != 1u) return 0u;           /* STROBE_POPUP_ACT: flash raised */
    uint32_t user = paramdb_value(0x956fu);                 /* STROBE_MODE: user selection */
    if (user < VALUEID_STROBE_MODE_AUTO || user > VALUEID_STROBE_REAR_SYNC ||
        user == VALUEID_STROBE_MODE_OFF) return 0u;
    return user;
}

__attribute__((visibility("default")))
uint32_t sflash_pset_hook(void *obj, uint32_t param, const uint32_t *p) {
    volatile struct silent_flash_state *st = S_STATE;
    if (st->enabled && p) {
        /* Word 0 of the ParamStd_Template<int> object holds the value (observed on camera:
         * 0x9983 = 0x450/0x451, 0x9571 = 0xaa..0xae). */
        if (param == 0x956fu) st->user_strobe = p[0];
        else if (param == 0x9985u) st->silent_act = p[0];
        else if (param == 0x95fcu) { st->popup_act = p[0]; st->popup_seen = 1u; }
        else if (param == 0x9571u && p[0] == VALUEID_STROBE_MODE_OFF) {
            /* The SILENT_ACT write precedes STROBE_MODE_ACT in the same message (trace). */
            uint32_t user = override_mode(st->silent_act);
            if (user) {
                /* Silent Shooting forced the actual strobe mode OFF although the user selected
                 * a flash mode and the flash is raised: keep the user's mode instead. */
                ((uint32_t *)p)[0] = user;
                st->act_overrides++;
            }
        }
    }
    if (st->enabled && is_traced_param(param)) {
        uint32_t seq = __sync_fetch_and_add(&st->p_seq, 1u);
        volatile uint32_t *e = st->p_ring[seq % PRING];
        e[0] = seq;
        e[1] = param;
        e[2] = (uint32_t)__builtin_return_address(0);
        if (p) {
            e[3] = p[0];
            e[4] = p[1];
            e[5] = p[2];
            e[6] = p[3];
        } else {
            e[3] = e[4] = e[5] = e[6] = 0u;
        }
    }
    return sflash_orig_pset(obj, param, p);
}

/* v8: the StillRec model (source of the view's flash-mode icon) reads STROBE_MODE_ACT from the
 * notification message itself via libObj +0x6e0d1c (model, msg, param, *out, a5; 0 = found),
 * not from ParamDB. Fix the value it reads the same way. */
extern uint32_t sflash_orig_mget(void *model, void *msg, uint32_t param, uint32_t *out, uint32_t a5);

__attribute__((visibility("default")))
uint32_t sflash_mget_hook(void *model, void *msg, uint32_t param, uint32_t *out, uint32_t a5) {
    uint32_t r = sflash_orig_mget(model, msg, param, out, a5);
    volatile struct silent_flash_state *st = S_STATE;
    if (st->enabled && r == 0u && param == 0x9571u && out && *out == VALUEID_STROBE_MODE_OFF) {
        uint32_t silent = 0u;
        if (sflash_orig_mget(model, msg, 0x9985u, &silent, a5) != 0u) {
            silent = paramdb_value(0x9985u);   /* not in this message: current state */
        }
        uint32_t user = override_mode(silent);
        if (user) {
            *out = user;
            st->mget_overrides++;
        }
    }
    return r;
}

__attribute__((visibility("default")))
int sflash_on(void) {
    volatile struct silent_flash_state *st = S_STATE;
    st->magic = 0x554c4653u; /* "SFLU" */
    patch_inh(st);
    st->avcam_code = avcam_branch_swap(1);
    st->enabled = 1u;
    __sync_synchronize();
    write_log(1, st);
    return 0;
}

__attribute__((visibility("default")))
int sflash_off(void) {
    volatile struct silent_flash_state *st = S_STATE;
    st->enabled = 0u;
    restore_inh(st);
    st->avcam_code = avcam_branch_swap(0);
    __sync_synchronize();
    write_log(0, st);
    return 0;
}
