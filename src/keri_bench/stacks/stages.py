"""Where one heti verification spends its time, stage by stage (this.i 3jeks5st).

The timed verifier (``python -m keri_bench.stacks.verifier --stages``) installs these wrappers
around heti's own stage functions, at the heti this repository pins, before it opens its
lockbox. Every verification attempt is an :class:`Attempt`; a wrapped call charges its time to
the attempt running in the same asyncio task, on the harness's monotonic clock. Only the
outermost wrapped call is charged, so no nanosecond is counted twice and the stages never sum to
more than the attempt's total while heti runs them one after another, as 3a8b1ccb does; what
the stages leave over is parsing, schema fitting, SAID and
disclosure checks, and the verifier's own bookkeeping.

The stages, in the papers' terms (Fujie and Suzuki, arXiv:2611.03493):

- ``meet``: ``Lockbox.add_other``, resolving an OOBI and replaying the KEL it returns, which a
  verifier pays once per party it has not met;
- ``key_state.holder`` and ``key_state.issuer``: ``verifying._observe``, the key-state plan's
  steps for that AID; under heti's default plan, a gather from the AID's witnesses and the replay
  of what they return, on every verification;
- ``signatures``: ``ipex.satisfied_by``, the grant's signatures checked against the presenter's
  key state. It and ``key_state.holder`` are holder binding: an anchored ACDC carries no signature
  of its own, its issuer's is the KEL anchor that ``tel.vet`` checks;
- ``tel.fetch``: the Observer read, from which a current verification takes the TEL;
- ``tel.vet``: ``verifying._vetted`` and ``verifying._demand_whole``, keripy's judgment of the
  registry chain and its anchors in the issuer's KEL;
- ``receipt``: ``ipex._admit_exn``, signing the IPEX admit, which is a receipt and not a check.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import time
from contextvars import ContextVar

from ..errors import BenchError

E_MISSING = "e.feature.unsupported.stages.heti.f"
E_STAGE = "e.input.range.stages.stage.f"

STAGES = ("meet", "key_state.holder", "key_state.issuer", "signatures", "tel.fetch", "tel.vet",
          "receipt")
PHASES = ("admit", "current")
"""heti's two calls per verification: admit, which verifies the presenter's own TEL and binds
the presenter, then verify_current_acdc, which verifies again on the Observer's TEL."""

_now = time.monotonic_ns
_current: ContextVar[Attempt | None] = ContextVar("keri_bench_stage_attempt", default=None)
_open: ContextVar[bool] = ContextVar("keri_bench_stage_open", default=False)
"""Whether this task is inside a charged span: per task, so a stage another task runs at the same
time under the same attempt is charged as well, rather than being swallowed by this one."""
_running: set[Attempt] = set()


class Attempt:
    """One verification attempt's stage times, in nanoseconds."""

    def __init__(self, credential: str, *, holder: str | None) -> None:
        self.credential, self.holder = credential, holder
        self.stages = dict.fromkeys(STAGES, 0)
        self.calls = dict.fromkeys(STAGES, 0)
        self.phases: dict[str, int] = {}
        self.overlapped = False
        self.total = 0

    def key_state(self, aid: str) -> str:
        return "key_state.holder" if aid == self.holder else "key_state.issuer"

    @contextlib.contextmanager
    def active(self):
        """This attempt is the one wrapped calls in this task charge. Another attempt running at
        the same time marks both overlapped, since one event loop interleaving them charges each
        with some of the other's work."""
        if _running:
            self.overlapped = True
            for other in _running:
                other.overlapped = True
        _running.add(self)
        token = _current.set(self)
        began = _now()
        try:
            yield self
        finally:
            self.total = _now() - began
            _current.reset(token)
            _running.discard(self)

    @contextlib.contextmanager
    def phase(self, name: str):
        began = _now()
        try:
            yield
        finally:
            self.phases[name] = self.phases.get(name, 0) + _now() - began

    def record(self, *, outcome: str, code: str | None) -> dict:
        return {"credential": self.credential, "holder": self.holder, "outcome": outcome,
                "code": code,
                "total_ns": self.total, "overlapped": self.overlapped,
                "phases": dict(self.phases), "stages": dict(self.stages),
                "calls": dict(self.calls)}


@contextlib.contextmanager
def span(stage: str):
    """Charge the time inside to ``stage`` of the running attempt, unless an outer span already
    is being charged."""
    if stage not in STAGES:
        raise BenchError(E_STAGE, f"{stage!r} is not a verification stage; the stages are "
                         f"{list(STAGES)}.")
    attempt = _current.get()
    if attempt is None or _open.get():
        yield
        return
    token = _open.set(True)
    began = _now()
    try:
        yield
    finally:
        attempt.stages[stage] += _now() - began
        attempt.calls[stage] += 1
        _open.reset(token)


def _stage_of(stage):
    """``stage`` as a function of the call's arguments: a name, or a callable choosing one."""
    if callable(stage):
        return stage
    return lambda *args, **kwargs: stage


def _wrapped(function, stage):
    choose = _stage_of(stage)
    if inspect.iscoroutinefunction(function):
        @functools.wraps(function)
        async def timed(*args, **kwargs):
            with span(choose(*args, **kwargs)):
                return await function(*args, **kwargs)
    else:
        @functools.wraps(function)
        def timed(*args, **kwargs):
            with span(choose(*args, **kwargs)):
                return function(*args, **kwargs)
    return timed


def _observed(lockbox, aid, *args, **kwargs) -> str:
    attempt = _current.get()
    return attempt.key_state(aid) if attempt is not None else "key_state.issuer"


def install(*, ipex=None, verifying=None, lockbox=None) -> None:
    """Wrap heti's stage functions in place, or refuse naming the first one heti lacks."""
    if ipex is None:
        from heti.anchored.produce import ipex
    if verifying is None:
        from heti.anchored.produce import verifying
    if lockbox is None:
        from heti.anchored.produce.lockbox import Lockbox as lockbox
    targets = ((ipex, "satisfied_by", "signatures"), (ipex, "_admit_exn", "receipt"),
               (verifying, "_observe", _observed), (verifying, "_vetted", "tel.vet"),
               (verifying, "_demand_whole", "tel.vet"), (lockbox, "add_other", "meet"))
    for owner, name, _ in targets:
        if not callable(getattr(owner, name, None)):
            raise BenchError(E_MISSING, f"heti has no {name} where the timed verifier wraps it, "
                             f"so it cannot time that stage; the stage wrappers were written "
                             f"for heti 3a8b1ccb and need updating for this one.")
    for owner, name, stage in targets:
        setattr(owner, name, _wrapped(getattr(owner, name), stage))


class TimedSource:
    """A RegistrySource whose reads are charged to ``tel.fetch``."""

    def __init__(self, source) -> None:
        self._source = source

    def __getattr__(self, name):
        return getattr(self._source, name)

    async def read(self, regid: str):
        with span("tel.fetch"):
            return await self._source.read(regid)


def write(path, record: dict) -> None:
    """Append one record as a JSON line."""
    import json
    with open(path, "a") as out:
        out.write(json.dumps(record, sort_keys=True) + "\n")
