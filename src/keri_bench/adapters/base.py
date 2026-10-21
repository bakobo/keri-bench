"""The adapter interface (this.i 2vu4969q).

An adapter is a Python class per role. Its ``declaration`` says, before the run, every milestone
it can emit with each one's method, clock and resolution, its versioned knob schema, and the stack
versions it was contract-tested against. Every event goes through ``AdapterContext.emit``, which
refuses anything the declaration does not name, so the contract suite checks an adapter by running
it rather than by reading it.

Lifecycle: the harness calls ``start`` on every adapter in role order, then ``connect`` in the same
order, then the role's operations, then ``stop``. ``start`` brings up what the adapter serves and
returns those endpoints; ``connect`` wires it to its peers once every peer has started, because on
real stacks the wiring is circular (an issuer needs the Registrar, and the Observer needs the
issuer). Every endpoint a peer is given is the harness's proxy in front of the real one
(this.i 0wcr2rrj), so an adapter must use the endpoints it is given and nothing else.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..errors import BenchError
from ..knobs import KnobSchema
from ..timeline import Event
from ..vocabulary import MILESTONES, Method, Role

E_MILESTONE = "e.input.range.declaration.milestone.f"
E_METHOD = "e.input.range.declaration.method.f"
E_CLOCK = "e.input.range.declaration.clock.f"
E_RESOLUTION = "e.input.range.declaration.resolution.f"
E_ADAPTER = "e.input.missing.declaration.adapter.f"
E_UNDECLARED = "e.rule.adapter.milestone.undeclared.f"
E_TIME = "e.rule.adapter.milestone.time.f"
E_CREDENTIALS = "e.rule.adapter.milestone.credentials.f"

HARNESS = "harness"
"""The harness's monotonic clock. Only ``observed`` and ``polled`` events use it."""
REALTIME = "realtime"
"""Wall time on this host. The harness measures its offset itself."""
STACK = "stack:"
"""Prefix of a clock the stack keeps; the adapter supplies a reader so its offset is measured."""

Endpoints = dict[str, str | list[str]]


@dataclass(frozen=True)
class Observation:
    method: str
    clock: str
    resolution_ns: int | None = None
    """Required for polled (the poll interval) and reported (the stack's timestamp granularity);
    an observed event's resolution is the harness clock's, 1 ns."""

    @property
    def resolution(self) -> int:
        return 1 if self.resolution_ns is None else self.resolution_ns


@dataclass(frozen=True)
class Declaration:
    adapter: str
    role: Role
    version: str
    milestones: Mapping[str, Observation]
    knobs: KnobSchema
    tested_against: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.adapter:
            raise BenchError(E_ADAPTER, "An adapter declaration must name its adapter.")
        for name, seen in self.milestones.items():
            if name not in MILESTONES:
                raise BenchError(E_MILESTONE, f"{self.adapter} declares {name!r}, which is not a "
                                 f"milestone in the harness's vocabulary.")
            if seen.method not in set(Method):
                raise BenchError(E_METHOD, f"{self.adapter} declares {name!r} with method "
                                 f"{seen.method!r}; it must be observed, polled or reported.")
            stack = seen.clock.startswith(STACK) and len(seen.clock) > len(STACK)
            reported = seen.method == Method.REPORTED
            if not (seen.clock in (HARNESS, REALTIME) or stack) \
                    or reported == (seen.clock == HARNESS):
                raise BenchError(E_CLOCK, f"{self.adapter} declares {name!r} on clock "
                                 f"{seen.clock!r} with method {seen.method!r}. A stack-written "
                                 f"time is reported on a realtime or stack: clock; the harness's "
                                 f"own clock is only observed or polled.")
            if seen.method != Method.OBSERVED and not (seen.resolution_ns or 0) > 0:
                raise BenchError(E_RESOLUTION, f"{self.adapter} declares {name!r} as "
                                 f"{seen.method} without a positive resolution: a poll interval, "
                                 f"or the granularity of the stack's timestamps.")

    def stack_clocks(self) -> set[str]:
        return {seen.clock for seen in self.milestones.values() if seen.clock.startswith(STACK)}

    def describe(self) -> dict:
        return {"adapter": self.adapter, "role": self.role.value, "version": self.version,
                "milestones": {name: {"method": seen.method, "clock": seen.clock,
                                      "resolution_ns": seen.resolution}
                               for name, seen in self.milestones.items()},
                "knobs": self.knobs.describe(), "tested_against": dict(self.tested_against)}


