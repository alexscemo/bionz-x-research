/* RearLcd60fps: SMOD v2 RAM-only mod for Sony ILCE-6400 (2.00 captured layout)
 * and ILCE-6400A (1.01).
 *
 * Problem solved:
 *   When Sony's "Display Quality" setting (BK4 0x01070c8a) is set to "High" (0) so the
 *   XGA OLED EVF uses its sharper 1996x800 sensor readout (BK4 0x0107147b = 0x00 @ 30 fps),
 *   viewUnified2.so also throttles the Rear LCD (dev == 1) Live-View (EE) frame rate in half
 *   from 59.94 fps to 29.97 fps via BK4 0x01071489 = 0x02, even though the Rear LCD's own
 *   display-resolution byte in High (0x0107147d = 0x01) is identical to Standard
 *   (0x0107147e = 0x01, IMX284 col 3 1996x570).
 *
 * Mechanism (Mod Option A):
 *   1. Hooks Backup_read(int id, void *buf) in libbackup.so (+0x1204) and, when enabled,
 *      overrides only BK4 unit 0x01071489 (NoAdapter + High Quality + Rear LCD EE FrameRate)
 *      in RAM from 0x02 (30 fps) to 0x01 (60 fps, matching 0x0107148a). All 5 viewUnified2.so
 *      callers (eye-sensor EVF<->LCD transitions, menu Display Quality changes, playback->rec
 *      return, and lens/adapter updates) funnel through Backup_read(0x01071489), while EVF
 *      High Quality (0x0107147b = 0x00, 0x01071487 = 0x02) and LA-EA mount-adapter paths
 *      (0x01071481..0x01071484) remain 100% stock. Backup.bin is never written.
 *   2. On live toggle ON / OFF in MENU -> Mods, queries Sony's mount-adapter helper and active
 *      panel-output property (0x1200600b). Whenever no LA-EA adapter (2/3) is attached and the
 *      Rear LCD (dev == 1) is currently active, immediately invokes
 *      CmnViewModelWrpCameraEeStatus::setCameraDispResolutionEEFrameRateNoAdapter(1) so the
 *      Rear LCD switches between 59.94 fps (ON) and stock 29.97 fps (OFF) without requiring
 *      an EVF eye-sensor cycle or Display Quality menu toggle.
 *
 * Log: /tmp_bt/mods/rear_lcd_60fps.log
 */
#include <stdint.h>

#define BK4_NOADAPTER_HIGH_LCD_EE_FPS  0x01071489u
#define EE_FPS_MODE_60FPS              0x01u

#define PANEL_DEV_REAR_LCD             1
#define MOUNT_ADAPTER_LAEA_2_4         2u
#define MOUNT_ADAPTER_LAEA_1_3         3u

struct rear_lcd_60fps_state {
    uint32_t enabled;
    uint32_t override_count;
    uint32_t read_hits;
    uint32_t live_apply_count;
    uint32_t last_panel_dev;
    uint32_t last_adapter;
    uint32_t last_orig_val;
    uint32_t toggle_seq;
};

_Static_assert(sizeof(struct rear_lcd_60fps_state) == 32u, "state size must match --bss 32");

extern uint32_t __smod_bss[] __attribute__((visibility("hidden"), aligned(4)));
#define R_STATE ((volatile struct rear_lcd_60fps_state *)(void *)__smod_bss)

extern int rlcd_orig_backup_read(uint32_t id, void *buf);
extern uint32_t rlcd_get_mount_adapter_type(void);
extern int rlcd_get_panel_out_device(void);
extern void rlcd_set_ee_fps_no_adapter(int dev);

static inline uint32_t target_bk4_id(void) {
    uint32_t id;
    __asm__ volatile(
        "movw %0, #0x1489\n\t"
        "movt %0, #0x0107"
        : "=r"(id)
    );
    return id;
}

__attribute__((visibility("default")))
int rlcd_hook_backup_read(uint32_t id, void *buf) {
    int rc = rlcd_orig_backup_read(id, buf);
    if (rc == 0 && buf != (void *)0 && id == target_bk4_id()) {
        volatile struct rear_lcd_60fps_state *st = R_STATE;
        st->read_hits++;
        if (st->enabled) {
            volatile uint8_t *dst = (volatile uint8_t *)buf;
            st->last_orig_val = (uint32_t)(*dst);
            *dst = EE_FPS_MODE_60FPS;
            st->override_count++;
        }
    }
    return rc;
}

static void apply_live_if_rear_lcd(volatile struct rear_lcd_60fps_state *st) {
    uint32_t adp = rlcd_get_mount_adapter_type() & 0xffu;
    int dev = rlcd_get_panel_out_device();
    st->last_adapter = adp;
    st->last_panel_dev = (uint32_t)dev;
    if (adp == MOUNT_ADAPTER_LAEA_2_4 || adp == MOUNT_ADAPTER_LAEA_1_3) {
        return;
    }
    if (dev == PANEL_DEV_REAR_LCD) {
        rlcd_set_ee_fps_no_adapter(PANEL_DEV_REAR_LCD);
        st->live_apply_count++;
    }
}

static long sc3(long n, long a, long b, long c) {
    register long r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static char *put_str(char *dst, const char *s) {
    while (*s) {
        *dst++ = *s++;
    }
    return dst;
}

static char *put_hex(char *dst, uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) {
        uint32_t nib = (v >> i) & 0xfu;
        *dst++ = (char)(nib < 10u ? ('0' + nib) : ('a' + nib - 10u));
    }
    return dst;
}

static void write_log(int on, const volatile struct rear_lcd_60fps_state *st) {
    char buf[128];
    char *p = buf;
    p = put_str(p, "rear_lcd_60fps on=");
    *p++ = on ? '1' : '0';
    p = put_str(p, " ovr=0x");
    p = put_hex(p, st->override_count);
    p = put_str(p, " hits=0x");
    p = put_hex(p, st->read_hits);
    p = put_str(p, " apply=0x");
    p = put_hex(p, st->live_apply_count);
    p = put_str(p, " dev=0x");
    p = put_hex(p, st->last_panel_dev);
    p = put_str(p, " adp=0x");
    p = put_hex(p, st->last_adapter);
    *p++ = '\n';

    long fd = sc3(5 /* open */, (long)"/tmp_bt/mods/rear_lcd_60fps.log",
                  01 | 0100 | 02000 /* O_WRONLY | O_CREAT | O_APPEND */, 0644);
    if (fd >= 0) {
        sc3(4 /* write */, fd, (long)buf, (long)(p - buf));
        sc3(6 /* close */, fd, 0, 0);
    }
}

__attribute__((visibility("default")))
int rlcd_on(void) {
    volatile struct rear_lcd_60fps_state *st = R_STATE;
    st->enabled = 1u;
    st->toggle_seq++;
    __sync_synchronize();
    apply_live_if_rear_lcd(st);
    write_log(1, st);
    return 0;
}

__attribute__((visibility("default")))
int rlcd_off(void) {
    volatile struct rear_lcd_60fps_state *st = R_STATE;
    st->enabled = 0u;
    st->toggle_seq++;
    __sync_synchronize();
    apply_live_if_rear_lcd(st);
    write_log(0, st);
    return 0;
}
