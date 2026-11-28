"""Practice mode: a simulated camera so people can try the Mod Manager with no camera attached.

Nothing here touches USB, drivers, SD cards or the network. The real, pure
``CameraService.plan_install`` builds the plan, so the screens, confirmations and
progress steps are the same ones a real install shows; only the camera side is
simulated (detection, install steps, power-cycle wait, ledger and restore).
"""
from __future__ import annotations

from datetime import datetime
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .camera_service import CameraService

PRACTICE_MODEL = "ILCE-6400"
PRACTICE_FIRMWARE = "2.00"
PRACTICE_IDENTITY = {"model_name": PRACTICE_MODEL, "firmware_version": PRACTICE_FIRMWARE, "serial": "PRACTICE"}


class PracticeTransport:
    """Placeholder transport: every live-camera call is refused, so nothing real can run."""

    def __getattr__(self, name: str) -> Any:
        # AttributeError (not RuntimeError) so optional ``getattr(transport, name, None)`` probes
        # see "not supported" while direct calls still fail loudly.
        raise AttributeError(f"Practice mode has no real camera (blocked transport call {name!r}).")


class PracticeCameraService(CameraService):
    """Simulated camera behind the real planner. ``step_delay_s`` paces the fake progress."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        data_dir: Path | None = None,
        appear_after: int = 1,
        step_delay_s: float = 1.2,
        sleep: Callable[[float], None] = time.sleep,
    ):
        super().__init__(root=root, data_dir=data_dir, transport=PracticeTransport())
        self._lock = threading.Lock()
        self._detect_calls = 0
        self._appear_after = max(0, int(appear_after))
        self._debugger_installed = False
        self._staged: list[dict[str, Any]] = []
        self._tweaks: dict[str, bool] = {}
        self._ledger: list[dict[str, Any]] = []
        self._step_delay_s = step_delay_s
        self._sleep = sleep

    # -- detection -------------------------------------------------------
    def detect_camera(self, *, mlv2_status: dict[str, object] | None = None) -> dict[str, object]:
        with self._lock:
            self._detect_calls += 1
            visible = self._detect_calls > self._appear_after
            staged = [dict(m) for m in self._staged]
            debugger = self._debugger_installed
        if not visible:
            return {
                "connected": False, "state": "offline", "transport": "none", "debugger_active": False,
                "model_name": None, "firmware_version": None, "mlv2_hook_state": "none", "staged_mods": [],
                "practice": True,
                "detail": "Practice mode: the pretend camera is being plugged in...",
            }
        return {
            "connected": True,
            "state": "debugger_ready" if debugger else "msc_verified",
            "transport": "msc",
            "debugger_active": debugger,
            "model_name": PRACTICE_MODEL,
            "model_code": "PRACTICE",
            "firmware_version": PRACTICE_FIRMWARE,
            "qualification": "verified",
            "bootstrap_mode": "senser",
            "mlv2_hook_state": "v2" if staged else "none",
            "staged_mods": staged,
            "practice": True,
            "detail": "Practice mode: pretend camera (nothing real is connected).",
        }

    def read_live_tweaks(self) -> dict[str, object]:
        with self._lock:
            return {"tweaks": [{"id": k, "enabled": v} for k, v in self._tweaks.items()], "practice": True}

    # -- install ---------------------------------------------------------
    def _pause(self, cancel_event: threading.Event | None) -> None:
        self._sleep(self._step_delay_s)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Cancelled. Practice mode: nothing was changed.")

    def execute_install(
        self,
        plan: dict[str, object],
        catalog: dict[str, object],
        mlv2_status: dict[str, object],
        *,
        confirmed_permanent: bool = False,
        progress_cb: Callable[[dict[str, object]], None] | None = None,
        wait_for_power_cycle_cb: Any = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, object]:
        del catalog, mlv2_status, wait_for_power_cycle_cb
        if plan.get("blocking_errors"):
            raise RuntimeError("; ".join(str(e) for e in plan["blocking_errors"]))
        if plan.get("requires_confirmation") and not confirmed_permanent:
            raise RuntimeError("Explicit user confirmation is required before permanent writes or risky mods.")

        def emit(step: str, status: str, message: str) -> None:
            if progress_cb:
                progress_cb({"step": step, "status": status, "message": message})

        group = datetime.now().isoformat(timespec="seconds")
        emit("verify", "running", "Checking the mod files...")
        self._pause(cancel_event)
        emit("verify", "done", "Mod files OK.")

        for change in plan.get("tweak_changes") or []:
            emit("tweaks", "running", f"(Practice) Saving '{change['title']}' in the camera...")
            self._pause(cancel_event)
            with self._lock:
                before = self._tweaks.get(str(change["id"]), False)
                self._tweaks[str(change["id"])] = bool(change["enabled"])
                self._ledger.append({"id": len(self._ledger) + 1, "group": group, "kind": "tweak", "at": group,
                                     "label": f"{change['title']}: {'on' if change['enabled'] else 'off'}",
                                     "tweak_id": str(change["id"]), "before": before})
        if plan.get("tweak_changes"):
            emit("tweaks", "done", "(Practice) Camera settings saved.")

        if plan.get("needs_debugger_bootstrap"):
            emit("bootstrap", "running", "(Practice) Preparing the camera for mods (first time only)...")
            self._pause(cancel_event)
            emit("bootstrap", "waiting_power_cycle",
                 "Turn the camera OFF and back ON now. (Practice mode continues by itself.)")
            self._pause(cancel_event)
            self._pause(cancel_event)
            with self._lock:
                self._debugger_installed = True
                self._ledger.append({"id": len(self._ledger) + 1, "group": group, "kind": "debugger", "at": group,
                                     "label": "Mod helper installed on the camera (first-time setup)"})
            emit("bootstrap", "done", "(Practice) Camera reconnected and ready.")

        if plan.get("needs_runtime_sync"):
            emit("mlv2_sync", "running", "(Practice) Sending the mods to the camera's memory...")
            self._pause(cancel_event)
            with self._lock:
                keep = {str(m["filename"]).lower() for m in plan.get("mods_to_keep") or []}
                self._staged = [m for m in self._staged if str(m["filename"]).lower() in keep]
                for item in [*(plan.get("mods_to_add") or []), *(plan.get("mods_to_update") or [])]:
                    self._staged.append({"filename": item["filename"], "sha256": item.get("sha256"),
                                         "remote_path": f"/practice/{item['filename']}"})
            emit("mlv2_sync", "done", "(Practice) Mods are running.")

        emit("receipt", "done", "Practice run finished. Nothing on any real camera was changed.")
        return {
            "operation": "one_click_install",
            "timestamp": group,
            "camera_model": PRACTICE_MODEL,
            "status": "success",
            "practice": True,
            "message": "Done! On the camera, press MENU and open the Mods tab. (This was practice mode: "
                       "nothing real was changed.)",
            "tweaks": [{"id": t["id"], "enabled": t["enabled"]} for t in plan.get("tweak_changes") or []],
        }

    # -- ledger / restore -----------------------------------------------
    def get_ledger(self) -> dict[str, object]:
        with self._lock:
            active = [dict(e) for e in self._ledger]
        return {
            "camera": dict(PRACTICE_IDENTITY),
            "path": None,
            "has_snapshot": bool(active),
            "active": [{k: e[k] for k in ("id", "group", "kind", "at", "label")} for e in active],
            "last_group": active[-1]["group"] if active else None,
            "reverted_count": 0,
            "practice": True,
        }

    def restore_stock(
        self,
        *,
        last_group_only: bool = False,
        progress_cb: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        def emit(message: str) -> None:
            if progress_cb:
                progress_cb({"step": "restore", "status": "running", "message": message})

        with self._lock:
            group = self._ledger[-1]["group"] if (self._ledger and last_group_only) else None
            undo = [e for e in self._ledger if group is None or e["group"] == group]
        receipt: dict[str, object] = {
            "operation": "undo_last_install" if last_group_only else "restore_stock",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "camera": dict(PRACTICE_IDENTITY),
            "status": "nothing_to_restore" if not undo else "restored",
            "receipt_path": "(practice mode: no receipt)",
            "practice": True,
        }
        if not undo:
            return receipt
        emit("(Practice) Putting the camera back to how it was...")
        self._sleep(self._step_delay_s)
        with self._lock:
            for e in reversed(undo):
                if e["kind"] == "tweak":
                    self._tweaks[e["tweak_id"]] = bool(e["before"])
                elif e["kind"] == "debugger":
                    self._debugger_installed = False
                    self._staged = []
            ids = {e["id"] for e in undo}
            self._ledger = [e for e in self._ledger if e["id"] not in ids]
        return receipt

    # -- refused live-only operations ------------------------------------
    def check_debugger_compatibility(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        raise RuntimeError("Not available in practice mode.")

    def collect_onboarding_bundle(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        raise RuntimeError("Not available in practice mode.")

    def list_receipts(self, limit: int = 15) -> list[dict[str, object]]:
        return []
