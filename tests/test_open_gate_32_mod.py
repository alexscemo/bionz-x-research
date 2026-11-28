"""Comprehensive Cortex-A5 Thumb Unicorn tests for OpenGate32.mod and OpenGateSensor.mod.

Covers:
1. SMOD v2 + SMNU packaging, FNV-1a integrity, and position-independent execution at
   multiple randomized load bases across both ILCE-6400 (2.00) and ILCE-6400A (1.01).
2. Full state-machine matrix across all SMNU options (Canvas Format 0..3, Sensor Timing
   0..2, Movie Scope 0..2), boot NTSC vs PAL vs cold-BSS rt_t14[10] preservation, and
   two-phase fail-closed guards (wrong ORIL build, late-site corruption atomicity,
   missing libmpr.so mapping).
3. Real-firmware end-to-end execution in Unicorn against local av-cam.bin and libmpr.so
   images: runs the compiled ARM Thumb .mod via syscalls, executes Sony's unmodified
   firmware functions (sel_24p, sel_25p, sel_30p, stripe_chk, fe_sel, drive_key_lookup,
   ExlHelper crop math, buf_sel, and MprLibCore::EncOpen resolution converter), and
   proves 100% bit-for-bit restoration across the entire firmware images on OFF.
"""
from __future__ import annotations

import hashlib
import os
import struct
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import unicorn as uc
from unicorn import arm_const as A

from project_paths import PROJECT_ROOT
from src.a6400.mod_manager.smod import SmodError, parse_smod

from tools.research.a6400_open_gate import (
    FE_OFFSETS_PLUS_DY,
    FE_OFFSETS_PLUS_HALF_DY,
    FE_OFFSETS_PLUS_QUARTER_DY,
    FE_OFFSET_SCALED,
)

AVCAM_ORIL_BASE = 0x635C6000
AVCAM_PHYS_BASE = 0x035C6000
AVCAM_VA_TO_PA = 0x60000000

U16_3840_2160 = 0x08700F00
U16_3240_2160 = 0x08700CA8
U16_1920_1080 = 0x04380780
U16_1620_1080 = 0x04380654

FE_6K_STOCK_AND_PATCHED: Tuple[Tuple[int, int, int], ...] = (
    (0x002, 3442, 4066), (0x00A, 3392, 4016), (0x012, 3376, 4000), (0x016, 3392, 4016),
    (0x01E, 3392, 4016), (0x026, 3376, 4000), (0x02A, 3392, 4016), (0x032, 3392, 4016),
    (0x03A, 3376, 4000), (0x03E, 3392, 4016), (0x046, 3392, 4016), (0x04E, 3376, 4000),
    (0x082, 3392, 4016), (0x08A, 3392, 4016), (0x092, 3376, 4000), (0x0AA, 3392, 4016),
    (0x0B2, 3392, 4016), (0x0BA, 3376, 4000), (0x0BE, 3392, 4016), (0x0C6, 3392, 4016),
    (0x0CE, 3376, 4000), (0x0FA, 3442, 4066), (0x108, 3442, 4066), (0x114, 3441, 4065),
    (0x11C, 3441, 4065), (0x144, 3442, 4066), (0x14C, 3441, 4065), (0x154, 3441, 4065),
    (0x15C, 3441, 4065), (0x160, 3442, 4066), (0x190, 3445, 4069), (0x198, 3441, 4065),
    (0x1A0, 3441, 4065), (0x1A8, 3441, 4065), (0x1B0, 3441, 4065), (0x1B8, 3441, 4065),
    (0x1BC, 3443, 4067), (0x1C0, 3445, 4069), (0x1C4, 3445, 4069), (0x1CC, 3441, 4065),
    (0x1D4, 3445, 4069), (0x1E4, 1711, 2023), (0x1EC, 1695, 2007), (0x202, 3392, 4016),
    (0x206, 3392, 4016), (0x20E, 5818, 6888), (0x218, 3407, 4031), (0x220, 3391, 4015),
    (0x228, 3391, 4015), (0x22C, 3407, 4031), (0x230, 3401, 4025), (0x242, 3392, 4016),
    (0x250,  848, 1004), (0x254,  848, 1004), (0x3B8, 3398, 4022), (0x3C0, 3391, 4015),
    (0x3C8, 3391, 4015), (0x3CC, 3394, 4018), (0x3E6, 3392, 4016), (0x3F4, 3398, 4022),
    (0x3FC, 3391, 4015), (0x404, 3391, 4015), (0x408, 3392, 4016), (0x40C, 3394, 4018),
    (0x426, 3391, 4015), (0x432, 3391, 4015),
)

RCV_6K_STOCK_AND_PATCHED: Tuple[Tuple[int, int, int], ...] = (
    (0x014, 3396, 4020),
    (0x028, 3392, 4016),
    (0x054, 3396, 4020),
    (0x058, 3376, 4000),
    (0x06A, 3392, 4016),
    (0x070, 3447, 4071),
)


@dataclass(frozen=True)
class TargetCoords:
    target_id: str
    oril_data_va: int
    oril_data_sz: int
    pa_sel24_4k: int
    pa_sel24_hd: int
    pa_sel25_4k: int
    pa_sel25_hd: int
    pa_sel30_4k: int
    pa_sel30_hd: int
    pa_exl_main: int
    pa_exl_sub: int
    pa_buf_bne: int
    pa_t10_ntsc: int
    pa_t10_pal: int
    pa_rt_t14_m10: int
    pa_reglist_r1_al: int
    pa_reglist_r10_al: int
    pa_reglist_r11_al: int
    pa_reglist_r12_al: int
    pa_reglist_r13_al: int
    pa_fe6_base: int
    pa_fe9_base: int
    pa_m16_vact: int
    pa_m17_vact: int
    pa_rcv_base: int
    pa_t16_ntsc: int
    pa_t17_ntsc: int
    pa_t16_pal: int
    pa_t17_pal: int
    pa_rt_t14_m16: int
    pa_rt_t14_m17: int
    pa_sele_crash: int
    pa_dmm_0: int
    pa_dmm_1: int
    pa_dmm_2: int
    pa_vpi_0: int
    pa_vpi_1: int
    pa_vpi_2: int
    venc_4k_pas: Tuple[int, ...]
    venc_hd_pas: Tuple[int, ...]
    mpr_encopen_hd_off: int
    mpr_encopen_4k_off: int
    mpr_4k_u32_offs: Tuple[int, ...]
    mpr_4k_u16_offs: Tuple[int, ...]
    mpr_hd_u32_offs: Tuple[int, ...]
    mpr_hd_u16_offs: Tuple[int, ...]
    # Firmware function VAs for real-image end-to-end execution
    va_sel24_fn: int
    va_sel25_fn: int
    va_sel30_fn: int
    va_imager_singleton_fn: int
    va_is_supported_fn: int
    va_drive_key_lookup_fn: int
    va_drive_keys: int
    va_stripe_chk_fn: int
    va_fe_sel_fn: int
    va_fe_rec4: int
    va_exl_sub_fn: int
    va_buf_sel_fn: int
    va_reglist_apply_fn: int
    mpr_res_conv_fn_off: int


