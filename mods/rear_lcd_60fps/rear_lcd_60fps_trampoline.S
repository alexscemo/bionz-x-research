.syntax unified
.thumb
.section .text

#ifdef TARGET_ILCE6400A
#define BACKUP_READ_RESUME_VA   0x0405c213
#define GET_MOUNT_ADAPTER_VA    0x131f3a0d
#define GET_PANEL_OUT_DEV_VA    0x1334de61
#define SET_EE_FPS_NO_ADP_VA    0x1334c9e1
#else
#define BACKUP_READ_RESUME_VA   0x0401f213
#define GET_MOUNT_ADAPTER_VA    0x15890fe1
#define GET_PANEL_OUT_DEV_VA    0x159e9e21
#define SET_EE_FPS_NO_ADP_VA    0x159e89a1
#endif

/* Replays the 14-byte position-independent prologue of Backup_read (libbackup.so + 0x1204)
 * and branches to Backup_read + 14 (+0x1212 | 1):
 *   +0x00: 80b5      push   {r7, lr}
 *   +0x02: 84b0      sub    sp, #0x10
 *   +0x04: 02af      add    r7, sp, #8
 *   +0x06: 07f10803  add.w  r3, r7, #8
 *   +0x0a: 4ff0ff32  mov.w  r2, #-1
 */
.balign 4
.global rlcd_orig_backup_read
.hidden rlcd_orig_backup_read
.thumb_func
rlcd_orig_backup_read:
    push {r7, lr}
    sub sp, #0x10
    add r7, sp, #8
    add.w r3, r7, #8
    mov.w r2, #-1
    nop
    ldr.w pc, [pc, #0]
    .word BACKUP_READ_RESUME_VA

.balign 4
.global rlcd_get_mount_adapter_type
.hidden rlcd_get_mount_adapter_type
.thumb_func
rlcd_get_mount_adapter_type:
    ldr.w pc, [pc, #0]
    .word GET_MOUNT_ADAPTER_VA

.balign 4
.global rlcd_get_panel_out_device
.hidden rlcd_get_panel_out_device
.thumb_func
rlcd_get_panel_out_device:
    ldr.w pc, [pc, #0]
    .word GET_PANEL_OUT_DEV_VA

.balign 4
.global rlcd_set_ee_fps_no_adapter
.hidden rlcd_set_ee_fps_no_adapter
.thumb_func
rlcd_set_ee_fps_no_adapter:
    ldr.w pc, [pc, #0]
    .word SET_EE_FPS_NO_ADP_VA

.section .note.GNU-stack,"",%progbits
