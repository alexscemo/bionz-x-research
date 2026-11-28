/* Freestanding AArch64 constructor for the ILCE-6700 Jiritsu SET_FILE2 loader.
 *
 * libJiritsu's adj_cmd_set_file2 dlopens and immediately dlcloses the uploaded
 * /tmp/jiritsu/libJit.so. This shared object has no imports and no DT_NEEDED
 * entries. Its only constructor vforks, closes inherited descriptors 3..255,
 * starts a new session and execs `busybox sh <script>`. Nothing is written to
 * /setting or /etc/ld.so.preload and no persistent hook is installed.
 *
 * Build parameters (defaults match the camera):
 *   -DINTERP_PATH='"/bin/busybox"'  -DINTERP_ARG0='"busybox"'  -DINTERP_ARG1='"sh"'
 *   -DSCRIPT_PATH='"/tmp/jiritsu/run.sh"'
 */
#ifndef INTERP_PATH
#define INTERP_PATH "/bin/busybox"
#endif
#ifndef INTERP_ARG0
#define INTERP_ARG0 "busybox"
#endif
#ifndef INTERP_ARG1
#define INTERP_ARG1 "sh"
#endif
#ifndef SCRIPT_PATH
#define SCRIPT_PATH "/tmp/jiritsu/run.sh"
#endif

    .text
    .align 2
    .type launch, %function
launch:
    stp x29, x30, [sp, #-16]!
    mov x29, sp
    mov x8, #220                /* __NR_clone */
    mov x0, #0x4111             /* CLONE_VM | CLONE_VFORK | SIGCHLD */
    mov x1, #0                  /* keep the parent's stack pointer */
    mov x2, #0
    mov x3, #0
    mov x4, #0
    svc #0
    cbnz x0, 9f                 /* parent (pid or -errno): return */
    /* vfork child: only syscalls from here on, never return. */
    mov x20, #3
1:  mov x0, x20
    mov x8, #57                 /* __NR_close */
    svc #0
    add x20, x20, #1
    cmp x20, #256
    b.lt 1b
    mov x8, #157                /* __NR_setsid */
    svc #0
    sub sp, sp, #64
    adr x0, str_arg0
    str x0, [sp, #0]
    adr x0, str_arg1
    str x0, [sp, #8]
    adr x0, str_script
    str x0, [sp, #16]
    str xzr, [sp, #24]          /* argv terminator */
    adr x0, str_path_env
    str x0, [sp, #32]
    str xzr, [sp, #40]          /* envp terminator */
    adr x0, str_interp
    mov x1, sp                  /* argv */
    add x2, sp, #32             /* envp */
    mov x8, #221                /* __NR_execve */
    svc #0
    mov x0, #127
    mov x8, #94                 /* __NR_exit_group */
    svc #0
9:  ldp x29, x30, [sp], #16
    ret
    .size launch, .-launch

str_interp:   .asciz INTERP_PATH
str_arg0:     .asciz INTERP_ARG0
str_arg1:     .asciz INTERP_ARG1
str_script:   .asciz SCRIPT_PATH
str_path_env: .asciz "PATH=/sbin:/bin:/usr/sbin:/usr/bin"

    .section .init_array, "aw"
    .align 3
    .xword launch

    .section .note.GNU-stack, "", %progbits