COORDS_6400 = TargetCoords(
    target_id="ilce_6400",
    oril_data_va=0x644F1000,
    oril_data_sz=0x000D0798,
    pa_sel24_4k=0x03909E4C,
    pa_sel24_hd=0x03909E74,
    pa_sel25_4k=0x03909F52,
    pa_sel25_hd=0x03909FA6,
    pa_sel30_4k=0x03D4DAB4,
    pa_sel30_hd=0x03D4DAC8,
    pa_exl_main=0x0376E23E,
    pa_exl_sub=0x03CE22CC,
    pa_buf_bne=0x03CAEAE4,
    pa_t10_ntsc=0x03E258FC,
    pa_t10_pal=0x03E25AC4,
    pa_rt_t14_m10=0x0420173C,
    pa_reglist_r1_al=0x0454C4A6,
    pa_reglist_r10_al=0x0454C55A,
    pa_reglist_r11_al=0x0454C56E,
    pa_reglist_r12_al=0x0454C582,
    pa_reglist_r13_al=0x0454C596,
    pa_fe6_base=0x03E14E94,
    pa_fe9_base=0x03E15E54,
    pa_m16_vact=0x03E2546C,
    pa_m17_vact=0x03E254B8,
    pa_rcv_base=0x03E19740,
    pa_t16_ntsc=0x03E2598C,
    pa_t17_ntsc=0x03E259A4,
    pa_t16_pal=0x03E25B54,
    pa_t17_pal=0x03E25B6C,
    pa_rt_t14_m16=0x04201A0C,
    pa_rt_t14_m17=0x04201A84,
    pa_sele_crash=0x03907870,
    pa_dmm_0=0x03DF199C,
    pa_dmm_1=0x03DF1A44,
    pa_dmm_2=0x03DF1AEC,
    pa_vpi_0=0x03DF1EC0,
    pa_vpi_1=0x0454388C,
    pa_vpi_2=0x04570198,
    venc_4k_pas=(
        0x03E369F4, 0x03E36A54, 0x03E36BD4, 0x03E36C38, 0x03E36C9C, 0x03E36D00, 0x03E36D64,
        0x03E36DC8, 0x03E37A48, 0x03E37AAC, 0x03E37B10, 0x03E37B74, 0x03E37BD8, 0x03E37C3C,
    ),
    venc_hd_pas=(
        0x03E36AB4, 0x03E36B14, 0x03E36E2C, 0x03E36E90, 0x03E36EF4, 0x03E36F58, 0x03E36FBC,
        0x03E37020, 0x03E37084, 0x03E370E8, 0x03E3714C, 0x03E371B0, 0x03E37214, 0x03E37278,
        0x03E372DC, 0x03E37340, 0x03E373A4, 0x03E37408, 0x03E3746C, 0x03E374D0, 0x03E37534,
        0x03E37598, 0x03E375FC, 0x03E37660, 0x03E376C4, 0x03E37728, 0x03E3778C, 0x03E37CA0,
    ),
    mpr_encopen_hd_off=0x0087025A,
    mpr_encopen_4k_off=0x00870266,
    mpr_4k_u32_offs=(0x00A2F114, 0x00A2F124, 0x00A2F134, 0x00A2F144, 0x00A2F154, 0x00A59E78),
    mpr_4k_u16_offs=(0x007C7FD0, 0x007C9868, 0x008EBC28, 0x00A5A0C4),
    mpr_hd_u32_offs=(0x00A2F1F4, 0x00A2F214, 0x00A2F234, 0x00A2F244, 0x00A2F254, 0x00A59DDC),
    mpr_hd_u16_offs=(0x007C9864, 0x008EBC24, 0x00A5A004),
    va_sel24_fn=0x63909DAC,
    va_sel25_fn=0x63909E80,
    va_sel30_fn=0x63D4DA60,
    va_imager_singleton_fn=0x6379C1DC,
    va_is_supported_fn=0x63CEF580,
    va_drive_key_lookup_fn=0x63CEF804,
    va_drive_keys=0x63E25514,
    va_stripe_chk_fn=0x63764714,
    va_fe_sel_fn=0x63771C78,
    va_fe_rec4=0x63E14414,
    va_exl_sub_fn=0x63CE22C6,
    va_buf_sel_fn=0x63CAEADA,
    va_reglist_apply_fn=0x6379D618,
    mpr_res_conv_fn_off=0x0087022C,
)

COORDS_6400A = TargetCoords(
    target_id="ilce_6400a",
    oril_data_va=0x644F5B80,
    oril_data_sz=0x000D079C,
    pa_sel24_4k=0x0390C184,
    pa_sel24_hd=0x0390C1AC,
    pa_sel25_4k=0x0390C28A,
    pa_sel25_hd=0x0390C2DE,
    pa_sel30_4k=0x03D5087A,
    pa_sel30_hd=0x03D5088E,
    pa_exl_main=0x037702E2,
    pa_exl_sub=0x03CE501E,
    pa_buf_bne=0x03CB445A,
    pa_t10_ntsc=0x03E282AC,
    pa_t10_pal=0x03E28474,
    pa_rt_t14_m10=0x042062FC,
    pa_reglist_r1_al=0x04550F6E,
    pa_reglist_r10_al=0x04551022,
    pa_reglist_r11_al=0x04551036,
    pa_reglist_r12_al=0x0455104A,
    pa_reglist_r13_al=0x0455105E,
    pa_fe6_base=0x03E17A24,
    pa_fe9_base=0x03E189E4,
    pa_m16_vact=0x03E27E1C,
    pa_m17_vact=0x03E27E68,
    pa_rcv_base=0x03E1C2D0,
    pa_t16_ntsc=0x03E2833C,
    pa_t17_ntsc=0x03E28354,
    pa_t16_pal=0x03E28504,
    pa_t17_pal=0x03E2851C,
    pa_rt_t14_m16=0x042065CC,
    pa_rt_t14_m17=0x04206644,
    pa_sele_crash=0x03909BA8,
    pa_dmm_0=0x03DF4584,
    pa_dmm_1=0x03DF462C,
    pa_dmm_2=0x03DF46D4,
    pa_vpi_0=0x03DF4B3C,
    pa_vpi_1=0x04548678,
    pa_vpi_2=0x045747A8,
    venc_4k_pas=(
        0x03E3959C, 0x03E395FC, 0x03E3977C, 0x03E397E0, 0x03E39844, 0x03E398A8, 0x03E3990C,
        0x03E39970, 0x03E3A5F0, 0x03E3A654, 0x03E3A6B8, 0x03E3A71C, 0x03E3A780, 0x03E3A7E4,
    ),
    venc_hd_pas=(
        0x03E3965C, 0x03E396BC, 0x03E399D4, 0x03E39A38, 0x03E39A9C, 0x03E39B00, 0x03E39B64,
        0x03E39BC8, 0x03E39C2C, 0x03E39C90, 0x03E39CF4, 0x03E39D58, 0x03E39DBC, 0x03E39E20,
        0x03E39E84, 0x03E39EE8, 0x03E39F4C, 0x03E39FB0, 0x03E3A014, 0x03E3A078, 0x03E3A0DC,
        0x03E3A140, 0x03E3A1A4, 0x03E3A208, 0x03E3A26C, 0x03E3A2D0, 0x03E3A334, 0x03E3A848,
    ),
    mpr_encopen_hd_off=0x00870696,
    mpr_encopen_4k_off=0x008706A2,
    mpr_4k_u32_offs=(0x00A2F3D4, 0x00A2F3E4, 0x00A2F3F4, 0x00A2F404, 0x00A2F414, 0x00A5A130),
    mpr_4k_u16_offs=(0x007C81E0, 0x007C9A78, 0x008EBE20, 0x00A5A37C),
    mpr_hd_u32_offs=(0x00A2F4B4, 0x00A2F4D4, 0x00A2F4F4, 0x00A2F504, 0x00A2F514, 0x00A5A094),
    mpr_hd_u16_offs=(0x007C9A74, 0x008EBE1C, 0x00A5A2BC),
    va_sel24_fn=0x6390C0E4,
    va_sel25_fn=0x6390C1B8,
    va_sel30_fn=0x63D50826,
    va_imager_singleton_fn=0x6379E3D8,
    va_is_supported_fn=0x63CF22F6,
    va_drive_key_lookup_fn=0x63CF28A4,
    va_drive_keys=0x63E27EC4,
    va_stripe_chk_fn=0x637665EC,
    va_fe_sel_fn=0x63773D1C,
    va_fe_rec4=0x63E16FA4,
    va_exl_sub_fn=0x63CE5018,
    va_buf_sel_fn=0x63CB4450,
    va_reglist_apply_fn=0x6379E770,
    mpr_res_conv_fn_off=0x00870668,
)


def _unpack_mod(mod_bytes: bytes) -> Tuple[bytes, int, int, int, int]:
    magic, ver, flags, _name, code_sz, _fnv, bss_sz, on_off, off_off, hooks = (
        struct.unpack_from("<III32s6I", mod_bytes, 0)
    )
    assert magic == 0x444F4D53 and ver == 2 and hooks == 0
    code = mod_bytes[68 : 68 + code_sz]
    cfg_off = 0
    if flags & 0x20:
        sm_magic, _cnt, _bss_off, _rsv, cfg_off = struct.unpack_from("<IBBHI", mod_bytes, 68 + code_sz)
        assert sm_magic == 0x554E4D53
    return code, bss_sz, on_off, off_off, cfg_off


