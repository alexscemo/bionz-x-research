"""Offline checks for the ILCE-6700 Jiritsu RAM-chunk acquisition tool (no USB, no camera)."""
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.research import a6700_jiritsu_dump as dump

RAM = "/tmp_wbshd/a6700-" + "0123456789abcdef" * 2


class FakeDevice:
    """Records Senser packets and replies with a scripted Jiritsu status and payload."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def sendSenserPacket(self, pfunc, body):
        self.sent.append((pfunc, bytes(body)))
        status, payload = self.replies.pop(0)
        return 0, struct.pack("<I", status) + payload


class WorkerScriptTests(unittest.TestCase):
    def test_script_stays_in_declared_ram_and_removes_its_own_files(self):
        script = dump.worker_script(RAM).decode("ascii")
        self.assertIn(f"R={RAM}", script)
        self.assertIn(f"busybox rm -f {dump.LOADER} {dump.RUN_SH}", script)
        for forbidden in ("/setting", "ld.so.preload", "nflasha23", "mount ", "mkfs", "dd of=/dev"):
            self.assertNotIn(forbidden, script)

    def test_ram_directory_must_be_a_fresh_nonce_below_tmp_wbshd(self):
        for bad in ("/tmp/a6700-" + "0" * 32, RAM + "/x", "/tmp_wbshd/a6700-xyz", "/tmp_network/a6700-" + "0" * 32):
            with self.assertRaises(ValueError):
                dump.worker_script(bad)


class ChunkJobTests(unittest.TestCase):
    def test_job_is_a_bounded_read_only_dd_with_pad(self):
        job = dump.chunk_job("nflasha2", RAM, 4096 * 3, 4096 * 2).decode("ascii")
        self.assertIn("if=/dev/nflasha2", job)
        self.assertIn(f"of={RAM}/chunk", job)
        self.assertIn("skip=3 count=2", job)
        self.assertIn(f"printf END >>{RAM}/chunk", job)
        self.assertNotIn("of=/dev", job)

    def test_rejects_excluded_unknown_unaligned_and_out_of_range(self):
        for args in (("nflasha23", RAM, 0, 4096), ("nflasha99", RAM, 0, 4096), ("nflasha2", RAM, 1, 4096),
                     ("nflasha2", RAM, 0, 4097), ("nflasha2", RAM, 0, dump.CHUNK_BYTES + 4096),
                     ("nflasha13", RAM, 4194304, 4096), ("nflasha2", RAM, -4096, 4096)):
            with self.assertRaises(ValueError):
                dump.chunk_job(*args)

    def test_default_partitions_are_the_ten_missing_from_the_update(self):
        self.assertEqual(
            set(dump.DEFAULT_PARTITIONS) - {"nflasha7"},
            {"nflasha1", "nflasha2", "nflasha4", "nflasha6", "nflasha10", "nflasha11",
             "nflasha12", "nflasha13", "nflasha18", "nflasha22"})
        self.assertNotIn("nflasha23", dump.PARTITIONS)

    def test_partition_sizes_match_live_proc_partitions_blocks(self):
        live_kib = {"nflasha1": 24512, "nflasha2": 20480, "nflasha4": 12288, "nflasha6": 25600,
                    "nflasha7": 16384, "nflasha10": 12288, "nflasha11": 307200, "nflasha12": 40960}
        for name, kib in live_kib.items():
            self.assertEqual(dump.PARTITIONS[name], kib * 1024, name)


class ParseInfoTests(unittest.TestCase):
    def test_memfree_and_partition_sizes(self):
        text = ("MemFree:         15652 kB\nmajor minor  #blocks  name\n\n"
                " 252        1      24512 pnflasha1\n 252        7      16384 pnflasha7\n"
                "   1        0       8192 ram0\n")
        info = dump.parse_info(text)
        self.assertEqual(info["memfree_kib"], 15652)
        self.assertEqual(info["sizes"], {"nflasha1": 24512 * 1024, "nflasha7": 16384 * 1024})


class ChannelTests(unittest.TestCase):
    def test_read_uses_the_traversal_framing_and_rejects_unsafe_paths(self):
        device = FakeDevice([(0, b"abc")])
        channel = dump.Channel(device, [])
        self.assertEqual(channel.read("/etc/hosts"), b"abc")
        pfunc, body = device.sent[0]
        self.assertEqual(pfunc, 0x40)
        self.assertEqual(body, struct.pack("<HH", 0x1001, 8) + b"\x01\x00\x00/../etc/hosts\0")
        for bad in ("etc/hosts", "/etc/../shadow", "/tmp/a b", "/etc/\n"):
            with self.assertRaises(ValueError):
                channel.read(bad)

    def test_only_read_and_upload_commands_exist(self):
        channel = dump.Channel(FakeDevice([]), [])
        for code in (1, 2, 3, 4, 5, 6, 9, 14, 18):  # mode, mount, format, start, debug, firmup, delete, factory
            with self.assertRaises(ValueError):
                channel.exchange(code, b"")

    def test_upload_is_limited_to_owned_files(self):
        channel = dump.Channel(FakeDevice([]), [])
        for path in ("/tmp/other", "/setting/x", "/tmp_wbshd/a6700-1/input", RAM + "/chunk", "/dev/nflasha1"):
            with self.assertRaises(ValueError):
                channel.write(path, b"x", RAM)
        with self.assertRaises(ValueError):
            channel.write(f"{RAM}/input", b"x", None)
        with self.assertRaises(ValueError):
            channel.write(dump.RUN_SH, b"", RAM)

    def test_ram_files_are_addressed_through_the_fixed_traversal_prefix(self):
        device = FakeDevice([(0, b""), (0, b"")])
        dump.Channel(device, []).write(f"{RAM}/input", b"job", RAM)
        self.assertEqual(device.sent[0][1][4:], b"\0/tmp/jiritsu/../.." + f"{RAM}/input".encode() + b"\0")
        self.assertEqual(device.sent[1][1][4:], b"\x00\xff\x00job")

    def test_status_is_surfaced_and_transient_reads_retry(self):
        device = FakeDevice([(0x10000006, b""), (0x10000008, b""), (0, b"ok")])
        with mock.patch.object(dump.time, "sleep"):
            self.assertEqual(dump.Channel(device, []).read_retry("/etc/hosts"), b"ok")
        with self.assertRaises(dump.JiritsuStatus):
            dump.Channel(FakeDevice([(0x10000001, b"")]), []).read_retry("/etc/hosts")


class FakeChannel:
    """Camera stand-in: serves a partition image the way the worker and GET_FILE would."""

    def __init__(self, image, fail_reads=0):
        self.image = image
        self.fail_reads = fail_reads
        self.current = None

    def read_retry(self, path):
        assert path.endswith("/chunk")
        if self.fail_reads:
            self.fail_reads -= 1
            raise dump.JiritsuStatus(0x10000006)
        return self.current + b"END"


class CaptureTests(unittest.TestCase):
    def capture(self, image, *, fail_reads=0, name="nflasha13"):
        fake = FakeChannel(image, fail_reads)

        def run_job(channel, ram_dir, job, **_):
            text = job.decode("ascii")
            skip = int(text.split("skip=")[1].split()[0])
            count = int(text.split("count=")[1].split()[0])
            fake.current = image[skip * 4096:(skip + count) * 4096]
            return (f"{count}+0 records in\n{count}+0 records out\n"
                    f"{hashlib.sha1(fake.current).hexdigest()}  {ram_dir}/chunk\n__RC__:0\n__JOB__:1\n")

        dump.STEP_STATE["bytes"] = dump.CHUNK_BYTES
        report = {"partitions": {}}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(dump, "run_job", run_job), \
                mock.patch.dict(dump.PARTITIONS, {name: len(image)}):
            dump.capture_partition(fake, RAM, name, len(image), Path(directory), report, None)
            return report, (Path(directory) / f"{name}.bin").read_bytes()

    def test_chunks_are_verified_unpadded_and_reassembled(self):
        image = bytes(range(256)) * (16 * dump.CHUNK_BYTES // 256 // 4) + b"\x7f" * 8192
        report, data = self.capture(image[: 3 * dump.CHUNK_BYTES // 2])
        self.assertEqual(data, image[: 3 * dump.CHUNK_BYTES // 2])
        self.assertEqual(report["partitions"]["nflasha13"]["sha256"], hashlib.sha256(data).hexdigest())

    def test_zero_chunks_are_not_transferred(self):
        report, data = self.capture(bytes(dump.CHUNK_BYTES))
        self.assertEqual(data, bytes(dump.CHUNK_BYTES))
        self.assertEqual(report["partitions"]["nflasha13"]["zero_chunks"], 1)
        self.assertEqual(report["partitions"]["nflasha13"]["fetched_chunks"], 0)

    def test_persistent_read_failure_halves_the_chunk_then_stops_at_the_minimum(self):
        image = b"\x55" * dump.CHUNK_BYTES
        report, data = self.capture(image, fail_reads=2)
        self.assertEqual(data, image)
        self.assertEqual(len(report["step_reductions"]), 2)
        self.assertEqual(report["partitions"]["nflasha13"]["chunk_bytes_at_end"], dump.CHUNK_BYTES // 4)

    def test_missing_pad_is_refused(self):
        image = b"\x55" * 8192
        fake = FakeChannel(image)
        fake.read_retry = lambda path: image  # no END pad
        dump.STEP_STATE["bytes"] = dump.CHUNK_BYTES

        def run_job(channel, ram_dir, job, **_):
            return ("2+0 records in\n2+0 records out\n"
                    f"{hashlib.sha1(image).hexdigest()}  {ram_dir}/chunk\n__RC__:0\n__JOB__:1\n")

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(dump, "run_job", run_job), \
                mock.patch.dict(dump.PARTITIONS, {"nflasha13": 8192}):
            with self.assertRaisesRegex(RuntimeError, "pad missing"):
                dump.capture_partition(fake, RAM, "nflasha13", 8192, Path(directory), {"partitions": {}}, None)


class LoaderSourceTests(unittest.TestCase):
    def test_loader_source_is_freestanding_and_non_persistent(self):
        source = (Path(__file__).resolve().parents[1] / "tools" / "native_camera"
                  / "a6700_jiritsu_loader.S").read_text(encoding="utf-8")
        self.assertIn("__NR_clone", source)
        self.assertIn("exit_group", source)
        for forbidden in ("/setting", "ld.so.preload", "mount", "nflasha"):
            self.assertNotIn(forbidden, source.replace("Nothing is written to\n * /setting or /etc/ld.so.preload", ""))

    def test_cli_refuses_the_camera_without_confirmation(self):
        self.assertEqual(dump.main(["--output-dir", "x", "--loader", "x", "--pmca-source", "x"]), 2)


if __name__ == "__main__":
    unittest.main()
