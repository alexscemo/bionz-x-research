"""QEMU qtest/QMP verification for CXD90045 stateful clocks/resets, PMIC/SoC
reset, E0705 MMIO coverage, P2S Ch1 IMX284 sensor bank, sub-micro 256B + HDMI
cable reporting, and UHS-I U3 SD speed-class negotiation.
"""
from __future__ import annotations

import json
import struct
import unittest

from src.a6400.sensor.imx284_p2s import (
    IMX284_CHIP_ID,
    P2sBurst,
    encode_p2s_frame,
)
from tests.test_qemu_emmc import NATIVE_AVAILABLE
from tests.test_qemu_media import (
    MMC0,
    SdHost,
    qtest_machine,
    sio3_exchange,
    submicro_crc,
    temporary_media,
)
from tools.research.qemu_boot_probe import QmpDisconnected, digest
from tools.research.qemu_media import create_sdhc_fat32_image, sd_blockdev, sd_event


@unittest.skipUnless(NATIVE_AVAILABLE, "Optional WSL/Linux sony-a6400 QEMU build absent")
class QemuPrerequisitesHardwareTests(unittest.TestCase):
    def test_e0705_remaining_mmio_windows_retire_cleanly(self):
        with temporary_media() as root:
            with qtest_machine(root, "sony-a6400", "-d", "unimp") as (io, _):
                # GP-DMAC1 (0xF0103000)
                io.write(0xF0103030, 1)
                self.assertEqual(io.read(0xF0103030), 1)
                io.write(0xF0103110, 0x01000001)  # start auto-clears, busy bit 18 == 0
                self.assertEqual(io.read(0xF0103110) & ((1 << 18) | 1), 0)
                io.write(0xF0103008, 0x9)

                # emmc0 vendor wrapper (0xF0304100..0xF03044FC)
                io.write(0xF0304100, 7)
                io.write(0xF0304108, 0x0E010000)
                self.assertEqual(io.read(0xF0304108), 0x0E010000)
                self.assertEqual(io.read(0xF030410C), 0)
                io.write(0xF0304244, 0x8000)
                self.assertEqual(io.read(0xF0304244), 0x8000)

                # spi0, sircs, i2c0, tpu, combophy, pbl-aic0, dfe2, audioip+0x8000, cal-nor
                io.write(0xF2006000, 0x40)
                io.write(0xF2400000, 0x654C)
                io.write(0xF2400020, 0xA01)
                self.assertEqual(io.read(0xF2401010, "w") & 0x8000, 0)
                self.assertEqual(io.read(0xF2401014, "w") & 0x8000, 0)
                self.assertEqual(io.read(0xF2401000, "w"), 0)
                self.assertEqual(io.read(0xF2401020, "w"), 0)
                self.assertEqual(io.read(0xF2404010, "w"), 0)
                self.assertEqual(io.read(0xF2404110, "w"), 0)
                self.assertEqual(io.read(0xF2404210, "w"), 0)
                io.write(0xF2931100, 3)
                self.assertEqual(io.read(0xF29320C0), 0)
                io.write(0xF30A0000, 0x303)
                self.assertEqual(io.read(0xF30A0000), 0x303)
                io.write(0xF30A0004, 0x303)
                self.assertEqual(io.read(0xF30A0004), 0x303)
                io.write(0xF30A000C, 0x77FFF)
                self.assertEqual(io.read(0xF30A000C), 0x77FFF)
                io.write(0xF010318C, 0x1234)
                self.assertEqual(io.read(0xF010318C), 0x1234)
                io.write(0xF0103190, (1 << 18) | 0x41)
                self.assertEqual(io.read(0xF0103190), 0x40)
                io.write(0xF3203384, 0)
                io.write(0xF3215B00, 0)
                io.write(0xF3428000, 0x1000)
                self.assertEqual(io.read(0xF3428000), 0x1000)
                self.assertEqual(io.read(0xF6000000), 0)
                self.assertEqual(io.read(0xF6000010, "b"), 0)

            log_text = (root / "sony-a6400.log").read_text(errors="replace")
            self.assertNotIn(".unmodelled:", log_text)

    def test_stateful_clkrst_zma0_rc0_and_sa4_models(self):
        with temporary_media() as root:
            with qtest_machine(root, "sony-a6400,clkrst-model=on") as (io, _):
                # clkrst3 0xF2900030/34/38 triplet (cxd900x0_pcie_powersave)
                io.write(0xF2900034, 0x11500000)
                self.assertEqual(io.read(0xF2900030), 0x11500000)
                io.write(0xF2900038, 0x01100000)
                self.assertEqual(io.read(0xF2900030), 0x10400000)
                self.assertEqual(io.read(0xF2900038), 0)
                io.write(0xF2900038, 0x10400000)
                self.assertEqual(io.read(0xF2900030) & 0x11500000, 0)

                # f2a0 triplet + CRCDC 0xF2A31000
                io.write(0xF2A00024, 0x0000000F)
                self.assertEqual(io.read(0xF2A00020), 0x0000000F)
                io.write(0xF2A00028, 0x00000005)
                self.assertEqual(io.read(0xF2A00020), 0x0000000A)
                self.assertEqual(io.read(0xF2A31190), 0x3F)
                io.write(0xF2A31030, 1)
                self.assertEqual(io.read(0xF2A31030) & 1, 0)

                # ZMA0 (0xF3500000) & RC0 (0xF3880000) PBL AIC/AIF semantics
                for base in (0xF3500000, 0xF3880000):
                    io.write(base + 0x0034, 0x101)
                    self.assertEqual(io.read(base + 0x0034) & 0x101, 0)
                    io.write(base + 0x1008, (1 << 24) | 0x1234)
                    self.assertEqual(io.read(base + 0x1008), 0x1234)
                    io.write(base + 0x1200, 0x10803)
                    self.assertEqual(io.read(base + 0x1200) & 1, 1)
                    io.write(base + 0x1204, 1 << 30)  # CH_STOP_ACK
                    self.assertEqual(io.read(base + 0x1204) >> 31, 0)
                    self.assertEqual(io.read(base + 0x1200) & 1, 0)

                # SA4 full DSP core at 0xF35D0000
                sa4_id = b"".join(
                    struct.pack("<I", io.read(0xF35D00F0 + 4 * k)) for k in range(4)
                )
                self.assertEqual(sa4_id, b"SA1_V1.0SA5_V1.0")
                io.write(0xF35D0000, 1)
                self.assertEqual(io.read(0xF35D0000), 1)

    def test_pmic_soc_warm_reset_and_sram_gpio_poweroff(self):
        with temporary_media() as root:
            with qtest_machine(root, "sony-a6400,pmic-reset=on", "-no-shutdown") as (io, qmp):
                # Boot xrstreq=1 write (BIT(4) without BIT(0)) must NOT reset
                io.write(0xF2915044, 0x10)
                self.assertEqual(io.read(0xF2915040), 0x10)
                self.assertEqual([e for e in qmp.events if e.get("event") == "RESET"], [])

                # Warm reset (cxd900x0_reset writes 0x11) must emit QMP RESET
                io.write(0xF2915044, 0x11)
                io.step(10_000)
                qmp.request("query-status")
                self.assertTrue(
                    any(e.get("event") == "RESET" for e in qmp.events),
                    f"Expected RESET event in {qmp.events}",
                )

                # SRAM 0xFE020000 power-down sequence: DDR-A + DDR-B self-refresh
                io.write(0xF0104090, 0x1AE)
                self.assertEqual(io.read(0xF0104148) & 0x10, 0x10)
                io.write(0xF0105090, 0xEA)
                self.assertEqual(io.read(0xF0105148) & 0x10, 0x10)

                # Drive GPIO P10.5 high, clear XRSTREQ (0xF2915048=0x10), then
                # pull GPIO P10.5 low (0xF2910A48=0x20) -> guest SHUTDOWN
                io.write(0xF2910A04, 0x20)  # dir out
                io.write(0xF2910A44, 0x20)  # out high
                io.write(0xF2915048, 0x10)  # XRSTREQ_CLR arms power-off
                io.write(0xF2910A48, 0x20)  # P10.5 low -> cuts rails
                io.step(10_000)
                try:
                    qmp.request("query-status")
                except QmpDisconnected:
                    pass
                self.assertTrue(
                    any(e.get("event") == "SHUTDOWN" for e in qmp.events),
                    f"Expected SHUTDOWN event in {qmp.events}",
                )

    def test_p2s_ch1_imx284_recognizer_and_submicro_256b_hdmi(self):
        with temporary_media() as root:
            fixture = root / "micro.bin"
            slot_a = submicro_crc(bytes([0x20]) + bytes(27) + bytes([0x01, 0x02]) + bytes(98))
            slot_c = submicro_crc(bytes([0x60]) + bytes(127))
            slot_b = submicro_crc(
                struct.pack("<BBH", 0x30, 0, 8) + b"\x03\x04\x00\x00\x00\x00\x00\x03" + bytes(116)
            )
            fixture.write_bytes(slot_a + slot_c + slot_b)

            machine = f"sony-a6400,hdmi-cec=on,submicro-lens-power=on,submicro-frames={fixture}"
            with qtest_machine(root, machine) as (io, qmp):
                # 1. Verify 256-byte Mode 1 SPI poll (tx[0]=0x21, tx[128]=0x61)
                tx256 = (b"\x21" + bytes(127)) + (b"\x61" + bytes(127))
                rx256 = sio3_exchange(io, tx256)
                self.assertEqual(rx256[0], 0x20)
                self.assertEqual(rx256[128], 0x60)  # Slot C returned, not 0x30!
                # HDMI plugged by default with hdmi-cec=on -> Slot A[29] bit 6 == 1
                self.assertEqual(rx256[29] & 0x40, 0x40)
                self.assertEqual(rx256[:128], submicro_crc(rx256[:128]))
                self.assertEqual(io.read(0xF292C160), 1)
                self.assertEqual(io.read(0xF2A40000 + 0x3004 * 4) & 0xF3, 0xF3)
                self.assertEqual(io.read(0xF2A40000 + 0x0108 * 4) & 0x02, 0x02)
                self.assertEqual(io.read(0xF2A40000 + 0x0101 * 4) & 0x80, 0x80)

                # Unplug HDMI via QMP -> Slot A[29] bit 6 & oLWAITSTAT clear
                qmp.request(
                    "qom-set",
                    {
                        "path": "/machine/cxd90045-hdmi-cec",
                        "property": "hdmi-plugged",
                        "value": False,
                    },
                )
                rx_unplug = sio3_exchange(io, b"\x21" + bytes(127))
                self.assertEqual(rx_unplug[29] & 0x40, 0)
                self.assertEqual(io.read(0xF292C160), 0)

                # 2. Verify P2S Ch 1 (0xFC088000) IMX284 mode-table recognizer
                p2s_payload = encode_p2s_frame([
                    P2sBurst(IMX284_CHIP_ID, 0x0000, bytes([0x04, 0x04, 0x00])),
                    P2sBurst(IMX284_CHIP_ID, 0x0007, bytes([0x8F, 0x03])),
                    P2sBurst(IMX284_CHIP_ID, 0x000A, bytes([0x6A, 0x00, 0x6C, 0x04])),
                    P2sBurst(IMX284_CHIP_ID, 0x000F, bytes([0x30])),
                    P2sBurst(IMX284_CHIP_ID, 0x0017, bytes([0x1A])),
                    P2sBurst(IMX284_CHIP_ID, 0x00B3, bytes([0x90])),
                    P2sBurst(IMX284_CHIP_ID, 0x0420, bytes([0x7E, 0x00])),
                    P2sBurst(IMX284_CHIP_ID, 0x0803, bytes([0x80])),
                    P2sBurst(IMX284_CHIP_ID, 0x0924, bytes([0x00])),
                ])
                for slot_idx, b in enumerate(p2s_payload):
                    io.write(0xFC088340 + 4 * slot_idx, b)
                io.write(0xFC088E00, 1)
                io.write(0xFC088F08, 1)  # Ch 1 START
                io.step(100_000)
                self.assertEqual(io.read(0xFC088F08) & 1, 0)
                self.assertEqual(io.read(0xFC088F04) & 1, 1)
                self.assertEqual(
                    qmp.request(
                        "qom-get",
                        {"path": "/machine/cxd90014-p2s-ch1", "property": "imx284-active-col"},
                    ),
                    0,
                )
                self.assertEqual(
                    qmp.request(
                        "qom-get",
                        {"path": "/machine/cxd90014-p2s-ch1", "property": "imx284-mode"},
                    ),
                    "still_full_6000x4000_14bit",
                )
                self.assertTrue(
                    qmp.request(
                        "qom-get",
                        {"path": "/machine/cxd90014-p2s-ch1", "property": "imx284-streaming"},
                    )
                )
                self.assertEqual(
                    qmp.request(
                        "qom-get",
                        {"path": "/machine/cxd90014-p2s-ch1", "property": "imx284-pgc"},
                    ),
                    0x038F,
                )

    def test_uhs_u3_sd_voltage_switch_tuning_and_ssr_speed_class(self):
        with temporary_media() as root:
            card = root / "uhs_4g.img"
            create_sdhc_fat32_image(card, size_gib=4)
            node = sd_blockdev(card, digest(card), root / "inputs", root / "run")
            with qtest_machine(
                root,
                "sony-a6400,sd-uhs-u3=on",
                "-drive",
                "if=none,id=a6400-sd",
                "-blockdev",
                json.dumps(node),
            ) as (io, qmp):
                sd = SdHost(io, MMC0)
                sd.enable()
                sd_event(qmp, "insert")
                io.step(1_000_000)
                sd.command(0, 0, 0)
                self.assertEqual(sd.command(8, 0x1AA, 0x1A), 0x1AA)
                # ACMD41 with HCS (bit 30) + XPC (bit 28) + S18R (bit 24)
                sd.command(55, 0, 0x1A)
                ocr = sd.command(41, 0x51FF8000, 2)
                self.assertEqual(ocr & 0xC1000000, 0xC1000000)  # BUSY | CCS | S18A

                # CMD11 1.8V switch -> DAT[3:0] drops to 0x0, then rises to 0xF on V18_ENA
                sd.command(11, 0, 0x1A)
                self.assertEqual((sd.read(0x24) >> 20) & 0xF, 0x0)
                sd.write(0x3E, 0x0008, "w")  # SDHC_HOSTCTL2 V18_ENA
                self.assertEqual(sd.read(0x3E, "w") & 0x0008, 0x0008)
                self.assertEqual((sd.read(0x24) >> 20) & 0xF, 0xF)

                sd.command(2, 0, 9)
                rca = sd.command(3, 0, 0x1A) & 0xFFFF0000
                sd.command(7, rca, 0x1B)

                # CMD19 UHS-I tuning sets SAMPLING_CLKSEL (0x80), clears EXECUTE_TUNING (0x40),
                # and raises SDHC_NIS_RBUFRDY (0x20) without stranding card state
                sd.write(0x3E, 0x0008 | 0x00C0, "w")
                sd.write(0x30, 0xFFFFFFFF)
                sd.write(0x04, 64)
                sd.write(0x08, 0)
                sd.write(0x0C, ((19 << 8) | 0x3A) << 16 | 0x10)
                io.step(200_000)
                self.assertEqual(sd.read(0x3E, "w") & 0x00C0, 0x0080)
                self.assertEqual(sd.read(0x30) & 0x0020, 0x0020)

                # ACMD13 64-byte SD_STATUS (SSR): Class 10 (0x04) + UHS Grade 3 (0x39)
                sd.command(55, rca, 0x1A)
                sd.write(0x30, 0xFFFFFFFF)
                sd.write(0x04, 64)
                sd.write(0x08, 0)
                sd.write(0x0C, ((13 << 8) | 0x3A) << 16 | 0x10)
                io.step(200_000)
                ssr = b"".join(struct.pack("<I", sd.read(0x20)) for _ in range(16))
                self.assertEqual(ssr[8], 0x04)         # SPEED_CLASS = Class 10
                self.assertEqual(ssr[14] >> 4, 3)      # UHS_SPEED_GRADE = U3 (30 MB/s -> 0xF0 > 0x78)

    def test_zma_jpeg_decode_encode_rc0_vph_and_craw2_still_capture(self):
        from project_paths import PROJECT_ROOT

        sidecar = (
            PROJECT_ROOT
            / "firmware/extracted_a6400a_v101/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/sa_zima_startup_zma1.bin"
        )
        if not sidecar.is_file():
            self.skipTest(f"Optional ZMA1 startup sidecar absent: {sidecar}")

        def build_minimal_422_color_jpeg() -> bytes:
            """Construct a pure-Python 32x16 4:2:2 baseline JPEG (left=red, right=cyan)."""
            # Standard Annex K DHT tables
            dc0_bits = bytes([0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0])
            dc0_val = bytes(range(12))
            dc1_bits = bytes([0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0])
            dc1_val = bytes(range(12))
            ac_bits = bytes([1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
            ac_val = bytes([0x00])  # EOB = single '0' bit for flat DC-only blocks!

            dqt = b"\xff\xdb\x00\x84\x00" + bytes([8] * 64) + b"\x01" + bytes([8] * 64)
            sof0 = struct.pack(">2sHBHHB", b"\xff\xc0", 17, 8, 16, 32, 3) + bytes([
                1, 0x21, 0,  # Y: H=2, V=1 (4:2:2), QT=0
                2, 0x11, 1,  # Cb: H=1, V=1, QT=1
                3, 0x11, 1,  # Cr: H=1, V=1, QT=1
            ])

            def dht_seg(tc_th: int, bits16: bytes, vals: bytes) -> bytes:
                ln = 2 + 1 + 16 + len(vals)
                return struct.pack(">2sHB", b"\xff\xc4", ln, tc_th) + bits16 + vals

            dht = (
                dht_seg(0x00, dc0_bits, dc0_val)
                + dht_seg(0x01, dc1_bits, dc1_val)
                + dht_seg(0x10, ac_bits, ac_val)
                + dht_seg(0x11, ac_bits, ac_val)
            )
            sos = struct.pack(">2sHB", b"\xff\xda", 12, 3) + bytes([
                1, 0x00, 2, 0x11, 3, 0x11, 0, 63, 0,
            ])

            # Encode 4 MCUs (width=32 -> 2 MCUs across, height=16 -> 2 MCUs down):
            # MCU(x=0): Red (Y~82 -> dc=-6, Cb~90 -> dc=-5, Cr~240 -> dc=+14)
            # MCU(x=1): Cyan (Y~178 -> dc=+6, Cb~166 -> dc=+5, Cr~16 -> dc=-14)
            def build_huff_codes(bits16: bytes, vals: bytes):
                codes = {}
                code = 0
                k = 0
                for length in range(1, 17):
                    for _ in range(bits16[length - 1]):
                        codes[vals[k]] = (code, length)
                        code += 1
                        k += 1
                    code <<= 1
                return codes

            c_dc0 = build_huff_codes(dc0_bits, dc0_val)
            c_dc1 = build_huff_codes(dc1_bits, dc1_val)

            bitstream = []

            def put_bits(val: int, n: int):
                for shift in range(n - 1, -1, -1):
                    bitstream.append((val >> shift) & 1)

            def put_dc_block(target_dc: int, pred: list[int], idx: int, tbl: dict):
                diff = target_dc - pred[idx]
                pred[idx] = target_dc
                if diff == 0:
                    c, ln = tbl[0]
                    put_bits(c, ln)
                else:
                    a = abs(diff)
                    cat = a.bit_length()
                    mag = diff if diff > 0 else (diff + (1 << cat) - 1)
                    c, ln = tbl[cat]
                    put_bits(c, ln)
                    put_bits(mag, cat)
                put_bits(0, 1)  # AC EOB ('0')

            pred = [0, 0, 0]
            for _my in range(2):
                # In 2D DCT, F[0,0] = 8 * (pixel - 128); with Q[0] = 8, quantized dc = (pixel - 128)!
                # Left MCU (Red: Y=82 -> dc=-46, Cb=90 -> dc=-38, Cr=240 -> dc=+112)
                put_dc_block(-46, pred, 0, c_dc0)
                put_dc_block(-46, pred, 0, c_dc0)
                put_dc_block(-38, pred, 1, c_dc1)
                put_dc_block(112, pred, 2, c_dc1)
                # Right MCU (Cyan: Y=178 -> dc=+50, Cb=166 -> dc=+38, Cr=16 -> dc=-112)
                put_dc_block(50, pred, 0, c_dc0)
                put_dc_block(50, pred, 0, c_dc0)
                put_dc_block(38, pred, 1, c_dc1)
                put_dc_block(-112, pred, 2, c_dc1)

            while len(bitstream) % 8:
                bitstream.append(1)
            ecs = bytearray()
            for i in range(0, len(bitstream), 8):
                b = 0
                for bit in bitstream[i : i + 8]:
                    b = (b << 1) | bit
                ecs.append(b)
                if b == 0xFF:
                    ecs.append(0x00)
            return b"\xff\xd8" + dqt + sof0 + dht + sos + bytes(ecs) + b"\xff\xd9"

        jpg_bytes = build_minimal_422_color_jpeg()
        sof0_idx = jpg_bytes.index(b"\xff\xc0")
        self.assertEqual(jpg_bytes[sof0_idx + 11], 0x21)

        with temporary_media() as root:
            with qtest_machine(
                root,
                "sony-a6400,liveview-source=bars,still-capture=on",
                "-global",
                "cxd90014-sa.zma1-jpeg-decode=on",
            ) as (io, qmp):
                io.write(0xF3440028, 0x111)       # VPH clock on
                io.write(0xF3440140, 0x30000000)  # panel stage active
                io.write(0xF3440144, 0x01000231)  # YUV layer on

                zma1_prog = sidecar.read_bytes()[0x200 : 0x200 + 2692]

                prog_pa = 0x20200000
                src_jpg_pa = 0x18000000
                dec_yc_pa = 0x2BD5B800
                disp_yc_pa = 0x3C000000
                desc_pa = 0x15EF8000
                rpt_pa = 0x15EF8400
                io.write_memory(prog_pa, zma1_prog)
                io.write_memory(src_jpg_pa, jpg_bytes)

                # Boot ZMA1 (0xF34B0000)
                io.write(0xF34B0000, 1)
                io.write(0xF34B0008, 0x80000000)
                io.write(0xF34B0030, prog_pa)
                io.write(0xF34B0108, 0x80)
                io.write(0xF34B0004, 1)
                io.step(200_000)
                self.assertEqual(io.read(0xF34B0104), 0x80)
                io.write(0xF34B0104, 0x80)
                io.write(0xF34B0108, 0xFF)

                # Submit DGI JPEG Decode (op = 0x11 -> 0x01001100) into 320x240 UYVY
                desc = bytearray(0x180)
                struct.pack_into("<I", desc, 0x0C, (16 << 16) | 32)
                struct.pack_into("<I", desc, 0x18, 320)
                struct.pack_into("<I", desc, 0x1C, 240)
                struct.pack_into("<I", desc, 0x20, 320)
                struct.pack_into("<I", desc, 0x34, src_jpg_pa)
                struct.pack_into("<I", desc, 0x38, len(jpg_bytes))
                struct.pack_into("<I", desc, 0x44, dec_yc_pa)
                struct.pack_into("<I", desc, 0x5C, rpt_pa)
                io.write_memory(desc_pa, bytes(desc))
                io.write(0xF34B0110, 0x01001100)
                io.write(0xF34B0114, desc_pa)
                io.write(0xF34B000C, 1)
                io.step(200_000)
                self.assertEqual(io.read(rpt_pa + 4), 1)
                self.assertEqual(io.read(0xF34B0104), 1)
                io.write(0xF34B0104, 1)

                # Copy/resize via RC0 AIF (0xF3881000): ch0 DRAM-READ -> ch3 DRAM-WRITE
                io.write(0xF3881220, dec_yc_pa)
                io.write(0xF3881224, 320 * 2)
                io.write(0xF3881228, 0)
                io.write(0xF388122C, 240 - 1)
                io.write(0xF38813A0, disp_yc_pa)
                io.write(0xF38813A4, 320 * 2)
                io.write(0xF38813A8, 0)
                io.write(0xF38813AC, 240 - 1)
                io.write(0xF3881200, 0x801)
                io.write(0xF3881380, 0x001)
                io.step(3_000_000)
                self.assertEqual(io.read(0xF3881000) & 0x1001, 0x1001)

                # Scan out PA 0x3C000000 on VPH ch51 in packed UYVY mode
                io.write(0xF3441220, disp_yc_pa)
                io.write(0xF3441224, 320 * 2)
                io.write(0xF3441228, 0)
                io.write(0xF344122C, 240 - 1)
                io.write(0xF3441420, (240 << 16) | 320)
                io.step(20_000_000)
                play_ppm = root / "play.ppm"
                qmp.request(
                    "screendump", {"filename": str(play_ppm), "device": "vph", "head": 0}
                )
                ppm_raw = play_ppm.read_bytes()
                _, _, _, pixels = ppm_raw.split(b"\n", 3)
                # Left half (x=100, y=240 on 640x480) must be bright RED (R > 180, B < 60)
                off_l = (240 * 640 + 100) * 3
                r_l, g_l, b_l = pixels[off_l : off_l + 3]
                self.assertGreater(r_l, 180, f"Expected red left half, got {(r_l, g_l, b_l)}")
                self.assertLess(b_l, 60)
                # Right half (x=540, y=240) must be bright CYAN (R < 60, G > 180, B > 180)
                off_r = (240 * 640 + 540) * 3
                r_r, g_r, b_r = pixels[off_r : off_r + 3]
                self.assertLess(r_r, 60, f"Expected cyan right half, got {(r_r, g_r, b_r)}")
                self.assertGreater(g_r, 180)
                self.assertGreater(b_r, 180)

                # Test DGI JPEG Encode (op = 0x1B -> 0x02001B00) + decode roundtrip!
                enc_desc_pa = 0x15EF8800
                enc_rpt_pa = 0x15EF8C00
                enc_out_pa = 0x19000000
                enc_desc = bytearray(0x180)
                struct.pack_into("<I", enc_desc, 0x04, 0x10)
                struct.pack_into("<I", enc_desc, 0x08, 4)
                struct.pack_into("<I", enc_desc, 0x0C, (240 << 16) | 320)
                struct.pack_into("<I", enc_desc, 0x18, 320)
                struct.pack_into("<I", enc_desc, 0x1C, 240)
                struct.pack_into("<I", enc_desc, 0x20, 320)
                struct.pack_into("<I", enc_desc, 0x34, disp_yc_pa)
                struct.pack_into("<I", enc_desc, 0x5C, enc_rpt_pa)
                for w in range(16):
                    struct.pack_into("<I", enc_desc, 0x80 + 4 * w, 0x08080808)
                    struct.pack_into("<I", enc_desc, 0xC0 + 4 * w, 0x08080808)
                struct.pack_into("<I", enc_desc, 0x110, 1)
                struct.pack_into("<I", enc_desc, 0x118, enc_out_pa)
                struct.pack_into("<II", enc_desc, 0x120, 0x40000 | 1, enc_out_pa)
                io.write_memory(enc_desc_pa, bytes(enc_desc))
                io.write(0xF34B0110, 0x02001B00)
                io.write(0xF34B0114, enc_desc_pa)
                io.write(0xF34B000C, 1)
                io.step(200_000)
                self.assertEqual(io.read(enc_rpt_pa + 4), 1)
                ecs_len = io.read(enc_rpt_pa + 8)
                self.assertGreater(ecs_len, 64)
                ecs_data = io.read_memory(enc_out_pa + 0x10, ecs_len)
                self.assertEqual(ecs_data[-2:], b"\xff\xd9")

                # Test active 0xF3030000 IDT/RCRAW CompressRaw + CAIF link
                craw_pa = 0x1A000000
                io.write(0xF3030108, 0xFF)
                io.write(0xF3030110, 0x00040000)
                io.write(0xF303000C, 1)
                self.assertEqual(io.read(0xF3030104) & 1, 1)
                self.assertEqual(io.read(0xF3030120), 0x00040000)
                io.write(0xF3030104, 1)

                io.write(0xF3031180, craw_pa)
                io.write(0xF3030110, 0x00060000)
                io.write(0xF303000C, 1)
                self.assertEqual(io.read(0xF30313F0), 24_000_000)
                self.assertEqual(io.read(0xF3030104) & 1, 1)
                blk0 = io.read_memory(craw_pa, 16)
                self.assertNotEqual(blk0, bytes(16))

                io.write(0xF2910704, 1 << 15)
                io.write(0xF2910744, 1 << 15)
                io.write(0xF2910748, 1 << 15)
                io.step(300_000)
                self.assertEqual((io.read(0xF2910210) >> 5) & 1, 1)
                hdr8 = sio3_exchange(io, bytes(8))
                self.assertEqual(hdr8, bytes([0x00, 0x03, 0x00, 0x00, 0x14, 0x00, 0x00, 0x00]))
                _ = sio3_exchange(io, bytes(20))
                self.assertEqual((io.read(0xF2910210) >> 5) & 1, 0)


if __name__ == "__main__":
    unittest.main()
