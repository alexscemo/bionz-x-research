/*
 * SIGBUS-guarded single-page /dev/mem reader for ILCE-6400 (ro_page).
 * Maps one 4 KiB page via O_RDONLY | O_SYNC mmap (no read() on /dev/mem)
 * and catches SIGBUS with sigsetjmp/siglongjmp so precise external aborts
 * do not reach Sony's em_exception_monitor.
 *
 * Build: arm-linux-gnueabi-gcc -O2 -Wall -Wextra -Werror -static \
 *            tools/native_camera/ro_page.c -o ro_page
 * Usage: ro_page <PAGE_PA_HEX> <OFF_HEX> <LEN>   (LEN in 1..256)
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

static sigjmp_buf g_env;
static volatile sig_atomic_t g_armed = 0;

static void bus_handler(int sig) {
    (void)sig;
    if (g_armed) {
        siglongjmp(g_env, 1);
    }
    _exit(128 + SIGBUS);
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s PA_HEX OFF_HEX LEN\n", argv[0]);
        return 2;
    }
    uint32_t page_pa = (uint32_t)strtoul(argv[1], NULL, 0);
    uint32_t off = (uint32_t)strtoul(argv[2], NULL, 0);
    uint32_t len = (uint32_t)strtoul(argv[3], NULL, 0);
    if ((page_pa & 0xfffu) || off + len > 0x1000u || len == 0u || len > 256u) {
        fprintf(stderr, "bad args\n");
        return 2;
    }

    struct sigaction sa;
    sa.sa_handler = bus_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;
    if (sigaction(SIGBUS, &sa, NULL) != 0) {
        perror("sigaction");
        return 1;
    }

    int fd = open("/dev/mem", O_RDONLY | O_SYNC);
    if (fd < 0) {
        perror("open /dev/mem");
        return 1;
    }

    void *map = mmap(NULL, 0x1000u, PROT_READ, MAP_SHARED, fd, (off_t)page_pa);
    if (map == MAP_FAILED) {
        perror("mmap");
        close(fd);
        return 1;
    }

    if (sigsetjmp(g_env, 1) != 0) {
        g_armed = 0;
        fprintf(stderr, "SIGBUS at 0x%08x\n", page_pa + off);
        munmap(map, 0x1000u);
        close(fd);
        return 3;
    }

    g_armed = 1;
    const volatile uint8_t *p = (const volatile uint8_t *)map + off;
    uint8_t buf[256];
    for (uint32_t i = 0; i < len; i++) {
        buf[i] = p[i];
    }
    g_armed = 0;

    for (uint32_t i = 0; i < len; i++) {
        printf("%02x", buf[i]);
    }
    printf("\n");

    munmap(map, 0x1000u);
    close(fd);
    return 0;
}