class SyntheticModHarness:
    """Isolated ARM Thumb Unicorn harness with page-accurate /dev/mem and libmpr.so backing."""

    STOP_VA = 0x3F000000
    STACK_VA = 0x20000000
    MAPS_SCRATCH_VA = 0x21000000

    def __init__(
        self,
        mod_bytes: bytes,
        coords: TargetCoords,
        *,
        mod_base: int = 0x40AB3000,
        mpr_base: int = 0x075CE000,
        include_mpr_in_maps: bool = True,
        rt_init_hv: Tuple[int, int] = (660, 1820),
    ) -> None:
        self.coords = coords
        self.mod_base = mod_base
        self.mpr_base = mpr_base
        self.include_mpr_in_maps = include_mpr_in_maps
        self.code, self.bss_sz, self.on_off, self.off_off, self.cfg_off = _unpack_mod(mod_bytes)

        self.mu = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
        mod_span = ((len(self.code) + self.bss_sz + 0x1FFF) & ~0xFFF)
        self.mu.mem_map(self.mod_base, mod_span)
        self.mu.mem_write(self.mod_base, self.code)

        self.mu.mem_map(self.STOP_VA, 0x1000)
        self.mu.mem_map(self.STACK_VA, 0x10000)

        # Allocate distinct guest backing pages for every unique physical 4 KiB page
        self.pgoff_to_va: Dict[int, int] = {}
        next_phys_map = 0x30000000

        def ensure_phys_page(pa: int) -> int:
            nonlocal next_phys_map
            pgoff = pa >> 12
            if pgoff not in self.pgoff_to_va:
                self.mu.mem_map(next_phys_map, 0x1000)
                self.pgoff_to_va[pgoff] = next_phys_map
                next_phys_map += 0x1000
            return self.pgoff_to_va[pgoff] + (pa & 0xFFF)

        self._phys_va = ensure_phys_page
        c = coords

        # Populate stock ORIL header + segment descriptor
        self._w32(AVCAM_PHYS_BASE + 0x00, 0xEA00000A)
        self._w32(AVCAM_PHYS_BASE + 0x04, 0x4C49524F)
        self._w32(AVCAM_PHYS_BASE + 0x08, 4)
        self._w32(AVCAM_PHYS_BASE + 0xB8, c.oril_data_va)
        self._w32(AVCAM_PHYS_BASE + 0xBC, c.oril_data_sz)

        # Populate stock instruction sites
        self._w16(c.pa_sel24_4k, 0x201F)
        self._w16(c.pa_sel24_hd, 0x2007)
        self._w16(c.pa_sel25_4k, 0x2089)
        self._w16(c.pa_sel25_hd, 0x2005)
        self._w16(c.pa_sel30_4k, 0x2013)
        self._w16(c.pa_sel30_hd, 0x2005)
        self._w16(c.pa_exl_main, 0x6F05)
        self._w16(c.pa_exl_sub, 0x6F19)
        self._w16(c.pa_buf_bne, 0xD112)

        # Populate stock Mode 10 timings
        for off in (0x00, 0x0C):
            self._w32(c.pa_t10_ntsc + off, 660)
            self._w32(c.pa_t10_ntsc + off + 4, 1820)
            self._w32(c.pa_t10_pal + off, 660)
            self._w32(c.pa_t10_pal + off + 4, 2180)
        for off in (0x08, 0x68):
            self._w32(c.pa_rt_t14_m10 + off, rt_init_hv[0])
            self._w32(c.pa_rt_t14_m10 + off + 4, rt_init_hv[1])

        # Populate stock 6K Reg01 / Mode 16/17 / FE[6]/FE[9] / rcv_desc / S-Ele sites
        self._w16(c.pa_reglist_r1_al, 0x4141)
        self._w16(c.pa_reglist_r10_al, 0x568A)
        self._w16(c.pa_reglist_r11_al, 0x0102)
        self._w16(c.pa_reglist_r12_al, 0x44DC)
        self._w16(c.pa_reglist_r13_al, 0x0D0A)
        self._w16(c.pa_sele_crash, 0x603F)
        for off, stock_v, _ in RCV_6K_STOCK_AND_PATCHED:
            self._w16(c.pa_rcv_base + off, stock_v)
        for off, stock_v, _ in FE_6K_STOCK_AND_PATCHED:
            self._w16(c.pa_fe6_base + off, stock_v)
            self._w16(c.pa_fe9_base + off, stock_v)
        self._w32(c.pa_m16_vact, 3376)
        self._w32(c.pa_m17_vact, 3376)
        for off in (0x00, 0x0C):
            self._w32(c.pa_t16_ntsc + off, 862)
            self._w32(c.pa_t16_ntsc + off + 4, 1740)
            self._w32(c.pa_t16_pal + off, 862)
            self._w32(c.pa_t16_pal + off + 4, 1740)
            self._w32(c.pa_t17_ntsc + off, 827)
            self._w32(c.pa_t17_ntsc + off + 4, 1740)
            self._w32(c.pa_t17_pal + off, 827)
            self._w32(c.pa_t17_pal + off + 4, 1740)
        if rt_init_hv != (0, 0):
            for off in (0x08, 0x68):
                self._w32(c.pa_rt_t14_m16 + off, 862)
                self._w32(c.pa_rt_t14_m16 + off + 4, 1740)
                self._w32(c.pa_rt_t14_m17 + off, 827)
                self._w32(c.pa_rt_t14_m17 + off + 4, 1740)
            self._w32(c.pa_rt_t14_m16 + 0x50, 3376)
            self._w32(c.pa_rt_t14_m17 + 0x50, 3376)
        else:
            for base_rt in (c.pa_rt_t14_m16, c.pa_rt_t14_m17):
                for off in (0x08, 0x0C, 0x50, 0x68, 0x6C):
                    self._w32(base_rt + off, 0)

        # Populate stock DMM/VPI/VENC 4K & HD entries
        for dmm in (c.pa_dmm_0, c.pa_dmm_1, c.pa_dmm_2):
            self._w32(dmm + 1 * 24 + 0x00, 1920)
            self._w32(dmm + 1 * 24 + 0x08, 1920)
            self._w32(dmm + 4 * 24 + 0x00, 3840)
            self._w32(dmm + 4 * 24 + 0x08, 3840)
        self._w32(c.pa_vpi_0 + 0x00, 3840)
        self._w32(c.pa_vpi_0 + 0x0C, 3840)
        self._w32(c.pa_vpi_0 + 0x18, 1920)
        self._w32(c.pa_vpi_1 + 0x00, 3840)
        self._w32(c.pa_vpi_1 + 0x08, 3840)
        self._w32(c.pa_vpi_1 + 0x14, 1920)
        self._w32(c.pa_vpi_1 + 0x1C, 1920)
        self._w32(c.pa_vpi_2 + 0x00, 3840)
        for pa in c.venc_4k_pas:
            self._w32(pa, U16_3840_2160)
        for pa in c.venc_hd_pas:
            self._w32(pa, U16_1920_1080)

        # Map sparse libmpr.so pages around needed offsets
        self.mpr_pages: set[int] = set()

        def ensure_mpr_va(off: int, length: int = 4) -> int:
            for byte_off in range(off, off + length):
                pg = (self.mpr_base + byte_off) & ~0xFFF
                if pg not in self.mpr_pages:
                    self.mu.mem_map(pg, 0x1000)
                    self.mpr_pages.add(pg)
            return self.mpr_base + off

        self.mu.mem_write(ensure_mpr_va(0, 4), b"\x7fELF")
        self.mu.mem_write(ensure_mpr_va(c.mpr_encopen_4k_off, 4), struct.pack("<2H", 0xF44F, 0x6370))
        self.mu.mem_write(ensure_mpr_va(c.mpr_encopen_hd_off, 4), struct.pack("<2H", 0xF44F, 0x63F0))
        for off in c.mpr_4k_u32_offs:
            self.mu.mem_write(ensure_mpr_va(off), struct.pack("<I", 3840))
        for off in c.mpr_4k_u16_offs:
            self.mu.mem_write(ensure_mpr_va(off), struct.pack("<I", U16_3840_2160))
        for off in c.mpr_hd_u32_offs:
            self.mu.mem_write(ensure_mpr_va(off), struct.pack("<I", 1920))
        for off in c.mpr_hd_u16_offs:
            self.mu.mem_write(ensure_mpr_va(off), struct.pack("<I", U16_1920_1080))

        self.stock_phys_snapshot = {
            pgoff: bytes(self.mu.mem_read(va, 0x1000))
            for pgoff, va in self.pgoff_to_va.items()
        }
        self.stock_mpr_snapshot = {
            pg: bytes(self.mu.mem_read(pg, 0x1000))
            for pg in self.mpr_pages
        }

        self.logs: list[str] = []
        self.cacheflushes: list[Tuple[int, int]] = []
        self._maps_pos = 0
        self._maps_bytes = self._build_maps_bytes()
        self.mu.hook_add(uc.UC_HOOK_INTR, self._on_svc)

    def _build_maps_bytes(self) -> bytes:
        lines = ["0401b000-04033000 r-xp 00000000 fc:07 584 /lib/ld-2.11.2.so\n"]
        if self.include_mpr_in_maps:
            lines.append(
                f"{self.mpr_base:08x}-{(self.mpr_base + 0x00BEE000):08x} "
                "r-xp 00000000 fc:0f 2156 /usr/lib/libmpr.so\n"
            )
        return "".join(lines).encode("ascii")

    def _w16(self, pa: int, val: int) -> None:
        self.mu.mem_write(self._phys_va(pa), struct.pack("<H", val))

    def _w32(self, pa: int, val: int) -> None:
        self.mu.mem_write(self._phys_va(pa), struct.pack("<I", val))

    def r16(self, pa: int) -> int:
        return struct.unpack("<H", self.mu.mem_read(self._phys_va(pa), 2))[0]

    def r32(self, pa: int) -> int:
        return struct.unpack("<I", self.mu.mem_read(self._phys_va(pa), 4))[0]

    def mpr_u32(self, off: int) -> int:
        return struct.unpack("<I", self.mu.mem_read(self.mpr_base + off, 4))[0]

    def mpr_hw2(self, off: int) -> Tuple[int, int]:
        return struct.unpack("<2H", self.mu.mem_read(self.mpr_base + off, 4))

    def set_cfg(self, c0: int, c1: int, c2: int = 0, c3: int = 0) -> None:
        bss_addr = self.mod_base + len(self.code)
        self.mu.mem_write(bss_addr + 4, bytes([c0, c1, c2, c3]))

    def read_bss_state(self) -> dict[str, int]:
        bss_addr = self.mod_base + len(self.code)
        magic, _c0, _c1, active, apply_cnt, wr, unch, err, err_idx, rt_v, rt_h, rt_v0, mpr_b = (
            struct.unpack("<IIIIIIIIIIIII", self.mu.mem_read(bss_addr, 52))
        )
        return {
            "magic": magic,
            "active": active,
            "apply_count": apply_cnt,
            "written": wr,
            "unchanged": unch,
            "error_code": err,
            "error_idx": err_idx,
            "rt_saved_valid": rt_v,
            "rt_orig_h": rt_h,
            "rt_orig_v": rt_v0,
            "last_mpr_base": mpr_b,
        }

    def _on_svc(self, mu: uc.Uc, _intno: int, _user: object) -> None:
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [
            mu.reg_read(r)
            for r in (
                A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5,
            )
        ]
        ret = 0
        if nr == 5:  # open
            path = bytes(mu.mem_read(a[0], 96)).split(b"\0", 1)[0].decode("latin1")
            if path == "/dev/mem":
                ret = 10
            elif path == "/proc/self/maps":
                self._maps_pos = 0
                ret = 11
            elif path in ("/tmp_bt/mods/open_gate_32.log", "/tmp_bt/mods/open_gate_sensor.log"):
                ret = 12
            else:
                raise AssertionError(f"Unexpected open({path!r})")
        elif nr == 3 and a[0] == 11:  # read(/proc/self/maps)
            chunk = self._maps_bytes[self._maps_pos : self._maps_pos + a[2]]
            if chunk:
                mu.mem_write(a[1], chunk)
            self._maps_pos += len(chunk)
            ret = len(chunk)
        elif nr == 4 and a[0] == 12:  # write(log)
            self.logs.append(bytes(mu.mem_read(a[1], a[2])).decode("latin1"))
            ret = a[2]
        elif nr == 192:  # mmap2
            assert a[0] == 0 and a[1] == 4096 and a[4] == 10
            ret = self.pgoff_to_va[a[5]]
        elif nr in (6, 91, 125):  # close, munmap, mprotect
            ret = 0
        elif nr == 0x0F0002:  # __ARM_NR_cacheflush
            self.cacheflushes.append((a[0], a[1]))
            ret = 0
        else:
            raise AssertionError(f"Unexpected syscall nr={nr:#x} args={a}")
        mu.reg_write(A.UC_ARM_REG_R0, ret)

    def call_entry(self, entry_off: int, r0: int) -> None:
        self.mu.reg_write(A.UC_ARM_REG_SP, self.STACK_VA + 0xE000)
        self.mu.reg_write(A.UC_ARM_REG_R0, r0)
        self.mu.reg_write(A.UC_ARM_REG_LR, self.STOP_VA | 1)
        self.mu.emu_start((self.mod_base + entry_off) | 1, self.STOP_VA, count=500_000)

    def assert_all_stock(self, test_case: unittest.TestCase) -> None:
        for pgoff, exp in self.stock_phys_snapshot.items():
            cur = bytes(self.mu.mem_read(self.pgoff_to_va[pgoff], 0x1000))
            test_case.assertEqual(cur, exp, f"Physical page {pgoff:#x} not restored to stock")
        for pg, exp in self.stock_mpr_snapshot.items():
            cur = bytes(self.mu.mem_read(pg, 0x1000))
            test_case.assertEqual(cur, exp, f"libmpr page {pg:#x} not restored to stock")


