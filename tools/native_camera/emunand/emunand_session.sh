#!/bin/sh
# Reversible CXD90045 EmuNAND session controller (BusyBox 1.13.4 + 1.34.1 compatible).
# Usage:
#   emunand_session.sh start <BUNDLE_DIR> [--dev /dev/mmca1] [--image CFW_VDISK.IMG] [--sha256 HEX] [--rw]
#   emunand_session.sh status
#   emunand_session.sh stop
set -eu

bb=/bin/busybox
work=/tmp_bt/emunand
mnt="$work/mnt"
loop_node="$work/loop0"
stage_img="$work/vdisk.img"
mounts_log="$work/mounts.state"
extra_log="$work/extra_pkgs.state"

valid_bind_target() {
    case "$1" in
        *..*|*" "*|"") return 1 ;;
        /usr/*|/setting/*|/etc/*|/system|/system/*) return 0 ;;
        *) return 1 ;;
    esac
}

valid_subdir_name() {
    case "$1" in
        *..*|*/*|*" "*|""|"mnt") return 1 ;;
        *) return 0 ;;
    esac
}

do_stop() {
    if [ -f "$mounts_log" ]; then
        # Unmount bind targets in exact reverse order using pure POSIX sh (no tr/wc/sed needed).
        rev_list=""
        while IFS= read -r target || [ -n "${target:-}" ]; do
            [ -n "$target" ] || continue
            rev_list="$target
$rev_list"
        done < "$mounts_log"
        while IFS= read -r target; do
            [ -n "$target" ] || continue
            "$bb" umount "$target" 2>/dev/null || "$bb" umount -l "$target" 2>/dev/null || true
        done <<EOF
$rev_list
EOF
        "$bb" rm -f "$mounts_log"
    fi
    if [ -f "$extra_log" ]; then
        rev_extra=""
        while IFS= read -r row || [ -n "${row:-}" ]; do
            [ -n "$row" ] || continue
            rev_extra="$row
$rev_extra"
        done < "$extra_log"
        while IFS=' ' read -r ex_mnt ex_loop ex_stage; do
            [ -n "${ex_mnt:-}" ] || continue
            if "$bb" grep -q " $ex_mnt " /proc/mounts 2>/dev/null; then
                "$bb" umount "$ex_mnt" 2>/dev/null || "$bb" umount -l "$ex_mnt" 2>/dev/null || true
            fi
            if [ -x "$work/emunand_vdisk_ctl.elf" ]; then
                "$work/emunand_vdisk_ctl.elf" detach --loop "$ex_loop" --stage "$ex_stage" >/dev/null 2>&1 || true
            fi
            "$bb" rm -f "$ex_stage" "$ex_loop"
            "$bb" rmdir "$ex_mnt" 2>/dev/null || true
        done <<EOF
$rev_extra
EOF
        "$bb" rm -f "$extra_log"
    fi
    if "$bb" grep -q " $mnt " /proc/mounts 2>/dev/null; then
        "$bb" umount "$mnt" 2>/dev/null || true
    fi
    if [ -w /proc/emunand_status ]; then
        # Flush dirty CoW buffers while guard is active, then drop CoW + invalidate bdev caches.
        "$bb" sync
        echo clear_cow > /proc/emunand_status 2>/dev/null || true
    fi
    if [ -x "$work/emunand_vdisk_ctl.elf" ]; then
        "$work/emunand_vdisk_ctl.elf" detach --loop "$loop_node" --stage "$stage_img" >/dev/null 2>&1 || true
    fi
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
    "$bb" rm -f "$stage_img" "$loop_node" "$work/emunand_vdisk_ctl.elf"
    "$bb" rmdir "$mnt" 2>/dev/null || true

    if "$bb" grep -q '^emunand_guard ' /proc/modules 2>/dev/null; then
        # Sync and drop clean caches before unloading CoW guard; dirty CoW sectors stay in RAM only.
        "$bb" rmmod emunand_guard
    fi
    if [ -f "$work/backup_save.orig" ] && [ -w /sys/module/backup/parameters/Backup_save ]; then
        "$bb" cat "$work/backup_save.orig" > /sys/module/backup/parameters/Backup_save 2>/dev/null || true
        "$bb" rm -f "$work/backup_save.orig"
    fi
    "$bb" rmdir "$work" 2>/dev/null || true
    echo EMUNAND_STOPPED
}

cmd="${1:-}"
[ -n "$cmd" ] || exit 2
shift

if [ "$cmd" = "status" ]; then
    if [ -r /proc/emunand_status ]; then
        "$bb" cat /proc/emunand_status
    else
        echo "guard_enabled=0"
    fi
    if [ -f "$mounts_log" ]; then
        echo "bind_mounts:"
        "$bb" cat "$mounts_log"
    fi
    exit 0
fi

if [ "$cmd" = "stop" ]; then
    do_stop
    exit 0
fi

if [ "$cmd" != "start" ]; then
    exit 2
fi

bundle="${1:-}"
[ -n "$bundle" ] || exit 2
shift

dev_path="/dev/mmca1"
img_name="CFW_VDISK.IMG"
expected_sha=""
rw_flag=""
extra_pkgs=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dev) dev_path="$2"; shift 2 ;;
        --image) img_name="$2"; shift 2 ;;
        --sha256) expected_sha="$2"; shift 2 ;;
        --rw) rw_flag="--rw"; shift ;;
        --extra-pkg)
            extra_pkgs="$extra_pkgs
$2"
            shift 2 ;;
        *) exit 2 ;;
    esac
