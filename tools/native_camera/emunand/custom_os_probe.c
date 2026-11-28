/* Minimal freestanding ARMv7 Custom-OS chroot execution probe (-nostdlib).
 * Executed via `chroot /tmp_bt/emunand/mnt /sbin/custom_os_probe.elf` to verify
 * PROT_EXEC ELF mapping and syscall execution directly from an SD-backed ext2
 * Custom-OS package without touching internal eMMC.
 */
static inline long sc0(long n) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = 0;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}
static inline long sc1(long n, long a) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}
static inline long sc3(long n, long a, long b, long c) {
    register long r7 __asm__("r7") = n, r0 __asm__("r0") = a, r1 __asm__("r1") = b, r2 __asm__("r2") = c;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7), "r"(r1), "r"(r2) : "memory", "cc");
    return r0;
}

__attribute__((used)) static int probe_main(void) {
    char buf[128];
    int fd = (int)sc3(5 /* SYS_open */, (long)"/etc/os-release", 0, 0);
    if (fd < 0) return 2;
    long n = sc3(3 /* SYS_read */, fd, (long)buf, sizeof(buf) - 1);
    sc1(6 /* SYS_close */, fd);
    if (n <= 0) return 3;

    long uid = sc0(199 /* SYS_getuid32 */);
    if (uid != 0) return 4;

    static const char prefix[] = "CUSTOM_OS_CHROOT_EXEC_OK ";
    sc3(4 /* SYS_write */, 1, (long)prefix, sizeof(prefix) - 1);
    sc3(4 /* SYS_write */, 1, (long)buf, n);
    return 0;
}

__asm__(
    ".text\n"
    ".global _start\n"
    ".type _start, %function\n"
    "_start:\n"
    "    bic sp, sp, #7\n"
    "    bl  probe_main\n"
    "    mov r7, #1\n"
    "    svc 0\n"
);
