.syntax unified
.thumb
.section .text

/* Replays the 12 original bytes of ExcMgr's exclusion evaluator (libObj.so +0x00c708a0,
 * ref VA 0x069888a0) and resumes at +12 (0x069888ac, Thumb 0x069888ad). Same contract as
 * mods/silent_shutter_unlock/silent_shutter_unlock_trampoline.S. */
.balign 4
.global sflash_orig_check
.hidden sflash_orig_check
.thumb_func
sflash_orig_check:
    push.w {r4, r5, r6, r7, r8, lr}
    mov r4, r0
    movw r0, #0xc0b6
    add r7, sp, #0
    ldr.w pc, [pc, #0]
    .word 0x069888ad

.balign 4
.global sflash_get_inh_ref_tbl
.hidden sflash_get_inh_ref_tbl
.thumb_func
sflash_get_inh_ref_tbl:
    ldr r0, .Linh_ref_tbl
    bx lr
.align 2
.Linh_ref_tbl:
    .word 0x056918b8

/* v5: replays of libMWF MWF::InhInfo::GetInhInfo prologues (libMWF ref base 0x040f7000).
 * The PC-relative literal loads are replaced by their constant values. */
.balign 4
.global sflash_orig_inh3
.hidden sflash_orig_inh3
.thumb_func
sflash_orig_inh3:                 /* +0x1e32c: 2de9f041 0d46 1949 82b0 8046 */
    push.w {r4, r5, r6, r7, r8, lr}
    mov r5, r1
    movw r1, #0x28dc              /* literal at +0x1e398 = 0x000128dc */
    movt r1, #0x0001
    sub sp, #8
    mov r8, r0
    .balign 4                     /* ldr.w pc,[pc,#0] must be 4-aligned */
    ldr.w pc, [pc, #0]
    .word 0x04115339              /* resume at +0x1e338 (Thumb) */

.balign 4
.global sflash_orig_inh2
.hidden sflash_orig_inh2
.thumb_func
sflash_orig_inh2:                 /* +0x1e3a4: f0b5 0d46 1749 83b0 0646 00af */
    push {r4, r5, r6, r7, lr}
    mov r5, r1
    movw r1, #0x287e              /* literal at +0x1e408 = 0x0001287e */
    movt r1, #0x0001
    sub sp, #12
    mov r6, r0
    add r7, sp, #0
    .balign 4
    ldr.w pc, [pc, #0]
    .word 0x041153b1              /* resume at +0x1e3b0 (Thumb) */

/* v6: replay of libObj ParamDB::Set prologue (+0xa11d28, ref VA 0x06729d28):
 * 2de9f843 0446 00af 1646 8846 (no PC-relative instructions). */
.balign 4
.global sflash_orig_pset
.hidden sflash_orig_pset
.thumb_func
sflash_orig_pset:
    push.w {r3, r4, r5, r6, r7, r8, r9, lr}
    mov r4, r0
    add r7, sp, #0
    mov r6, r2
    mov r8, r1
    .balign 4
    ldr.w pc, [pc, #0]
    .word 0x06729d35              /* resume at +0xa11d34 (Thumb) */

/* v8: GOT slot holding libObj's camera-state singleton pointer (+0x137257c). */
.balign 4
.global sflash_get_state_got
.hidden sflash_get_state_got
.thumb_func
sflash_get_state_got:
    ldr r0, .Lstate_got
    bx lr
.align 2
.Lstate_got:
    .word 0x0708a57c

/* v8: replay of the StillRec model message getter prologue (+0x6e0d1c, ref VA 0x063f8d1c):
 * f0b5 00af 1546 1c46 7e69 1eb9 (cbnz r6 -> +0x6e0d30; fall-through -> +0x6e0d28). */
.balign 4
.global sflash_orig_mget
.hidden sflash_orig_mget
.thumb_func
sflash_orig_mget:
    push {r4, r5, r6, r7, lr}
    add r7, sp, #0
    mov r5, r2
    mov r4, r3
    ldr r6, [r7, #20]
    cbnz r6, 1f
    .balign 4
    ldr.w pc, [pc, #0]
    .word 0x063f8d29              /* +0x6e0d28 (Thumb): a5 == 0 path */
    .balign 4
1:  ldr.w pc, [pc, #0]
    .word 0x063f8d31              /* +0x6e0d30 (Thumb): a5 != 0 path */

.section .note.GNU-stack,"",%progbits
