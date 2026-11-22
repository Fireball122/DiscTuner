"""Running external commands: hidden windows, streamed output, cancellation."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(Exception):
    """The user stopped the operation."""


class CancelToken:
    """Shared between the UI thread and the worker; stops the running child process."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled

    def sleep(self, seconds: float) -> None:
        """Wait, waking immediately (and raising Cancelled) if the token is cancelled."""
        if self._event.wait(seconds):
            raise Cancelled

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()

    def _attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._proc = proc
            if self._event.is_set():
                proc.terminate()

    def _detach(self) -> None:
        with self._lock:
            self._proc = None


@dataclass(frozen=True)
class Completed:
    returncode: int
    lines: list[str]
    stdout: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self, count: int = 6) -> str:
        """The last few non-empty lines: where a failing tool says why."""
        return "\n".join([line for line in self.lines if line.strip()][-count:])


def python_exe() -> str:
    """A console Python for child processes: pythonw has no stdout to capture."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return str(exe)


def tool_env(discprobe_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(discprobe_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"  # progress lines must arrive while the command runs
    env["DISCPROBE_PLAIN"] = "1"
    return env


def powershell(script_args: Sequence[str]) -> list[str]:
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *script_args]


def run(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = 120,
) -> Completed:
    """Run to completion without a console window and capture the output."""
    proc = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    return Completed(proc.returncode, (proc.stdout + proc.stderr).splitlines(), proc.stdout)


def stream(
    cmd: Sequence[str],
    token: CancelToken,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    on_line: Callable[[str], None] | None = None,
    on_tick: Callable[[], None] | None = None,
    tick_s: float = 1.0,
) -> Completed:
    """Run a command, feeding each output line to `on_line`.

    `on_tick` fires about every `tick_s` seconds even when the command is silent, so a UI
    can keep a clock running. Raises Cancelled if the token was cancelled.
    """
    token.raise_if_cancelled()
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    token._attach(proc)

    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.put(raw.rstrip("\r\n"))
        lines.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    collected: list[str] = []
    last_tick = time.monotonic()
    try:
        while True:
            try:
                item = lines.get(timeout=tick_s)
            except queue.Empty:
                pass
            else:
                if item is None:
                    break
                collected.append(item)
                if on_line:
                    on_line(item)
            if on_tick and time.monotonic() - last_tick >= tick_s:
                last_tick = time.monotonic()
                on_tick()
        returncode = proc.wait()
    finally:
        token._detach()
        if proc.poll() is None:
            proc.kill()
        reader.join(timeout=5)
        proc.stdout.close()
    token.raise_if_cancelled()
    return Completed(returncode, collected)