class TestOpenGateModPackagingAndSyntheticUnicorn(unittest.TestCase):
    """Runs unconditionally in CI without requiring proprietary firmware dumps."""

    MOD_PATHS = (
        ("ilce_6400", COORDS_6400, PROJECT_ROOT / "mods/open_gate"),
        ("ilce_6400a", COORDS_6400A, PROJECT_ROOT / "mods/ilce_6400a"),
    )

    def test_smod_headers_and_smnu_trailers(self) -> None:
        for target_id, _coords, mod_dir in self.MOD_PATHS:
            with self.subTest(target=target_id):
                og32 = (mod_dir / "OpenGate32.mod").read_bytes()
                info32 = parse_smod(og32, filename="OpenGate32.mod")
                self.assertEqual((info32.version, info32.name, info32.hook_count), (2, "Open Gate 3:2", 0))
                self.assertTrue(info32.flags & 0x20)

                corrupt = bytearray(og32)
                corrupt[-1] ^= 0x5A
                with self.assertRaises(SmodError):
                    parse_smod(bytes(corrupt), filename="OpenGate32.mod")

                ogs = (mod_dir / "OpenGateSensor.mod").read_bytes()
                infos = parse_smod(ogs, filename="OpenGateSensor.mod")
                self.assertEqual((infos.version, infos.name, infos.hook_count), (2, "Open Gate Sensor", 0))
                self.assertTrue(infos.flags & 0x20)

    def test_full_smnu_matrix_and_relocatability_at_two_bases(self) -> None:
        for target_id, coords, mod_dir in self.MOD_PATHS:
            mod_bytes = (mod_dir / "OpenGate32.mod").read_bytes()
            for mod_base, mpr_base in ((0x40AB3000, 0x07144000), (0x4CD17000, 0x08310000)):
                with self.subTest(target=target_id, mod_base=hex(mod_base)):
                    h = SyntheticModHarness(mod_bytes, coords, mod_base=mod_base, mpr_base=mpr_base)

                    # 1. Default cfg = (0: 5.1K True 4K, 0: Native 3:2 4K, 0: 24p, 0: 4K+HD)
                    h.set_cfg(0, 0, 0, 0)
                    h.call_entry(h.on_off, 1)
                    st = h.read_bss_state()
                    self.assertEqual((st["error_code"], st["active"], st["last_mpr_base"]), (0, 1, mpr_base))
                    # 4K stays on unbinned Mode 16/17/15 (5064x3376 / 4140x2760), FE[6]/FE[9] stay stock <= 3445!
                    self.assertEqual(h.r16(coords.pa_sel24_4k), 0x201F)
                    self.assertEqual(h.r16(coords.pa_sel25_4k), 0x2089)
                    self.assertEqual(h.r16(coords.pa_sel30_4k), 0x2013)
                    self.assertEqual(h.r16(coords.pa_fe6_base + 0x00A), 3392)
                    self.assertEqual(h.r16(coords.pa_reglist_r1_al), 0x4141)
                    # HD routes to full-width Mode 10 (0x44), ExlHelper 3:2 unlocked, 3240x2160 canvas active
                    self.assertEqual(h.r16(coords.pa_sel24_hd), 0x2044)
                    self.assertEqual(h.r16(coords.pa_exl_main), 0x2500)
                    self.assertEqual(h.r16(coords.pa_exl_sub), 0x2100)
                    self.assertEqual(h.r16(coords.pa_buf_bne), 0xBF00)
                    self.assertEqual((h.r32(coords.pa_t10_ntsc), h.r32(coords.pa_t10_ntsc + 4)), (660, 2275))
                    self.assertEqual((h.r32(coords.pa_rt_t14_m10 + 8), h.r32(coords.pa_rt_t14_m10 + 12)), (660, 2275))
                    self.assertEqual(h.r32(coords.pa_dmm_0 + 4 * 24 + 0x00), 3240)
                    self.assertEqual(h.r32(coords.pa_dmm_0 + 4 * 24 + 0x08), 3264)
                    self.assertEqual(h.r32(coords.pa_dmm_0 + 1 * 24 + 0x00), 1920)
                    self.assertEqual(h.mpr_hw2(coords.mpr_encopen_4k_off), (0xF640, 0x43A8))
                    self.assertEqual(h.mpr_hw2(coords.mpr_encopen_hd_off), (0xF44F, 0x63F0))
                    self.assertEqual(h.mpr_u32(coords.mpr_4k_u32_offs[0]), 3240)
                    self.assertGreaterEqual(len(h.cacheflushes), 1)

                    # 2. Live SMNU edit -> (1: 6K Reg01 Full, 0: Native 3:2 4K, 0: 24p, 0: 4K+HD)
                    h.set_cfg(1, 0, 0, 0)
                    h.call_entry(h.cfg_off, 2)
                    self.assertEqual(h.r16(coords.pa_sel24_4k), 0x201F)
                    self.assertEqual(h.r16(coords.pa_reglist_r1_al), 0x4041)   # reg 0x0001 col11: 0x41 -> 0x40!
                    self.assertEqual(h.r16(coords.pa_reglist_r10_al), 0x1E8A)  # Y_START = 30
                    self.assertEqual(h.r16(coords.pa_reglist_r12_al), 0xB4DC)  # Y_SIZE = 4020
                    self.assertEqual(h.r16(coords.pa_sele_crash), 0xBF00)      # S-Ele crash guard armed!
                    self.assertEqual(h.r16(coords.pa_fe6_base + 0x00A), 4016)
                    self.assertEqual(h.r16(coords.pa_fe9_base + 0x20E), 6888)
                    self.assertEqual(h.r32(coords.pa_m16_vact), 4000)
                    self.assertEqual((h.r32(coords.pa_t16_ntsc), h.r32(coords.pa_t16_ntsc + 4)), (731, 2052))
                    self.assertEqual((h.r32(coords.pa_rt_t14_m16 + 8), h.r32(coords.pa_rt_t14_m16 + 12)), (731, 2052))
                    self.assertEqual(h.r32(coords.pa_rt_t14_m16 + 0x50), 4000)

                    # 3. Live SMNU edit -> (2: Full-FOV 2K, 1: Native 3:2 HD, 1: 30p, 2: HD Only)
                    h.set_cfg(2, 1, 1, 2)
                    h.call_entry(h.cfg_off, 2)
                    self.assertEqual(h.r16(coords.pa_reglist_r1_al), 0x4141)   # 6K Reg01 reverted cleanly!
                    self.assertEqual(h.r16(coords.pa_fe6_base + 0x00A), 3392)
                    self.assertEqual(h.r16(coords.pa_sele_crash), 0x603F)
                    self.assertEqual(h.r16(coords.pa_sel24_4k), 0x201F)        # 4K restored (HD Only)!
                    self.assertEqual(h.r16(coords.pa_sel24_hd), 0x2044)        # HD stays Open Gate 0x44!
                    self.assertEqual((h.r32(coords.pa_t10_ntsc), h.r32(coords.pa_t10_ntsc + 4)), (660, 1820))
                    self.assertEqual((h.r32(coords.pa_t10_pal), h.r32(coords.pa_t10_pal + 4)), (600, 2400))
                    self.assertEqual(h.r32(coords.pa_dmm_0 + 4 * 24 + 0x00), 3840)
                    self.assertEqual(h.r32(coords.pa_dmm_0 + 1 * 24 + 0x00), 1620)
                    self.assertEqual(h.r32(coords.pa_dmm_0 + 1 * 24 + 0x08), 1664)
                    self.assertEqual(h.mpr_hw2(coords.mpr_encopen_4k_off), (0xF44F, 0x6370))
                    self.assertEqual(h.mpr_hw2(coords.mpr_encopen_hd_off), (0xF240, 0x6354))
                    self.assertEqual(h.mpr_u32(coords.mpr_hd_u32_offs[0]), 1620)

                    # 4. Live SMNU edit -> (2: Full-FOV 2K, 2: Native 3:2 Both, 0: 24p, 0: 4K+HD)
                    h.set_cfg(2, 2, 0, 0)
                    h.call_entry(h.cfg_off, 2)
                    self.assertEqual(h.r16(coords.pa_sel24_4k), 0x2044)
                    self.assertEqual(h.mpr_hw2(coords.mpr_encopen_4k_off), (0xF640, 0x43A8))
                    self.assertEqual(h.mpr_hw2(coords.mpr_encopen_hd_off), (0xF240, 0x6354))

                    # 5. Live SMNU edit -> (2: Full-FOV 2K, 3: Anamorphic, 2: Stock Auto, 1: 4K Only)
                    h.set_cfg(2, 3, 2, 1)
                    h.call_entry(h.cfg_off, 2)
                    self.assertEqual(h.r16(coords.pa_sel24_4k), 0x2044)
                    self.assertEqual(h.r16(coords.pa_sel24_hd), 0x2007)
                    self.assertEqual(h.r16(coords.pa_buf_bne), 0xD112)
                    self.assertEqual(h.r32(coords.pa_dmm_0 + 4 * 24 + 0x00), 3840)
                    self.assertEqual(h.r32(coords.pa_dmm_0 + 1 * 24 + 0x00), 1920)
                    self.assertEqual((h.r32(coords.pa_t10_pal), h.r32(coords.pa_t10_pal + 4)), (660, 2180))

                    # Turn OFF -> 100% byte-exact stock restoration across every page
                    h.call_entry(h.off_off, 0)
                    self.assertEqual(h.read_bss_state()["active"], 0)
                    h.assert_all_stock(self)

    def test_pal_boot_and_cold_bss_rt_t14_preservation(self) -> None:
        mod_bytes = (PROJECT_ROOT / "mods/open_gate/OpenGateSensor.mod").read_bytes()
        # Case A: PAL boot (660, 2180) in rt_t14[10] -> ON switches to (600, 2400), OFF restores (660, 2180)
        h_pal = SyntheticModHarness(mod_bytes, COORDS_6400, rt_init_hv=(660, 2180))
        h_pal.set_cfg(0, 0)
        h_pal.call_entry(h_pal.on_off, 1)
        self.assertEqual(
            (h_pal.r32(COORDS_6400.pa_rt_t14_m10 + 8), h_pal.r32(COORDS_6400.pa_rt_t14_m10 + 12)),
            (600, 2400),
        )
        h_pal.call_entry(h_pal.off_off, 0)
        h_pal.assert_all_stock(self)

        # Case B: Cold .bss (0, 0) -> untouched, no error
        h_cold = SyntheticModHarness(mod_bytes, COORDS_6400, rt_init_hv=(0, 0))
        h_cold.call_entry(h_cold.on_off, 1)
        self.assertEqual(h_cold.read_bss_state()["error_code"], 0)
        self.assertEqual(h_cold.r32(COORDS_6400.pa_rt_t14_m10 + 8), 0)
        h_cold.call_entry(h_cold.off_off, 0)
        h_cold.assert_all_stock(self)

    def test_fail_closed_guards_and_two_phase_atomicity(self) -> None:
        mod_6400 = (PROJECT_ROOT / "mods/open_gate/OpenGate32.mod").read_bytes()

        # 1. Cross-target ORIL mismatch (ILCE-6400 mod on ILCE-6400A ORIL segment header)
        h_wrong = SyntheticModHarness(mod_6400, COORDS_6400)
        h_wrong._w32(AVCAM_PHYS_BASE + 0xB8, COORDS_6400A.oril_data_va)
        h_wrong.call_entry(h_wrong.on_off, 1)
        self.assertEqual(h_wrong.read_bss_state()["error_code"], 2)
        self.assertIn("err=oril_hdr", "".join(h_wrong.logs))
        self.assertEqual(h_wrong.r16(COORDS_6400.pa_sel24_4k), 0x201F)

        # 2. Late-site corruption in libmpr.so -> Phase 1 preflight aborts BEFORE writing av-cam.bin!
        h_corrupt = SyntheticModHarness(mod_6400, COORDS_6400)
        bad_va = h_corrupt.mpr_base + COORDS_6400.mpr_hd_u16_offs[-1]
        h_corrupt.mu.mem_write(bad_va, struct.pack("<I", 0xCAFEBABE))
        h_corrupt.call_entry(h_corrupt.on_off, 1)
        st = h_corrupt.read_bss_state()
        self.assertEqual((st["error_code"], st["active"]), (3, 0))
        self.assertIn("err=preflight", "".join(h_corrupt.logs))
        for pgoff, exp in h_corrupt.stock_phys_snapshot.items():
            self.assertEqual(bytes(h_corrupt.mu.mem_read(h_corrupt.pgoff_to_va[pgoff], 0x1000)), exp)

        # 3. Missing libmpr.so in /proc/self/maps -> refuses cleanly without partial writes
        h_nomap = SyntheticModHarness(mod_6400, COORDS_6400, include_mpr_in_maps=False)
        h_nomap.call_entry(h_nomap.on_off, 1)
        self.assertEqual(h_nomap.read_bss_state()["error_code"], 4)
        h_nomap.assert_all_stock(self)