class AdapterContext:
    """What the harness gives an adapter: knobs, peers, process control, the clock and ``emit``."""

    def __init__(self, declaration: Declaration, *, run_dir: Path | None,
                 knobs: Mapping[str, Any], peers: Mapping[Role, Endpoints], procs: Any,
                 sink: Callable[[Event], None], harness_clock: str, realtime_clock: str,
                 clock: Callable[[], int] = time.monotonic_ns,
                 observe: Callable[..., None] | None = None,
                 front: Callable[[str, str], Awaitable[str]] | None = None,
                 shared: dict | None = None) -> None:
        self.declaration = declaration
        self.run_dir = run_dir
        self.knobs = dict(knobs)
        self.peers = peers
        self.procs = procs
        self.observe = observe
        self.front = front
        self.shared = {} if shared is None else shared
        """One dict every adapter in a run sees: what one role learns about a credential that
        another needs, such as the SAID and registry an issuer assigned it."""
        """``await ctx.front(name, upstream)`` puts a proxy link named ``<role>.<name>`` in front of
        ``upstream`` before the adapter starts the process behind it, and returns the proxy's URL.
        For a stack whose processes advertise their own address (a KERI witness names its URL in
        a signed location record), so that peers that discover the address still go through the
        proxy."""
        self._sink = sink
        self._clock = clock
        self._domains = {HARNESS: harness_clock, REALTIME: realtime_clock}

    def now(self) -> int:
        return self._clock()

    def domain(self, kind: str) -> str:
        return self._domains.get(kind, kind)

    def emit(self, name: str, *, credentials: Iterable[str], t: int | None = None,
             batch_id: str | None = None, attrs: Mapping[str, Any] | None = None) -> Event:
        seen = self.declaration.milestones.get(name)
        adapter = self.declaration.adapter
        if seen is None:
            raise BenchError(E_UNDECLARED, f"{adapter} emitted {name!r}, which its declaration "
                             f"does not name, so the event was refused.")
        credentials = tuple(credentials)
        if not credentials:
            raise BenchError(E_CREDENTIALS, f"{adapter} emitted {name!r} for no credential; "
                             f"every milestone names the credentials it covers.")
        if t is None:
            if seen.method == Method.REPORTED:
                raise BenchError(E_TIME, f"{adapter} emitted the reported milestone {name!r} "
                                 f"without the time the stack wrote.")
            t = self.now()
        event = Event(name=name, t=t, method=seen.method, clock=self.domain(seen.clock),
                      resolution=seen.resolution, credentials=credentials,
                      role=MILESTONES[name].value, batch_id=batch_id,
                      attrs={**(attrs or {}), "adapter": adapter})
        self._sink(event)
        return event


@dataclass(frozen=True)
class Issued:
    """What issuance handed back: what the harness relays when the stack does not deliver."""

    credential: str
    said: str | None
    grant: bytes | None
    delivered_by_stack: bool
    extra: Mapping[str, Any] = field(default_factory=dict)


class Adapter:
    declaration: ClassVar[Declaration]

    def __init__(self, ctx: AdapterContext) -> None:
        self.ctx = ctx

    async def start(self) -> Endpoints:
        return {}

    async def connect(self) -> Endpoints:
        return {}

    async def stop(self) -> None:
        return None

    async def versions(self) -> dict[str, str]:
        """The running version of each component this adapter drives, for provenance."""
        return {}

    def clock_sources(self) -> dict[str, Callable[[], Awaitable[int]]]:
        """A reader per ``stack:`` clock this adapter declares, for offset measurement."""
        return {}


class IssuerAdapter(Adapter):
    async def meet(self, holder: Mapping[str, Any]) -> None:
        """Learn the holder before the measured window, as an issuer knows its issuee before it
        issues; by default there is nothing to learn."""

    async def issue(self, credential: str, holder: Mapping[str, Any]) -> Issued:
        raise NotImplementedError

    async def revoke(self, credential: str) -> None:
        raise NotImplementedError

    async def footprint(self) -> int | None:
        """Bytes the issuer's store occupies now, sampled through a soak (this.i 7f9jfc99);
        None when the adapter cannot tell, and then no sample is written."""
        return None


class WitnessPoolAdapter(Adapter):
    async def anchors(self, credential: str) -> int | None:
        """How many KEL events anchor this credential's issuance, for reconciliation; None if
        the adapter cannot tell."""
        return None


class StateDistributionAdapter(Adapter):
    pass


class HolderAdapter(Adapter):
    async def identity(self) -> Mapping[str, Any]:
        raise NotImplementedError

    async def receive(self, issued: Issued) -> None:
        raise NotImplementedError

    async def present(self, credential: str, verifier: Mapping[str, Any]) -> Any:
        raise NotImplementedError


class VerifierAdapter(Adapter):
    async def identity(self) -> Mapping[str, Any]:
        raise NotImplementedError

    async def verify(self, credential: str, presentation: Any) -> bool:
        raise NotImplementedError

    async def status(self, credential: str) -> Mapping[str, Any]:
        """``{"status": "valid"|"revoked"|"unknown", "said": str|None}``, as this verifier would
        judge the credential now."""
        raise NotImplementedError

    async def sync_cold(self) -> None:
        raise NotImplementedError


ROLE_BASES: dict[Role, type[Adapter]] = {
    Role.ISSUER: IssuerAdapter, Role.WITNESS_POOL: WitnessPoolAdapter,
    Role.STATE_DISTRIBUTION: StateDistributionAdapter, Role.HOLDER: HolderAdapter,
    Role.VERIFIER: VerifierAdapter,
}
