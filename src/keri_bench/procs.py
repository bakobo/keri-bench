"""Start, kill, restart and stop role processes (test plan, Part B #8, and Faults: crash).

The launcher (``spawn``, ``Process`` and the parent-death hook) is copied from
bakobo/sedi-summit-nov26 harness/src/sedi_harness/topology.py:62-136 at commit
ce3b91edc3b32ef0b12c03ec90924c3b1dbc9fed, and adapted rather than imported so that repository
never becomes a dependency. The changes: the summit's table of per-process announcement checks
became a ``validate`` callable the caller supplies; an optional ``ready`` probe covers processes
that announce before their listener binds; the parent-death signal is SIGKILL rather than
SIGTERM, so a killed harness leaves nothing behind; signals go to the child's whole process
group; and logs are appended across restarts.

A process announces readiness by printing one JSON object on stdout. Its stderr goes to
``<run_dir>/<name>.log``.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import os
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import BenchError

E_STARTUP = "e.self.startup.harness.r"
E_UNKNOWN = "e.state.missing.process.f"
E_DUPLICATE = "e.state.conflict.process.f"

STARTUP_TIMEOUT = 120.0
"""Seconds a process may take to announce itself and pass its readiness probe."""

STOP_TIMEOUT = 15.0
"""Seconds a process has to exit after SIGTERM before it is sent SIGKILL."""

Validate = Callable[[dict], None]
Ready = Callable[[dict], Awaitable[None]]
OnEvent = Callable[[str, int, dict], None]


class StartupError(BenchError):
    """A process died, went silent, announced badly or never became ready."""

    def __init__(self, message: str) -> None:
        super().__init__(E_STARTUP, message)


_PR_SET_PDEATHSIG = 1


def _die_with_parent() -> None:
    """In the child, before exec: ask Linux for SIGKILL when the harness dies.

    Each child starts its own session so a terminal's Ctrl-C reaches the harness alone and
    teardown stays orderly. That same separation means a harness that is killed outright would
    orphan every role, and the next run would collide with them.
    """
    # Runs in the child between fork and exec, where coverage cannot see it; the killed-harness
    # test in tests/test_procs.py is what shows it works.
    ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)  # pragma: no cover


def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
    # The child leads its own session, so its pid is its process group id.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, sig)


async def _drain(stream: asyncio.StreamReader, log: Path) -> None:
    """Copy whatever a process writes to stdout after announcing into its log.

    Nothing else reads the pipe, and a process that fills it (about 64 KiB) would block on its
    next write: a stalled role that the harness would then measure as slow.
    """
    with open(log, "ab") as sink:
        while chunk := await stream.read(1 << 16):
            sink.write(chunk)
            sink.flush()


@dataclass
class Process:
    name: str
    proc: asyncio.subprocess.Process
    announced: dict
    log: Path
    drain: asyncio.Task | None = None
    watch: asyncio.Task | None = None

    @property
    def pid(self) -> int:
        return self.proc.pid

    async def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """SIGTERM the process group, then SIGKILL it if it has not exited within ``timeout``."""
        if self.proc.returncode is None:
            _signal_group(self.proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), timeout)
            except TimeoutError:
                await self.kill()

    async def kill(self) -> None:
        """SIGKILL the process group and wait until the process is reaped."""
        if self.proc.returncode is None:
            _signal_group(self.proc, signal.SIGKILL)
        await self.proc.wait()


async def spawn(name: str, command: tuple[str, ...], args: tuple[str, ...] = (), *,
                run_dir: Path, env: dict | None = None, timeout: float = STARTUP_TIMEOUT,
                validate: Validate | None = None, ready: Ready | None = None,
                attempt: int = 1) -> Process:
    """Start ``command`` with ``args``, wait for its one JSON line, then for ``ready``.

    The announcement and the readiness probe share one ``timeout``. On any failure the process
    is stopped and ``StartupError`` is raised.
    """
    log = Path(run_dir) / f"{name}.log"
    with open(log, "ab") as sink:
        sink.write(f"--- {name} start {attempt} ---\n".encode())
        sink.flush()
        proc = await asyncio.create_subprocess_exec(
            *command, *args, stdout=asyncio.subprocess.PIPE,
            stderr=sink, env={**os.environ, **(env or {})}, start_new_session=True,
            preexec_fn=_die_with_parent)
    deadline = asyncio.get_running_loop().time() + timeout
    process = Process(name, proc, {}, log)
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout)
    except TimeoutError:
        line = b""
    if not line:
        await process.stop()
        tail = log.read_text(errors="replace")[-2000:]
        raise StartupError(f"The {name} process did not announce itself within {timeout} "
                           f"seconds. Its log ends:\n{tail}")
    said = line.decode(errors="replace").strip()[:300]
    try:
        announced = json.loads(line)
        if not isinstance(announced, dict):
            raise ValueError("not an object")
    except ValueError:
        await process.stop()
        raise StartupError(f"The {name} process announced something that is not a JSON object, "
                           f"so it was stopped. It announced: {said!r}") from None
    try:
        if validate is not None:
            validate(announced)
    except ValueError as wrong:
        await process.stop()
        raise StartupError(f"The {name} process made a malformed announcement ({wrong}), so it "
                           f"was stopped. It announced: {announced!r}"[:600]) from None
    process.announced = announced
    process.drain = asyncio.create_task(_drain(proc.stdout, log))
    if ready is not None:
        try:
            async with asyncio.timeout_at(deadline):
                await ready(announced)
        except TimeoutError:
            await process.stop()
            raise StartupError(f"The {name} process announced itself but was not ready within "
                               f"{timeout} seconds of starting, so it was stopped.") from None
        except Exception as wrong:
            await process.stop()
            raise StartupError(f"The {name} process's readiness probe failed ({wrong}), so it "
                               f"was stopped.") from None
    return process


@dataclass
class _Spec:
    command: tuple[str, ...]
    args: tuple[str, ...]
    env: dict | None
    validate: Validate | None
    ready: Ready | None
    timeout: float
    starts: int = 0
    process: Process | None = None


class ProcessController:
    """The harness's role processes by name, with what it takes to restart each identically.

    ``on_event(name, t, attrs)`` receives ``process_killed`` once a killed process is reaped,
    ``process_ready`` once a start or restart passes readiness, and ``process_exited`` when a
    process exits that the controller did not kill, restart or stop, with ``t`` from ``clock``
    (this.i 9zgpyjxt). Every such exit is also kept in ``unexpected_exits``.
    """

    def __init__(self, run_dir: Path, *, clock: Callable[[], int] = time.monotonic_ns,
                 on_event: OnEvent | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.clock = clock
        self.on_event = on_event
        self._specs: dict[str, _Spec] = {}
        self._intended: set[int] = set()
        self.unexpected_exits: list[dict[str, Any]] = []

    def _emit(self, name: str, t: int, attrs: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(name, t, attrs)

    def _spec(self, name: str) -> _Spec:
        try:
            return self._specs[name]
        except KeyError:
            raise BenchError(E_UNKNOWN, f"There is no process named {name!r}. The known "
                             f"processes are: {sorted(self._specs)}.") from None

    def names(self) -> list[str]:
        """Every name a process has been started under, in start order."""
        return list(self._specs)

    def process(self, name: str) -> Process:
        """The most recent process started under ``name``."""
        return self._spec(name).process  # stored only once its first process has started

    async def start(self, name: str, command: tuple[str, ...], args: tuple[str, ...] = (), *,
                    env: dict | None = None, validate: Validate | None = None,
                    ready: Ready | None = None, timeout: float = STARTUP_TIMEOUT) -> Process:
        """Spawn a process and remember how, so ``restart`` can bring it back identically."""
        old = self._specs.get(name)
        if old is not None and old.process.proc.returncode is None:
            raise BenchError(E_DUPLICATE, f"A process named {name!r} is already running, with "
                             f"pid {old.process.pid}. Kill it or use another name.")
        spec = _Spec(tuple(command), tuple(args), env, validate, ready, timeout,
                     starts=old.starts if old is not None else 0)
        return await self._launch(name, spec, restart=False)

    async def _launch(self, name: str, spec: _Spec, *, restart: bool) -> Process:
        spec.starts += 1
        began = self.clock()
        process = await spawn(name, spec.command, spec.args, run_dir=self.run_dir, env=spec.env,
                              timeout=spec.timeout, validate=spec.validate, ready=spec.ready,
                              attempt=spec.starts)
        t = self.clock()
        spec.process = process
        self._specs[name] = spec
        process.watch = asyncio.create_task(self._watch(name, process))
        self._emit("process_ready", t, {"process": name, "restart": restart,
                                        "announced": process.announced, "ready_ns": t - began})
        return process

    async def _watch(self, name: str, process: Process) -> None:
        """Wait for the process to exit, and record the exit if the controller did not cause it.

        A role that dies mid-run leaves the stack short of it; without this the run's numbers
        would read as though it were whole."""
        code = await process.proc.wait()
        if process.pid in self._intended:
            return
        attrs = {"process": name, "pid": process.pid, "code": code,
                 "at": datetime.now(UTC).isoformat()}
        self.unexpected_exits.append(attrs)
        self._emit("process_exited", self.clock(), attrs)

    async def kill(self, name: str) -> None:
        """SIGKILL the named process's group, wait until it is reaped, and record it."""
        process = self.process(name)
        self._intended.add(process.pid)
        await process.kill()
        self._emit("process_killed", self.clock(), {"process": name})

    async def restart(self, name: str) -> Process:
        """Respawn the named process from its stored spec, killing it first if it still runs."""
        spec = self._spec(name)
        if self.process(name).proc.returncode is None:
            await self.kill(name)
        return await self._launch(name, spec, restart=True)

    async def stop_all(self, timeout: float = STOP_TIMEOUT) -> None:
        """Stop every process: SIGTERM, then SIGKILL for any still running after ``timeout``."""
        self._intended.update(spec.process.pid for spec in self._specs.values())
        await asyncio.gather(*(spec.process.stop(timeout) for spec in self._specs.values()))
        await asyncio.gather(*(spec.process.watch for spec in self._specs.values()))


def free_port() -> int:
    """A port free on loopback now. Roles are started on a fixed port so a restart comes back
    on the address its proxy already points at."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