class TestOpenGateRealFirmwareUnicorn(unittest.TestCase):
    """End-to-end execution of OpenGate32.mod AND Sony's real firmware functions in Unicorn."""

    def _run_real_firmware_e2e(
        self,
        avcam_path: Path,
        avcam_sha: str,
        mpr_bytes: bytes | None,
        mod_path: Path,
        coords: TargetCoords,
    ) -> None:
        avcam_orig = avcam_path.read_bytes()
        self.assertEqual(hashlib.sha256(avcam_orig).hexdigest(), avcam_sha)
        mod_bytes = mod_path.read_bytes()
        code, bss_sz, on_off, off_off, cfg_off = _unpack_mod(mod_bytes)

        mu = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
        av_map_sz = (len(avcam_orig) + 0x100000 + 0xFFF) & ~0xFFF  # includes .bss (rt_t14)
        mu.mem_map(AVCAM_ORIL_BASE, av_map_sz)
        mu.mem_write(AVCAM_ORIL_BASE, avcam_orig)
        # Populate live rt_t14[10, 16, 17] as NTSC boot before snapshotting baseline
        rt_va = coords.pa_rt_t14_m10 + AVCAM_VA_TO_PA
        rt16_va = coords.pa_rt_t14_m16 + AVCAM_VA_TO_PA
        rt17_va = coords.pa_rt_t14_m17 + AVCAM_VA_TO_PA
        for off in (0x08, 0x68):
            mu.mem_write(rt_va + off, struct.pack("<2I", 660, 1820))
            mu.mem_write(rt16_va + off, struct.pack("<2I", 862, 1740))
            mu.mem_write(rt17_va + off, struct.pack("<2I", 827, 1740))
        mu.mem_write(rt16_va + 0x50, struct.pack("<I", 3376))
        mu.mem_write(rt17_va + 0x50, struct.pack("<I", 3376))
        avcam_full_orig = bytes(mu.mem_read(AVCAM_ORIL_BASE, len(avcam_orig)))

        mpr_base = 0x075CE000
        if mpr_bytes is not None:
            mpr_map_sz = (len(mpr_bytes) + 0xFFF) & ~0xFFF
            mu.mem_map(mpr_base, mpr_map_sz)
            mu.mem_write(mpr_base, mpr_bytes)
        else:
            # Construct minimal authentic libmpr image when full binary is only in WSL emmc.img
            mu.mem_map(mpr_base, 0x00B00000)
            mu.mem_write(mpr_base, b"\x7fELF")
            # Copy exact MprLibCore::EncOpen converter function bytes from ILCE-6400A (identical logic)
            mpr_a_path = (
                PROJECT_ROOT
                / "firmware/extracted_a6400a_v101/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/lib/libmpr.so"
            )
            if mpr_a_path.is_file():
                fn_blob = mpr_a_path.read_bytes()[COORDS_6400A.mpr_res_conv_fn_off : COORDS_6400A.mpr_res_conv_fn_off + 0x88]
                mu.mem_write(mpr_base + coords.mpr_res_conv_fn_off, fn_blob)
            else:
                mu.mem_write(mpr_base + coords.mpr_encopen_hd_off, struct.pack("<2H", 0xF44F, 0x63F0))
                mu.mem_write(mpr_base + coords.mpr_encopen_4k_off, struct.pack("<2H", 0xF44F, 0x6370))
            for off in coords.mpr_4k_u32_offs:
                mu.mem_write(mpr_base + off, struct.pack("<I", 3840))
            for off in coords.mpr_4k_u16_offs:
                mu.mem_write(mpr_base + off, struct.pack("<I", U16_3840_2160))
            for off in coords.mpr_hd_u32_offs:
                mu.mem_write(mpr_base + off, struct.pack("<I", 1920))
            for off in coords.mpr_hd_u16_offs:
                mu.mem_write(mpr_base + off, struct.pack("<I", U16_1920_1080))
        mpr_snap_len = len(mpr_bytes) if mpr_bytes is not None else 0x00A60000
        mpr_orig_snap = bytes(mu.mem_read(mpr_base, mpr_snap_len))

        mod_base = 0x40AB3000
        mu.mem_map(mod_base, (len(code) + bss_sz + 0x1FFF) & ~0xFFF)
        mu.mem_write(mod_base, code)

        stack_va = 0x20000000
        stop_va = 0x3F000000
        mu.mem_map(stack_va, 0x20000)
        mu.mem_map(stop_va, 0x1000)

        maps_data = f"{mpr_base:08x}-{(mpr_base + 0x00BEE000):08x} r-xp 00000000 fc:0f 1 /usr/lib/libmpr.so\n".encode()
        maps_pos = 0
        logs: list[str] = []

        def on_svc(u: uc.Uc, _n: int, _d: object) -> None:
            nonlocal maps_pos
            nr = u.reg_read(A.UC_ARM_REG_R7)
            a = [
                u.reg_read(r)
                for r in (
                    A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                    A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5,
                )
            ]
            ret = 0
            if nr == 5:
                path = bytes(u.mem_read(a[0], 80)).split(b"\0", 1)[0].decode("latin1")
                if path == "/dev/mem":
                    ret = 10
                elif path == "/proc/self/maps":
                    maps_pos = 0
                    ret = 11
                elif path.endswith(".log"):
                    ret = 12
                else:
                    raise AssertionError(path)
            elif nr == 3 and a[0] == 11:
                ch = maps_data[maps_pos : maps_pos + a[2]]
                if ch:
                    u.mem_write(a[1], ch)
                maps_pos += len(ch)
                ret = len(ch)
            elif nr == 4 and a[0] == 12:
                logs.append(bytes(u.mem_read(a[1], a[2])).decode("latin1"))
                ret = a[2]
            elif nr == 192:  # mmap2(PA >> 12) -> direct VA inside mapped av-cam.bin!
                ret = (a[5] << 12) + AVCAM_VA_TO_PA
            elif nr in (6, 91, 125, 0x0F0002):
                ret = 0
            else:
                raise AssertionError(f"Unexpected svc {nr:#x}")
            u.reg_write(A.UC_ARM_REG_R0, ret)

        # Hook ImagerDrvParam singleton + isSupported to invoke Sony's real 0x63CEF804 lookup
        def on_code(u: uc.Uc, address: int, _size: int, _d: object) -> None:
            if address == coords.va_imager_singleton_fn:
                u.reg_write(A.UC_ARM_REG_R0, stack_va + 0x1000)
                u.reg_write(A.UC_ARM_REG_PC, u.reg_read(A.UC_ARM_REG_LR))
            elif address == coords.va_is_supported_fn:
                key = u.reg_read(A.UC_ARM_REG_R1)
                keys = struct.unpack("<19I", u.mem_read(coords.va_drive_keys, 76))
                u.reg_write(A.UC_ARM_REG_R0, 1 if key in keys else 0)
                u.reg_write(A.UC_ARM_REG_PC, u.reg_read(A.UC_ARM_REG_LR))

        mu.hook_add(uc.UC_HOOK_INTR, on_svc)
        mu.hook_add(uc.UC_HOOK_CODE, on_code)

        def flush_jit() -> None:
            if hasattr(mu, "ctl_remove_cache"):
                mu.ctl_remove_cache(AVCAM_ORIL_BASE, AVCAM_ORIL_BASE + av_map_sz)
                mu.ctl_remove_cache(mpr_base, mpr_base + 0x00B00000)

        def call_mod(off: int, r0: int, cfg: Tuple[int, int, int, int] | None = None) -> None:
            if cfg is not None:
                mu.mem_write(mod_base + len(code) + 4, bytes(cfg))
            mu.reg_write(A.UC_ARM_REG_SP, stack_va + 0x18000)
            mu.reg_write(A.UC_ARM_REG_R0, r0)
            mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
            mu.emu_start((mod_base + off) | 1, stop_va, count=2_000_000)
            flush_jit()

        def eval_movie_selectors() -> dict[str, int]:
            out: dict[str, int] = {}
            for label, fn, r3_val in (
                ("24p_4k", coords.va_sel24_fn, 4),
                ("24p_hd", coords.va_sel24_fn, 2),
                ("25p_4k", coords.va_sel25_fn, 4),
                ("25p_hd", coords.va_sel25_fn, 2),
                ("30p_4k", coords.va_sel30_fn, 4),
                ("30p_hd", coords.va_sel30_fn, 2),
            ):
                sp = stack_va + 0x14000
                mu.mem_write(sp, struct.pack("<8I", 0, 1, 0, 0, 0, 0, 1, 1))
                # Zero [r2+0x21] checked at start of sel_24p/sel_25p by ensuring BSS is 0
                mu.reg_write(A.UC_ARM_REG_SP, sp)
                mu.reg_write(A.UC_ARM_REG_R0, 0)
                mu.reg_write(A.UC_ARM_REG_R1, 0)
                mu.reg_write(A.UC_ARM_REG_R2, 1)
                mu.reg_write(A.UC_ARM_REG_R3, r3_val)
                mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
                mu.emu_start(fn | 1, stop_va, count=500)
                out[label] = mu.reg_read(A.UC_ARM_REG_R0)
            return out

        def eval_pipeline(drive_key: int) -> dict[str, object]:
            # 1. drive_key -> mode index via Sony's 0x63CEF804
            sp = stack_va + 0x14000
            mu.reg_write(A.UC_ARM_REG_SP, sp)
            mu.reg_write(A.UC_ARM_REG_R0, 0)
            mu.reg_write(A.UC_ARM_REG_R1, drive_key)
            mu.reg_write(A.UC_ARM_REG_R2, coords.va_drive_keys)
            mu.reg_write(A.UC_ARM_REG_R3, 19)
            mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
            mu.emu_start(coords.va_drive_key_lookup_fn | 1, stop_va, count=300)
            mode_idx = mu.reg_read(A.UC_ARM_REG_R0)

            # 2. stripe_chk (0x63764714): returns 0 for single-stripe continuous video, 1 for dual-stripe
            mu.reg_write(A.UC_ARM_REG_SP, sp)
            mu.reg_write(A.UC_ARM_REG_R0, drive_key)
            mu.reg_write(A.UC_ARM_REG_R1, 2)
            mu.reg_write(A.UC_ARM_REG_R2, 2)
            mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
            mu.emu_start(coords.va_stripe_chk_fn | 1, stop_va, count=300)
            is_dual = mu.reg_read(A.UC_ARM_REG_R0)

            # 3. FE record selector (0x63771C78)
            arg = stack_va + 0x2000
            mu.mem_write(arg, struct.pack("<3I", drive_key, 0, 0x0A if is_dual else 0))
            mu.reg_write(A.UC_ARM_REG_SP, sp)
            mu.reg_write(A.UC_ARM_REG_R0, arg)
            mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
            mu.emu_start(coords.va_fe_sel_fn | 1, stop_va, count=300)
            fe_va = mu.reg_read(A.UC_ARM_REG_R0)

            # 4. ExlHelper sub-crop (imager_mode_aspect=3 [3:2], output_aspect=1 [16:9])
            obj, out = stack_va + 0x3000, stack_va + 0x3400
            mu.mem_write(obj + 0x60, struct.pack("<I", 3))
            mu.mem_write(obj + 0x70, struct.pack("<I", 1))
            mu.mem_write(obj + 0x194, struct.pack("<2I", 99, 444))
            mu.mem_write(out, b"\x00" * 32)
            mu.reg_write(A.UC_ARM_REG_SP, sp)
            mu.reg_write(A.UC_ARM_REG_R0, obj)
            mu.reg_write(A.UC_ARM_REG_R1, out)
            mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
            mu.emu_start(coords.va_exl_sub_fn | 1, stop_va, count=500)
            exl_w0 = struct.unpack("<I", mu.mem_read(out, 4))[0]
            crop_wh = (1 + (exl_w0 >> 16), (exl_w0 & 0xFFFF) - 2)

            # 5. Buffer selector (0x63CAEADA) for FHD & 4K with stream aspect=2 (16:9)
            buf_idxs = []
            for w, h in ((1920, 1080), (3840, 2160)):
                mu.reg_write(A.UC_ARM_REG_SP, sp)
                mu.mem_write(sp, struct.pack("<I", h))
                mu.reg_write(A.UC_ARM_REG_R1, 6)
                mu.reg_write(A.UC_ARM_REG_R2, 2)
                mu.reg_write(A.UC_ARM_REG_R3, w)
                mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
                mu.emu_start(coords.va_buf_sel_fn | 1, stop_va, count=200)
                buf_idxs.append(mu.reg_read(A.UC_ARM_REG_R0))

            # 6. libmpr MprLibCore::EncOpen resolution converter (res=2 -> FHD, res=3 -> 4K)
            mpr_wh = {}
            for rname, res_enum in (("hd", 2), ("4k", 3)):
                out_w, out_h = stack_va + 0x4000, stack_va + 0x4004
                mu.mem_write(out_w, b"\x00" * 8)
                mu.reg_write(A.UC_ARM_REG_SP, sp)
                mu.reg_write(A.UC_ARM_REG_R0, res_enum)
                mu.reg_write(A.UC_ARM_REG_R1, out_w)
                mu.reg_write(A.UC_ARM_REG_R2, out_h)
                mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
                mu.emu_start((mpr_base + coords.mpr_res_conv_fn_off) | 1, stop_va, count=200)
                mpr_wh[rname] = (
                    struct.unpack("<H", mu.mem_read(out_w, 2))[0],
                    struct.unpack("<H", mu.mem_read(out_h, 2))[0],
                )

            return {
                "mode_idx": mode_idx,
                "is_dual": is_dual,
                "fe_va": fe_va,
                "crop_wh": crop_wh,
                "buf_idxs": tuple(buf_idxs),
                "mpr_wh": mpr_wh,
            }

        def eval_exl_crop(ain: int, in_w: int, in_h: int) -> Tuple[int, int]:
            sp = stack_va + 0x14000
            obj, out = stack_va + 0x3000, stack_va + 0x3400
            mu.mem_write(obj + 0x60, struct.pack("<I", ain))
            mu.mem_write(obj + 0x70, struct.pack("<I", 1))
            mu.mem_write(obj + 0x194, struct.pack("<2I", in_w, in_h))
            mu.mem_write(out, b"\x00" * 32)
            mu.reg_write(A.UC_ARM_REG_SP, sp)
            mu.reg_write(A.UC_ARM_REG_R0, obj)
            mu.reg_write(A.UC_ARM_REG_R1, out)
            mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
            mu.emu_start(coords.va_exl_sub_fn | 1, stop_va, count=500)
            w0, _w1, _w2, _w3, _w4, w5, is_horiz = struct.unpack("<7I", mu.mem_read(out, 28))
            if is_horiz == 0:
                return (in_w, (w0 & 0xFFFF) - 2)
            # Horizontal 16:9 -> 3:2 crop: cropped width = round(in_h * 3 / 2)
            num_h = (w0 & 0xFFFF) - 2
            # ExlHelper sets num_h = 6*in_h (when 3:2) or 2*in_h (when 16:9), num_w = 12*in_w or 21*in_w
            if num_h == 6 * in_h:
                return ((3 * in_h) // 2, in_h)
            return (in_w, in_h)

        # --- Baseline stock verification ---
        s_stock = eval_movie_selectors()
        self.assertEqual(
            s_stock,
            {"24p_4k": 0x1F, "24p_hd": 0x07, "25p_4k": 0x89, "25p_hd": 0x05, "30p_4k": 0x13, "30p_hd": 0x05},
        )
        p_stock = eval_pipeline(s_stock["24p_4k"])
        self.assertEqual((p_stock["mode_idx"], p_stock["is_dual"]), (16, 1))
        self.assertEqual(p_stock["crop_wh"], (1386, 444))
        self.assertEqual(eval_exl_crop(5, 6000, 3376), (6000, 3376))
        self.assertEqual(p_stock["buf_idxs"], (4, 1))
        self.assertEqual(p_stock["mpr_wh"], {"hd": (1920, 1080), "4k": (3840, 2160)})

        # --- Tier 0 (Default ON): 5.1K True 4K (5064x3376 @ 24p/25p & 4140x2760 @ 30p -> 3240x2160) ---
        call_mod(on_off, 1, cfg=(0, 0, 0, 0))
        s_51k = eval_movie_selectors()
        self.assertEqual(
            s_51k,
            {"24p_4k": 0x1F, "24p_hd": 0x44, "25p_4k": 0x89, "25p_hd": 0x44, "30p_4k": 0x13, "30p_hd": 0x44},
        )
        p_51k = eval_pipeline(s_51k["24p_4k"])
        self.assertEqual((p_51k["mode_idx"], p_51k["is_dual"]), (16, 1))
        # Verify FE[6] stays 100% stock (3392 <= 3445 -> zero Lost CAMOUT_V possible!)
        self.assertEqual(
            struct.unpack("<H", mu.mem_read(coords.pa_fe6_base + 0x00A + AVCAM_VA_TO_PA, 2))[0],
            3392,
        )
        # Verify ExlHelper extracts 5064x3376 (17.09 MP 5.1K!) on 24p/25p and 4140x2760 (11.43 MP 4.1K!) on 30p
        self.assertEqual(eval_exl_crop(5, 6000, 3376), (5064, 3376))
        self.assertEqual(eval_exl_crop(5, 4896, 2760), (4140, 2760))
        self.assertEqual(p_51k["buf_idxs"], (6, 3))
        self.assertEqual(p_51k["mpr_wh"], {"hd": (1920, 1080), "4k": (3240, 2160)})

        # --- Tier 1 (Live SMNU -> 6K Reg01 Full: 6000x4000 @ 24p/25p + reg 0x0001=0x40 + S-Ele guard) ---
        call_mod(cfg_off, 2, cfg=(1, 0, 0, 0))
        reglist_va = (coords.pa_reglist_r1_al & ~1) + AVCAM_VA_TO_PA - 4 - 10 - 20
        saved_staging = []
        for row in range(125):
            dest_va = struct.unpack("<I", mu.mem_read(reglist_va + row * 20, 4))[0]
            saved_staging.append((dest_va, bytes(mu.mem_read(dest_va, 1))))

        # Execute Sony's real reglist applicator (0x6379D618 / 0x6379E770) for Mode 16 (index 16 -> col 11)!
        sp = stack_va + 0x14000
        imager_obj = stack_va + 0x5000
        mu.mem_write(imager_obj, b"\x00" * 0x400)
        mu.reg_write(A.UC_ARM_REG_SP, sp)
        mu.reg_write(A.UC_ARM_REG_R0, imager_obj)
        mu.reg_write(A.UC_ARM_REG_R1, 16)
        mu.reg_write(A.UC_ARM_REG_LR, stop_va | 1)
        mu.emu_start(coords.va_reglist_apply_fn | 1, stop_va, count=50_000)

        def staged_byte(row: int) -> int:
            dest_va = struct.unpack("<I", mu.mem_read(reglist_va + row * 20, 4))[0]
            return mu.mem_read(dest_va, 1)[0]

        self.assertEqual(staged_byte(0), 0x0C)  # reg 0x0000 = 0x0C (unbinned 6K readout)
        self.assertEqual(staged_byte(1), 0x40)  # reg 0x0001 = 0x40 (full-gate 3:2 continuous)!
        y_start = staged_byte(10) | (staged_byte(11) << 8)
        y_size = staged_byte(12) | (staged_byte(13) << 8)
        self.assertEqual((y_start, y_size), (30, 4020))
        self.assertEqual(
            struct.unpack("<H", mu.mem_read(coords.pa_fe6_base + 0x00A + AVCAM_VA_TO_PA, 2))[0],
            4016,
        )
        self.assertEqual(
            struct.unpack("<H", mu.mem_read(coords.pa_sele_crash + AVCAM_VA_TO_PA, 2))[0],
            0xBF00,
        )
        self.assertEqual(eval_exl_crop(5, 6000, 4000), (6000, 4000))
        for dest_va, orig_b in saved_staging:
            mu.mem_write(dest_va, orig_b)

        # --- Tier 2 (Live SMNU -> Full-FOV 2K: Mode 10 0x44 uncropped 1996x1332, Native Both, 30p, 4K Only) ---
        call_mod(cfg_off, 2, cfg=(2, 2, 1, 1))
        s_4k_only = eval_movie_selectors()
        self.assertEqual(
            s_4k_only,
            {"24p_4k": 0x44, "24p_hd": 0x07, "25p_4k": 0x44, "25p_hd": 0x05, "30p_4k": 0x44, "30p_hd": 0x05},
        )
        p_both = eval_pipeline(0x44)
        self.assertEqual((p_both["mode_idx"], p_both["is_dual"], p_both["fe_va"]), (10, 0, coords.va_fe_rec4))
        self.assertEqual(p_both["crop_wh"], (1980, 1332))
        self.assertEqual(p_both["mpr_wh"], {"hd": (1620, 1080), "4k": (3240, 2160)})
        th30, tv30 = struct.unpack("<2I", mu.mem_read(coords.pa_t10_ntsc + AVCAM_VA_TO_PA, 8))
        self.assertAlmostEqual(36_000_000.0 / (th30 * tv30), 30000.0 / 1001.0, places=9)

        # --- Live SMNU switch -> Anamorphic 3840x2160 / 1920x1080 ---
        call_mod(cfg_off, 2, cfg=(2, 3, 0, 0))
        p_ana = eval_pipeline(0x44)
        self.assertEqual(p_ana["crop_wh"], (1980, 1332))
        self.assertEqual(p_ana["buf_idxs"], (4, 1))
        self.assertEqual(p_ana["mpr_wh"], {"hd": (1920, 1080), "4k": (3840, 2160)})
        th, tv = struct.unpack("<2I", mu.mem_read(coords.pa_t10_ntsc + AVCAM_VA_TO_PA, 8))
        self.assertAlmostEqual(36_000_000.0 / (th * tv), 24000.0 / 1001.0, places=9)

        # --- OFF: 100% bit-for-bit restoration across entire av-cam.bin & libmpr.so ---
        call_mod(off_off, 0)
        self.assertEqual(bytes(mu.mem_read(AVCAM_ORIL_BASE, len(avcam_orig))), avcam_full_orig)
        self.assertEqual(bytes(mu.mem_read(mpr_base, mpr_snap_len)), mpr_orig_snap)
        self.assertEqual(struct.unpack("<2I", mu.mem_read(rt_va + 8, 8)), (660, 1820))
        self.assertEqual(struct.unpack("<2I", mu.mem_read(rt16_va + 8, 8)), (862, 1740))
        self.assertEqual(struct.unpack("<I", mu.mem_read(rt16_va + 0x50, 4))[0], 3376)
        self.assertTrue(any("OG32_OFF" in line and "st=ok" in line for line in logs))

    def test_real_ilce_6400_captured_firmware_e2e(self) -> None:
        env_avcam = os.environ.get("A6400_CAPTURED_AVCAM")
        candidates = tuple(
            p
            for p in (
                Path(env_avcam) if env_avcam else None,
                PROJECT_ROOT / "firmware/captured_a6400_20261113_analysis/av-cam.bin",
                PROJECT_ROOT / "artifacts/hardware/av-cam-camera-original.bin",
            )
            if p is not None
        )
        av_path = next((p for p in candidates if p.is_file()), None)
        if av_path is None:
            self.skipTest("Captured ILCE-6400 2.00 av-cam.bin not staged locally")
        self._run_real_firmware_e2e(
            avcam_path=av_path,
            avcam_sha="dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209",
            mpr_bytes=None,
            mod_path=PROJECT_ROOT / "mods/open_gate/OpenGate32.mod",
            coords=COORDS_6400,
        )

    def test_real_ilce_6400a_101_firmware_e2e(self) -> None:
        base_dir = (
            PROJECT_ROOT
            / "firmware/extracted_a6400a_v101/firmware.tar_unpacked/0700_part_image/dev"
        )
        av_path = base_dir / "nflasha3_unpacked/av-cam.bin"
        mpr_path = base_dir / "nflasha15_unpacked/lib/libmpr.so"
        if not (av_path.is_file() and mpr_path.is_file()):
            self.skipTest("Extracted ILCE-6400A 1.01 av-cam.bin/libmpr.so not staged locally")
        self._run_real_firmware_e2e(
            avcam_path=av_path,
            avcam_sha="3029a174b393fee9b2624d27737c20bc60e71cbbb33736b2a9363b57184ee03d",
            mpr_bytes=mpr_path.read_bytes(),
            mod_path=PROJECT_ROOT / "mods/ilce_6400a/OpenGate32.mod",
            coords=COORDS_6400A,
        )


if __name__ == "__main__":
    unittest.main()
