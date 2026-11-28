/* ARMv7 trampolines, atomics, and 3.0.27_nl-rt106+ module header for emunand_guard.ko. */
    .syntax unified
    .arch armv7-a
    .arm

    .section .text, "ax", %progbits
    .align 2

    .global emunand_orig_seq_read_trampoline
    .type emunand_orig_seq_read_trampoline, %function
emunand_orig_seq_read_trampoline:
    mov     ip, sp
    push    {r4, r5, r6, r7, r8, sl, fp, ip, lr, pc}
    sub     fp, ip, #4
    sub     sp, sp, #0x10
    ldr     ip, .Lorig_read_p16
    ldr     pc, [ip]
    .align 2
.Lorig_read_p16:
    .word   emunand_orig_seq_read_plus16
    .size emunand_orig_seq_read_trampoline, . - emunand_orig_seq_read_trampoline

    .global emunand_orig_seq_write_trampoline
    .type emunand_orig_seq_write_trampoline, %function
emunand_orig_seq_write_trampoline:
    mov     ip, sp
    push    {r4, r5, r6, r7, r8, sl, fp, ip, lr, pc}
    sub     fp, ip, #4
    sub     sp, sp, #0x10
    ldr     ip, .Lorig_write_p16
    ldr     pc, [ip]
    .align 2
.Lorig_write_p16:
    .word   emunand_orig_seq_write_plus16
    .size emunand_orig_seq_write_trampoline, . - emunand_orig_seq_write_trampoline

    .global emunand_spin_lock
    .type emunand_spin_lock, %function
emunand_spin_lock:
1:
    ldrex   r1, [r0]
    cmp     r1, #0
    bne     1b
    mov     r1, #1
    strex   r2, r1, [r0]
    cmp     r2, #0
    bne     1b
    dmb     sy
    bx      lr
    .size emunand_spin_lock, . - emunand_spin_lock

    .global emunand_spin_unlock
    .type emunand_spin_unlock, %function
emunand_spin_unlock:
    dmb     sy
    mov     r1, #0
    str     r1, [r0]
    dsb     sy
    bx      lr
    .size emunand_spin_unlock, . - emunand_spin_unlock

    .global emunand_atomic_inc
    .type emunand_atomic_inc, %function
emunand_atomic_inc:
1:
    ldrex   r1, [r0]
    add     r1, r1, #1
    strex   r2, r1, [r0]
    cmp     r2, #0
    bne     1b
    dmb     sy
    bx      lr
    .size emunand_atomic_inc, . - emunand_atomic_inc

    .global emunand_atomic_dec
    .type emunand_atomic_dec, %function
emunand_atomic_dec:
    dmb     sy
1:
    ldrex   r1, [r0]
    sub     r1, r1, #1
    strex   r2, r1, [r0]
    cmp     r2, #0
    bne     1b
    mov     r0, r1
    bx      lr
    .size emunand_atomic_dec, . - emunand_atomic_dec

    .global emunand_barriers_sync
    .type emunand_barriers_sync, %function
emunand_barriers_sync:
    dsb     sy
    isb     sy
    bx      lr
    .size emunand_barriers_sync, . - emunand_barriers_sync

    .section .modinfo, "a", %progbits
    .ascii "license=GPL\0"
    .ascii "description=Reversible CXD90045 eMMC CoW write-guard prototype\0"
    .ascii "depends=\0"
    .ascii "vermagic=3.0.27_nl-rt106+ SMP preempt mod_unload ARMv7 \0"

    .section .gnu.linkonce.this_module, "aw", %progbits
    .align 2
    .global __this_module
    .type __this_module, %object
    .size __this_module, 316
__this_module:
    .space 12, 0
    .ascii "emunand_guard"
    .space (60 - 13), 0
    .space (0xbc - 72), 0
    .word init_module
    .space (0x134 - 0xc0), 0
    .word cleanup_module
    .space (316 - 0x138), 0

    .section .note.GNU-stack, "", %progbits