done

if [ -e /setting/disable_emunand ]; then
    echo EMUNAND_DISABLED_BY_SETTING
    exit 4
fi

if ! "$bb" grep -q ' /tmp_bt tmpfs rw[, ]' /proc/mounts 2>/dev/null; then
    "$bb" mount -t tmpfs -o size=8192k tmpfs /tmp_bt
fi

"$bb" mkdir -p "$work" "$mnt"
"$bb" cp -f "$bundle/emunand_vdisk_ctl.elf" "$work/emunand_vdisk_ctl.elf"
"$bb" chmod 700 "$work/emunand_vdisk_ctl.elf"

# 1. Load reversible eMMC CoW write-guard FIRST before mounting or binding anything.
if [ -r /sys/module/backup/parameters/Backup_save ] && [ ! -f "$work/backup_save.orig" ]; then
    "$bb" cat /sys/module/backup/parameters/Backup_save > "$work/backup_save.orig"
fi
if ! "$bb" grep -q '^emunand_guard ' /proc/modules 2>/dev/null; then
    "$bb" insmod "$bundle/emunand_guard.ko"
fi
if [ -w /sys/module/backup/parameters/Backup_save ]; then
    echo 0 > /sys/module/backup/parameters/Backup_save
fi
if ! "$bb" grep -q '^guard_enabled=1$' /proc/emunand_status 2>/dev/null; then
    do_stop
    exit 1
fi

# 2. Attach SD VDisk (zero-copy ro on /dev/mmca1 if contiguous, or 8MiB-guarded RAM stage).
set -- attach --dev "$dev_path" --image "$img_name" --loop "$loop_node" --stage "$stage_img"
if [ -n "$expected_sha" ]; then
    set -- "$@" --sha256 "$expected_sha"
fi
if [ -n "$rw_flag" ]; then
    set -- "$@" "$rw_flag"
fi

if ! attach_out=$("$work/emunand_vdisk_ctl.elf" "$@"); then
    do_stop
    exit 3
fi
echo "$attach_out"

fstype="ext2"
case "$attach_out" in
    *fstype=vfat*) fstype="vfat" ;;
    *fstype=cramfs*) fstype="cramfs" ;;
esac
mnt_opts="ro"
if [ -n "$rw_flag" ]; then
    mnt_opts="rw"
fi

if ! "$bb" mount -t "$fstype" -o "$mnt_opts" "$loop_node" "$mnt"; then
    do_stop
    exit 8
fi

: > "$extra_log"
if [ -n "$extra_pkgs" ]; then
    while IFS= read -r spec; do
        [ -n "$spec" ] || continue
        ex_img="${spec%%:*}"
        rest="${spec#*:}"
        ex_sub="${rest%%:*}"
        ex_minor="${rest#*:}"
        if ! valid_subdir_name "$ex_sub"; then
            echo "Refusing unsafe extra-pkg subdir: $ex_sub" >&2
            do_stop
            exit 9
        fi
        case "$ex_minor" in
            1|2|3|4|5|6|7) ;;
            *) echo "Invalid loop minor $ex_minor (use 1..7)" >&2; do_stop; exit 9 ;;
        esac
        ex_mnt="$work/$ex_sub"
        ex_loop="$work/loop$ex_minor"
        ex_stage="$work/stage$ex_minor.img"
        "$bb" mkdir -p "$ex_mnt"
        if ! ex_out=$("$work/emunand_vdisk_ctl.elf" attach --dev "$dev_path" --image "$ex_img" --loop "$ex_loop" --stage "$ex_stage"); then
            do_stop
            exit 3
        fi
        echo "$ex_out"
        ex_fs="ext2"
        case "$ex_out" in
            *fstype=vfat*) ex_fs="vfat" ;;
            *fstype=cramfs*) ex_fs="cramfs" ;;
        esac
        if ! "$bb" mount -t "$ex_fs" -o ro "$ex_loop" "$ex_mnt"; then
            do_stop
            exit 8
        fi
        echo "$ex_mnt $ex_loop $ex_stage" >> "$extra_log"
    done <<EOF
$extra_pkgs
EOF
fi

: > "$mounts_log"
if [ -f "$mnt/emunand.manifest" ]; then
    while IFS=' ' read -r src_rel dst_abs || [ -n "${src_rel:-}" ]; do
        case "${src_rel:-}" in
            ""|\#*) continue ;;
        esac
        if ! valid_bind_target "$dst_abs"; then
            echo "Refusing unsafe bind target: $dst_abs" >&2
            do_stop
            exit 9
        fi
        case "$src_rel" in
            @*/*)
                pkg_tag="${src_rel#@}"
                pkg_sub="${pkg_tag%%/*}"
                pkg_tail="${pkg_tag#*/}"
                if [ "$pkg_tail" = "." ]; then
                    src_full="$work/$pkg_sub"
                else
                    src_full="$work/$pkg_sub/$pkg_tail"
                fi
                ;;
            *)
                src_full="$mnt/$src_rel"
                ;;
        esac
        if [ ! -e "$src_full" ] || [ ! -e "$dst_abs" ]; then
            echo "Missing bind endpoint: $src_full -> $dst_abs" >&2
            do_stop
            exit 9
        fi
        if ! "$bb" mount -o bind "$src_full" "$dst_abs"; then
            do_stop
            exit 9
        fi
        echo "$dst_abs" >> "$mounts_log"
    done < "$mnt/emunand.manifest"
fi

echo EMUNAND_ACTIVE
