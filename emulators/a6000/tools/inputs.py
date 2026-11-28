#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Local-input manifest for the sony-a6000 emulator (not part of voxivoid/a6000-qemu,
# which the scripts around it come from).
"""Record and check the local inputs of the A6000 emulator.

``inputs.local.json`` (git-ignored) holds the paths and SHA-256 of the files you
supply. Sources are only ever opened for reading. A mismatch is an error: the
recorded hashes are never refreshed to silence it.

    inputs.py init (--dump FILE | --nand FILE) --fw DIR [--data DIR]
    inputs.py check [--nand]
    inputs.py record-nand FILE
    inputs.py get dump|nand|fw_dir|data_dir
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

EMULATOR = Path(__file__).resolve().parent.parent
F3 = "unpacked/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked_unpacked"
# Everything run.sh and provision/mkinitramfs.py read from the updater, except
# the rootfs libraries (nflasha7).
INITRD = f"{F3}/initrd.img_unpacked"
FW_FILES = (f"{F3}/vmlinux.bin", f"{F3}/initrd.img") + tuple(
    f"{INITRD}/kmod/{name}.ko" for name in ("devno", "dmac", "ldec", "lld", "nand")
) + ("unpacked/firmware.tar_unpacked/0700_part_image/dev/nflasha7_unpacked_unpacked/bin/busybox",)
DATA_FILES = ("handoff_80021000.bin", "submicro_frames.bin")
# Scripts whose bytes decide what provisioning writes.
PROVISIONERS = ("provision.sh", "provision/init.sh", "provision/a6kcopy.c",
                "provision/mkinitramfs.py")
NAND_SIZE = 512 * 1024**2 + 1024**2 + 16 * 1024  # cxd90014-nand backing file


class InputError(Exception):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
    except OSError as error:
        raise InputError(f"cannot read {path}: {error.strerror}") from error
    return digest.hexdigest()


def entry(path: Path) -> dict:
    path = path.expanduser().resolve()
    digest = sha256(path)
    return {"path": str(path), "sha256": digest, "size": path.stat().st_size}


def verify(path: Path, expected: str) -> None:
    if sha256(path) != expected:
        raise InputError(f"SHA-256 mismatch: {path}")


def load(config: Path) -> dict:
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InputError(f"{config} missing; run: inputs.py init") from error
    except ValueError as error:
        raise InputError(f"{config} is not valid JSON") from error
    if data.get("schema_version") != 1:
        raise InputError(f"{config}: unsupported schema_version")
    return data


def save(config: Path, data: dict) -> None:
    temporary = config.with_name(config.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, config)


def provisioners() -> dict[str, str]:
    return {name: sha256(EMULATOR / name) for name in PROVISIONERS}


def adopt_nand(source: Path, target: Path) -> dict:
    """Copy an already provisioned NAND to the read-only base."""
    source = source.expanduser().resolve()
    if source.stat().st_size != NAND_SIZE:
        raise InputError(f"{source} is not a provisioned NAND file (wrong size)")
    digest = sha256(source)
    if target.exists():
        if sha256(target) != digest:
            raise InputError(f"{target} exists and differs; move it away first")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        if subprocess.run(["cp", "--reflink=auto", "--", source, temporary]).returncode:
            shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    target.chmod(0o444)
    return {"path": str(target.resolve()), "sha256": digest,
            "adopted_from": str(source)}


def init(args: argparse.Namespace) -> None:
    if args.config.exists() and not args.force:
        raise InputError(f"{args.config} exists; --force replaces it")
    fw = args.fw.expanduser().resolve()
    data_dir = (args.data or EMULATOR / "data").expanduser().resolve()
    config = {
        "schema_version": 1,
        "fw_dir": str(fw),
        "fw": {name: sha256(fw / name) for name in FW_FILES},
        "data_dir": str(data_dir),
        "data": {name: sha256(data_dir / name) for name in DATA_FILES},
    }
    if args.dump:
        config["dump"] = entry(args.dump)
    else:
        config["nand"] = adopt_nand(args.nand, args.base)
    save(args.config, config)
    print(f"wrote {args.config}")


def check(args: argparse.Namespace) -> None:
    config = load(args.config)
    for name, digest in config["fw"].items():
        verify(Path(config["fw_dir"]) / name, digest)
    for name, digest in config["data"].items():
        verify(Path(config["data_dir"]) / name, digest)
    if args.nand:
        if "nand" not in config:
            raise InputError("no provisioned NAND recorded; run ./provision.sh")
        verify(Path(config["nand"]["path"]), config["nand"]["sha256"])
    elif "dump" in config:
        verify(Path(config["dump"]["path"]), config["dump"]["sha256"])


def record_nand(args: argparse.Namespace) -> None:
    config = load(args.config)
    config["nand"] = entry(args.file)
    config["nand"]["provisioned_by"] = provisioners()
    if "dump" in config:
        config["nand"]["dump_sha256"] = config["dump"]["sha256"]
    save(args.config, config)


def get(args: argparse.Namespace) -> None:
    config = load(args.config)
    if args.key in ("dump", "nand"):
        value = config.get(args.key, {}).get("path")
    else:
        value = config.get(args.key)
    if not value:
        raise InputError(f"{args.config}: no {args.key} recorded")
    print(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=EMULATOR / "inputs.local.json")
    commands = parser.add_subparsers(required=True)
    sub = commands.add_parser("init")
    source = sub.add_mutually_exclusive_group(required=True)
    source.add_argument("--dump", type=Path, help="NAND dump of your own camera")
    source.add_argument("--nand", type=Path, help="already provisioned NAND file")
    sub.add_argument("--fw", type=Path, required=True, help="unpacked updater (A6K_FW)")
    sub.add_argument("--data", type=Path, help="tools/mkdata.py output")
    sub.add_argument("--base", type=Path, default=EMULATOR / "state/a6000.nand")
    sub.add_argument("--force", action="store_true")
    sub.set_defaults(run=init)
    sub = commands.add_parser("check")
    sub.add_argument("--nand", action="store_true")
    sub.set_defaults(run=check)
    sub = commands.add_parser("record-nand")
    sub.add_argument("file", type=Path)
    sub.set_defaults(run=record_nand)
    sub = commands.add_parser("get")
    sub.add_argument("key", choices=("dump", "nand", "fw_dir", "data_dir"))
    sub.set_defaults(run=get)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except (InputError, OSError) as error:
        print(f"inputs: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
