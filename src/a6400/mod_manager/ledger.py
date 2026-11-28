"""Per-camera change ledger: every persistent camera write, its before state, and its reversal.

Layout: ``<data_dir>/cameras/<model-slug>/ledger.json``::

    {
      "camera": {"model_name": ..., "model_code": ..., "firmware_version": ...},
      "snapshot": {"taken": ..., "tweaks": {...} | null, "preload_b64": ..., "preload_existed": bool} | null,
      "entries": [
        {"id": 1, "group": "<install timestamp>", "kind": "pmca_tweak" | "debugger_install",
         "before": ..., "after": ..., "reverted": null | {"at": ..., "receipt": ...}}
      ]
    }

The snapshot is taken once, before the first persistent write the app makes on that camera,
and is never overwritten. Sony's MSC identity has no serial number, so the key is the model
name and model code; one ledger is shared by bodies of the same model on this PC.
"""
from __future__ import annotations

import base64
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

KIND_TWEAK = "pmca_tweak"
KIND_DEBUGGER = "debugger_install"
KINDS = (KIND_TWEAK, KIND_DEBUGGER)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-") or "unknown"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


class CameraLedger:
    def __init__(self, data_dir: Path, identity: dict[str, Any]):
        model = str(identity.get("model_name") or "unknown")
        code = str(identity.get("model_code") or "")
        self.key = _slug(f"{model}-{code}" if code else model)
        self.path = Path(data_dir) / "cameras" / self.key / "ledger.json"
        self.identity = {k: identity.get(k) for k in ("model_name", "model_code", "firmware_version")}
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data.setdefault("entries", [])
            data.setdefault("snapshot", None)
            return data
        return {"camera": self.identity, "snapshot": None, "entries": []}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    # -- snapshot -----------------------------------------------------------
    @property
    def snapshot(self) -> dict[str, Any] | None:
        return self.data.get("snapshot")

    def ensure_snapshot(
        self,
        *,
        tweaks: dict[str, bool] | None = None,
        preload: bytes | None = None,
        preload_existed: bool | None = None,
    ) -> None:
        """Record the pre-change state once; later calls only fill fields that were unknown."""
        snap = self.data.get("snapshot")
        if snap is None:
            snap = {"taken": datetime.now().isoformat(timespec="seconds"), "tweaks": None,
                    "preload_b64": None, "preload_existed": None}
            self.data["snapshot"] = snap
        if snap.get("tweaks") is None and tweaks is not None:
            snap["tweaks"] = {str(k): bool(v) for k, v in tweaks.items()}
        if snap.get("preload_b64") is None and preload is not None:
            snap["preload_b64"] = b64(preload)
            snap["preload_existed"] = bool(preload_existed)
        self.save()

    # -- entries ------------------------------------------------------------
    def append(self, kind: str, group: str, before: Any, after: Any, **extra: Any) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"unknown ledger kind {kind!r}")
        entry = {
            "id": max((int(e["id"]) for e in self.data["entries"]), default=0) + 1,
            "group": group,
            "kind": kind,
            "at": datetime.now().isoformat(timespec="seconds"),
            "before": before,
            "after": after,
            "reverted": None,
            **extra,
        }
        self.data["entries"].append(entry)
        self.save()
        return entry

    def active(self, *, group: str | None = None) -> list[dict[str, Any]]:
        return [e for e in self.data["entries"] if e["reverted"] is None and (group is None or e["group"] == group)]

    def last_group(self) -> str | None:
        active = self.active()
        return active[-1]["group"] if active else None

    def mark_reverted(self, ids: list[int], receipt_path: str) -> None:
        stamp = datetime.now().isoformat(timespec="seconds")
        wanted = set(ids)
        for entry in self.data["entries"]:
            if entry["id"] in wanted and entry["reverted"] is None:
                entry["reverted"] = {"at": stamp, "receipt": receipt_path}
        self.save()

    def summary(self) -> dict[str, Any]:
        active = self.active()
        return {
            "camera": self.data.get("camera"),
            "path": str(self.path),
            "has_snapshot": self.snapshot is not None,
            "active": [
                {"id": e["id"], "group": e["group"], "kind": e["kind"], "at": e["at"],
                 "label": describe(e)} for e in active
            ],
            "last_group": self.last_group(),
            "reverted_count": len(self.data["entries"]) - len(active),
        }


def describe(entry: dict[str, Any]) -> str:
    if entry["kind"] == KIND_TWEAK:
        return f"Firmware tweak {entry.get('tweak_id')}: {entry['before']} -> {entry['after']}"
    if entry["kind"] == KIND_DEBUGGER:
        return "Native USB debugger installed to /setting (+ /etc/ld.so.preload)"
    return entry["kind"]
