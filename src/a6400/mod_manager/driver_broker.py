"""Session-scoped elevated driver broker: one UAC prompt, guaranteed driver restore.

The unelevated GUI owns a named-pipe ``multiprocessing.connection.Listener`` with a
random per-session ``authkey`` (mutual HMAC challenge). It starts this module's
``run_broker`` elevated (``--driver-broker``), which connects back and serves a
fixed command set:

* ``ping``
* ``bind(pid)``: ``usb_driver.install_winusb`` (allowlist + helper hash check)
* ``restore(pid)`` / ``restore_all``: ``usb_driver.restore_windows_driver``
* ``quit``

Mass-storage PIDs bound during the session are restored when the GUI asks, when
the pipe closes (GUI exit or crash), and when the broker exits for any reason.
The broker never runs arbitrary programs or touches non-allowlisted devices.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import sys
import threading
from typing import Any, Callable

from . import usb_driver

COMMANDS = ("ping", "bind", "restore", "restore_all", "quit")
DEFAULT_CONNECT_TIMEOUT_S = 120.0  # includes the time the user spends on the UAC prompt


class BrokerUnavailable(RuntimeError):
    """The elevated broker could not be started (declined UAC, missing helper, timeout)."""


class BrokerOps:
    """Real elevated operations; tests substitute fakes."""

    def __init__(self, data_dir: Path, progress: Callable[[str], None] | None = None):
        self.data_dir = Path(data_dir)
        self.progress = progress

    def bind(self, pid: int) -> dict[str, Any]:
        return usb_driver.install_winusb(
            pid, data_dir=self.data_dir, runner=usb_driver.run_direct, progress_cb=self.progress
        )

    def restore(self, pid: int | None) -> dict[str, Any]:
        return usb_driver.restore_windows_driver(pid, data_dir=self.data_dir, progress_cb=self.progress)


def serve(conn: Any, ops: Any) -> list[int]:
    """Serve commands on an authenticated connection; restore session MSC binds on exit.

    Returns the PIDs that were restored on the way out (for tests and receipts).
    """
    bound_msc: set[int] = set()
    restored: list[int] = []
    try:
        while True:
            try:
                msg = conn.recv()
            except (EOFError, OSError):
                break
            if not isinstance(msg, dict) or msg.get("cmd") not in COMMANDS:
                conn.send({"ok": False, "error": f"unsupported command: {msg!r}"[:200]})
                continue
            cmd = msg["cmd"]
            if cmd == "quit":
                conn.send({"ok": True})
                break
            try:
                if cmd == "ping":
                    result: Any = {"pid": os.getpid()}
                elif cmd == "bind":
                    pid = int(msg["pid"])
                    result = ops.bind(pid)
                    if pid not in usb_driver.SENSER_PIDS and result.get("status") in ("bound", "installed_not_rebound"):
                        bound_msc.add(pid)
                elif cmd == "restore":
                    pid = int(msg["pid"])
                    result = ops.restore(pid)
                    bound_msc.discard(pid)
                    restored.append(pid)
                else:  # restore_all
                    result = ops.restore(None)
                    restored.extend(sorted(bound_msc))
                    bound_msc.clear()
                conn.send({"ok": True, "result": result})
            except Exception as exc:  # report, keep serving
                conn.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        for pid in sorted(bound_msc):
            try:
                ops.restore(pid)
                restored.append(pid)
            except Exception:
                pass  # the startup check in the GUI offers a manual restore
    return restored


def run_broker(pipe: str, token: str, data_dir: Path) -> int:
    """Entry point for ``--driver-broker`` (runs elevated)."""
    from multiprocessing.connection import Client

    conn = Client(pipe, family="AF_PIPE", authkey=bytes.fromhex(token))
    try:
        serve(conn, BrokerOps(data_dir))
    finally:
        conn.close()
    return 0


def _default_launcher(pipe: str, token: str, data_dir: Path) -> None:
    """Start this app elevated in ``--driver-broker`` mode (one UAC prompt)."""
    from .tools_bundle import is_frozen, repo_root

    tail = ["--driver-broker", "--pipe", pipe, "--token", token, "--data-dir", str(data_dir)]
    if is_frozen():
        exe, args, cwd = Path(sys.executable), tail, Path(sys.executable).parent
    else:
        exe, args, cwd = Path(sys.executable), ["-m", "tools.mod_manager_gui", *tail], repo_root(None)
    usb_driver.shell_execute_elevated(exe, args, cwd=cwd, wait=False)


class BrokerClient:
    """GUI-side handle to one elevated broker for the whole app session."""

    def __init__(
        self,
        data_dir: Path,
        *,
        launcher: Callable[[str, str, Path], None] | None = None,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    ):
        self.data_dir = Path(data_dir)
        self.launcher = launcher or _default_launcher
        self.connect_timeout_s = connect_timeout_s
        self._conn: Any = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def _start(self) -> None:
        from multiprocessing.connection import Listener

        token = secrets.token_hex(32)
        pipe = rf"\\.\pipe\bionzx-driver-{os.getpid()}-{secrets.token_hex(8)}"
        listener = Listener(pipe, family="AF_PIPE", authkey=bytes.fromhex(token))
        accepted: dict[str, Any] = {}

        def _accept() -> None:
            try:
                accepted["conn"] = listener.accept()
            except Exception as exc:  # auth failure or listener closed
                accepted["error"] = exc

        thread = threading.Thread(target=_accept, name="driver-broker-accept", daemon=True)
        thread.start()
        try:
            self.launcher(pipe, token, self.data_dir)
        except Exception as exc:
            listener.close()
            raise BrokerUnavailable(str(exc)) from exc
        thread.join(self.connect_timeout_s)
        if "conn" not in accepted:
            listener.close()
            detail = accepted.get("error") or f"no connection within {self.connect_timeout_s:.0f}s"
            raise BrokerUnavailable(f"Driver helper did not start: {detail}")
        listener.close()
        conn = accepted["conn"]
        conn.send({"cmd": "ping"})  # called with self._lock held, so talk to conn directly
        if not conn.recv().get("ok"):
            conn.close()
            raise BrokerUnavailable("Driver helper failed its startup ping")
        self._conn = conn

    def call(self, cmd: str, **kwargs: Any) -> Any:
        if cmd not in COMMANDS:
            raise ValueError(f"unsupported broker command {cmd!r}")
        with self._lock:
            if self._conn is None:
                if cmd == "quit":
                    return None
                self._start()
            assert self._conn is not None
            try:
                self._conn.send({"cmd": cmd, **kwargs})
                reply = self._conn.recv()
            except (EOFError, OSError) as exc:
                self._conn = None
                raise BrokerUnavailable(f"Driver helper exited unexpectedly: {exc}") from exc
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error") or f"broker command {cmd} failed")
        return reply.get("result")

    def close(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.send({"cmd": "quit"})
            conn.recv()
        except Exception:
            pass
        finally:
            conn.close()
