"""One measured run, and a sweep of runs over a scenario's load steps and repetitions.

A run: resolve every adapter and knob before anything starts (an unset knob refuses the run, this.i
4q4540c4); start the roles in order with a proxy in front of every endpoint (this.i 0wcr2rrj);
measure clock offsets; drive issuance open-loop (this.i 808tr3fn) while each credential flows
through the scenario's stages; inject the scheduled faults; drain; reconcile; then write
``provenance.json`` and derive the report from it and the timeline alone (this.i 8q4myzru).
"""

from __future__ import annotations

import asyncio
import gc
import json
import resource
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import clocks, provenance, report
from .adapters import registry
from .adapters.base import (Adapter, AdapterContext, Declaration, Endpoints, Issued,
                            Observation)
from .driver import OpenLoopDriver
from .errors import BenchError
from .knobs import KnobSchema, resolve
from .procs import ProcessController
from .proxy import Link, measure_pass_through
from .scenario import Fault, Scenario
from .stats import saturation, spread
from .timeline import HARNESS_ROLE, Event, Header, TimelineWriter
from .vocabulary import Role

ROLE_ORDER = (Role.WITNESS_POOL, Role.STATE_DISTRIBUTION, Role.ISSUER, Role.HOLDER,
              Role.VERIFIER)
"""Start order. State distribution precedes the issuer because an issuer publishes to it; the
circular parts of real wiring happen in ``connect``, after every role has started."""

STAGE_ROLES = {"issue": {Role.ISSUER}, "deliver": {Role.HOLDER},
               "present": {Role.HOLDER, Role.VERIFIER}, "verify": {Role.VERIFIER},
               "revoke": {Role.VERIFIER}, "sync": {Role.VERIFIER}}

E_ROLE_MISSING = "e.input.missing.scenario.role.f"
E_ROLE_MISMATCH = "e.input.range.scenario.adapter.role.f"
E_SOURCE = "e.state.conflict.run.milestone-source.f"
E_FAULT_TARGET = "e.input.range.scenario.fault.target.f"
E_NOT_VERIFIED = "e.proof.presentation.f"
E_STATUS = "e.party.verifier.status.f"
E_ATTEMPTS = "e.input.range.sweep.attempts.f"

HARNESS_VERSION = "1"
OFFSET_READS = 15
RECONCILE_CONCURRENCY = 4
RECONCILE_ATTEMPTS = 3
RECONCILE_PAUSE_S = 1.0
"""Reconciliation asks a few questions at a time and asks a failed one again: on the imbu stack
each status question makes the one holder present again and the one verifier admit and verify,
so 32 at once timed out and a credential that was verified was counted lost (found in the
October 2026 baseline). One the verifier never answers is unanswered, not lost."""


def _harness_declaration(adapter: str, role: Role, milestones: dict) -> Declaration:
    return Declaration(adapter=adapter, role=role, version=HARNESS_VERSION,
                       milestones=milestones, knobs=KnobSchema(1, {}), tested_against={})


DRIVER = _harness_declaration("harness.driver", Role.ISSUER,
                              {"requested": Observation("observed", "harness")})
RELAY = _harness_declaration("harness.relay", Role.ISSUER,
                             {"grant_sent": Observation("observed", "harness")})


def _monitor(poll_ms: float) -> Declaration:
    return _harness_declaration("harness.monitor", Role.VERIFIER, {
        "revocation_recognised": Observation("polled", "harness", round(poll_ms * 1e6))})


@dataclass
class Plan:
    """Everything resolved from a scenario before a single process starts."""

    scenario: Scenario
    classes: dict[Role, type[Adapter]]
    knobs: dict[Role, dict]
    harness: list[Declaration]


def plan(scenario: Scenario) -> Plan:
    needed = set().union(*(STAGE_ROLES[stage] for stage in scenario.stages))
    configured = {Role(role) for role in scenario.roles}
    missing = sorted(role.value for role in needed - configured)
    if missing:
        raise BenchError(E_ROLE_MISSING, f"The scenario's stages {list(scenario.stages)} need "
                         f"the roles {missing}, which it does not configure.")
    classes, knobs = {}, {}
    for name, config in scenario.roles.items():
        role = Role(name)
        cls = registry.lookup(config.adapter)
        if cls.declaration.role is not role:
            raise BenchError(E_ROLE_MISMATCH, f"The adapter {config.adapter!r} is a "
                             f"{cls.declaration.role.value} adapter, not a {name} adapter.")
        classes[role] = cls
        knobs[role] = resolve(cls.declaration.knobs, config.knobs, owner=name)
    harness = [DRIVER]
    if "deliver" in scenario.stages and "grant_sent" not in classes[Role.ISSUER].declaration \
            .milestones:
        harness.append(RELAY)
    if scenario.recognition is not None:
        harness.append(_monitor(scenario.recognition.poll_ms))
    sources: dict[str, str] = {}
    for declaration in [cls.declaration for cls in classes.values()] + harness:
        for milestone in declaration.milestones:
            if milestone in sources:
                raise BenchError(E_SOURCE, f"Both {sources[milestone]} and "
                                 f"{declaration.adapter} declare {milestone!r}; a run takes "
                                 f"each milestone from exactly one source (this.i 0ys878gy).")
            sources[milestone] = declaration.adapter
    for fault in scenario.faults:
        # A crash names a role, or one process of it as <role>.<process> (this.i 5ed29j5w).
        if fault.kind == "crash" and fault.target.split(".")[0] not in scenario.roles:
            raise BenchError(E_FAULT_TARGET, f"The crash fault targets {fault.target!r}, which "
                             f"is neither a configured role nor one of its processes.")
    return Plan(scenario, classes, knobs, harness)


@dataclass
class Run:
    plan: Plan
    run_dir: Path
    rate: float
    repetition: int
    harness_commit: str
    calibration: Path | None = None
    clock: Callable[[], int] = time.monotonic_ns
    links: dict[str, Link] = field(default_factory=dict)
    adapters: dict[Role, Adapter] = field(default_factory=dict)
    peers: dict[Role, Endpoints] = field(default_factory=dict)
    watchers: dict[str, list] = field(default_factory=lambda: defaultdict(list))
    tasks: set = field(default_factory=set)
    exchanges: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    shared: dict = field(default_factory=dict)
    recognitions: dict[str, list] = field(default_factory=dict)
    watching: set = field(default_factory=set)
    credentials: list[str] = field(default_factory=list)
    completed: set = field(default_factory=set)
    stopped: bool = False

    # --- events ---

    def _harness_event(self, name: str, *, t: int | None = None, credentials=(),
                       attrs: dict | None = None) -> None:
        self.sink(Event(name=name, t=self.clock() if t is None else t, method="observed",
                        clock=self.harness_clock, resolution=1, credentials=tuple(credentials),
                        role=HARNESS_ROLE, attrs=attrs))

    def _context(self, declaration: Declaration, knobs: dict) -> AdapterContext:
        return AdapterContext(declaration, run_dir=self.run_dir, knobs=knobs, peers=self.peers,
                              procs=self.procs, sink=self.sink, clock=self.clock,
                              harness_clock=self.harness_clock,
                              realtime_clock=self.realtime_clock, observe=self._observe,
                              front=lambda name, upstream: self._front(declaration.role,
                                                                       name, upstream),
                              shared=self.shared)

    def _observe(self, link: str, callback) -> None:
        self.watchers[link].append(callback)

    def _exchange(self, exchange) -> None:
        self.exchanges[exchange.link] += 1
        for callback in self.watchers.get(exchange.link, ()):
            callback(exchange)

    # --- topology ---

    async def _front(self, role: Role, name: str, upstream: str) -> str:
        link = Link(f"{role.value}.{name}", upstream, clock=self.clock,
                    on_exchange=self._exchange)
        await link.start()
        self.links[link.name] = link
        return link.url

    async def _proxy(self, role: Role, endpoints: Endpoints) -> Endpoints:
        """Front every http URL an adapter announces with a link; pass anything else through.

        A URL that is already a link's address (the adapter fronted it itself) is left alone, and
        so is a value that is not an http URL, such as an AID a peer needs to know.
        """
        fronts = {link.url for link in self.links.values()}
        proxied: Endpoints = {}
        for name, value in endpoints.items():
            urls = value if isinstance(value, list) else [value]
            fronted = []
            for index, url in enumerate(urls):
                scheme, _, rest = str(url).partition("://")
                hostport, slash, path = rest.partition("/")
                if scheme != "http" or f"{scheme}://{hostport}" in fronts:
                    fronted.append(url)
                    continue
                link_name = f"{role.value}.{name}" + (f".{index}" if isinstance(value, list)
                                                      else "")
                fronted.append(await self._front(role, link_name.split(".", 1)[1],
                                                  f"{scheme}://{hostport}") + slash + path)
            proxied[name] = fronted if isinstance(value, list) else fronted[0]
        return proxied

    async def _bring_up(self) -> None:
        roles = [role for role in ROLE_ORDER if role in self.plan.classes]
        for role in roles:
            cls = self.plan.classes[role]
            self.adapters[role] = cls(self._context(cls.declaration, self.plan.knobs[role]))
            self.peers[role] = await self._proxy(role, await self.adapters[role].start())
        for role in roles:
            self.peers[role].update(await self._proxy(role, await self.adapters[role].connect()))

    async def _offsets(self) -> None:  # ~3q2p: a dead role's clock read aborts the run
        """Sample every foreign clock, keeping each domain's tightest bracket of several reads,
        as NTP does: a read delayed by a busy loop or a cold connection widens only itself."""
        samples = [min((clocks.sample_offset(self.realtime_clock, time.time_ns, mono=self.clock)
                        for _ in range(OFFSET_READS)), key=lambda s: s.bracket_ns)]
        for adapter in self.adapters.values():
            for domain, read in adapter.clock_sources().items():
                reads = [await clocks.sample_offset_async(domain, read, mono=self.clock)
                         for _ in range(OFFSET_READS)]
                samples.append(min(reads, key=lambda s: s.bracket_ns))
        for sample in samples:
            self._harness_event("clock_offset", attrs={
                "domain": sample.domain, "offset_ns": sample.offset_ns,
                "bracket_ns": sample.bracket_ns})

    # --- the lifecycle of one credential ---

    async def _lifecycle(self, credential: str, full: bool) -> None:
        """One credential through the scenario's stages; only issue when ``full`` is false
        (this.i 8654nxrl)."""
        stages = self.plan.scenario.stages if full else ("issue",)
        stage = "issue"
        try:
            issuer = self.adapters[Role.ISSUER]
            issued: Issued = await issuer.issue(credential, self.holder_identity)
            if "deliver" in stages:
                stage = "deliver"
                if not issued.delivered_by_stack:
                    self.relay.emit("grant_sent", credentials=[credential],
                                    attrs={"channel": "harness-relay"})
                await self.adapters[Role.HOLDER].receive(issued)
            presentation = None
            if "present" in stages:
                stage = "present"
                presentation = await self.adapters[Role.HOLDER].present(
                    credential, self.verifier_identity)
            if "verify" in stages:
                stage = "verify"
                if not await self.adapters[Role.VERIFIER].verify(credential, presentation):
                    raise BenchError(E_NOT_VERIFIED, f"The verifier did not accept the "
                                     f"presentation of {credential}.")
            if "revoke" in stages:
                stage = "revoke"
                await issuer.revoke(credential)
                await self._recognise(credential)
        except asyncio.CancelledError:
            raise
        except Exception as failed:
            self._harness_event("stage_failed", credentials=[credential], attrs={
                "stage": stage, "code": getattr(failed, "code", type(failed).__name__),
                "error": str(failed)[:500]})

    async def _status(self, credential: str, *, confirming: bool = False) -> tuple[int, bool]:
        """One poll. A confirmation poll that fails counts as not revoked, so a revocation is
        never recognised on evidence the run could not gather."""
        polled = self.clock()
        expected = self.shared.get("said", {}).get(credential)
        try:
            answer = await self.adapters[Role.VERIFIER].status(credential)
            if not isinstance(answer, Mapping):
                raise BenchError(E_STATUS, f"The verifier's status for {credential} is not an "
                                 f"object: {answer!r}"[:300])
            # An answer about another credential is no evidence about this one.
            revoked = answer.get("status") == "revoked" and (
                expected is None or answer.get("said") in (None, expected))
        except Exception:
            if not confirming:
                raise
            return polled, False
        return polled, revoked

    async def _recognise(self, credential: str) -> None:
        """Poll the verifier until ``stable_polls`` polls in a row say revoked, which ends the
        credential's lifecycle; then confirm with as many polls again, because the milestone is
        the first poll after which every later poll says revoked, and only later polls can show
        that (test plan, Milestones: revocation_recognised)."""
        rule = self.plan.scenario.recognition
        polls = self.recognitions.setdefault(credential, [])
        streak = 0
        while streak < rule.stable_polls:
            polls.append(await self._status(credential))
            streak = streak + 1 if polls[-1][1] else 0
            if streak < rule.stable_polls:
                await asyncio.sleep(rule.poll_ms / 1e3)
        task = asyncio.get_running_loop().create_task(self._keep_watching(credential, polls))
        self.watching.add(task)

    async def _keep_watching(self, credential: str, polls: list) -> None:
        """Poll ``stable_polls`` more times after the first streak: a confirmation window, so a
        verifier that flips back to valid disproves the streak. Watching to the end of the run
        instead was tried and cost the event loop enough to fail the driver check (calibration,
        2026-10-15)."""
        rule = self.plan.scenario.recognition
        for _ in range(rule.stable_polls):
            await asyncio.sleep(rule.poll_ms / 1e3)
            polls.append(await self._status(credential, confirming=True))

    def _recognitions(self) -> None:
        """Emit each credential's recognition from all its polls: the start of the final
        unbroken run of revoked answers, if that run is at least ``stable_polls`` long."""
        rule = self.plan.scenario.recognition
        for credential, polls in self.recognitions.items():
            tail = 0
            while tail < len(polls) and polls[len(polls) - 1 - tail][1]:
                tail += 1
            if tail < rule.stable_polls:
                continue
            seen = [t for t, revoked in polls if revoked]
            self.monitor.emit("revocation_recognised", credentials=[credential],
                              t=polls[len(polls) - tail][0],
                              attrs={"first_seen": seen[0], "polls": len(polls),
                                     "relapsed": len(seen) > tail})

    def _write(self, event: Event) -> None:
        """Write an event; on a soak with a credential stop, end the schedule once that many of
        the window's credentials have reached the measure's end (this.i 9yx6cg92)."""
        self.writer.write(event)
        stop = self.plan.scenario.stop
        if stop is None or self.stopped or event.name != self.plan.scenario.load.measure[1]:
            return
        self.completed.update(c for c in event.credentials if c in self.fired)
        if len(self.completed) >= stop.credentials:
            self.stopped = True
            self.loop_driver.stop()
            self._harness_event("stop_reached", attrs={"reason": "credentials",
                                                       "completed": len(self.completed)})

    async def _preseed(self) -> None:
        """Fill the store by issuing through the stack before the window (this.i 9yx6cg92)."""
        rule = self.plan.scenario.preseed
        if rule is None:
            return
        issuer = self.adapters[Role.ISSUER]
        gate = asyncio.Semaphore(rule.concurrency)
        failures: list[str] = []
        made: set[str] = set()
        began = self.clock()
        self._harness_event("preseed_started", attrs={"requested": rule.credentials,
                                                      "concurrency": rule.concurrency})

        async def one(index: int) -> None:
            async with gate:
                try:
                    issued = await issuer.issue(f"p{index:07d}", self.holder_identity)
                except Exception as failed:
                    failures.append(f"{type(failed).__name__}: {failed}"[:500])
                    return
                # The store grew only if the stack names what it made, and something new.
                said = getattr(issued, "said", None)
                if not isinstance(said, str) or not said or said in made:
                    failures.append(f"The issuer named no new credential for p{index:07d}, so "
                                    f"the store cannot be counted as holding it.")
                    return
                made.add(said)

        await asyncio.gather(*(one(i) for i in range(rule.credentials)))
        attrs = {"requested": rule.credentials, "failed": len(failures),
                 "elapsed_ns": self.clock() - began}
        if failures:
            attrs["first_error"] = failures[0]
        self._harness_event("preseed_ended", attrs=attrs)

    async def _sample_footprint(self) -> None:
        """Sample the issuer's store size every ``sample_s`` until cancelled. A sample that
        cannot be read is recorded and sampling goes on: one that died silently once left a
        soak's later buckets with a stale size (October 2026 baseline)."""
        issuer = self.adapters[Role.ISSUER]
        while True:
            try:
                size = await issuer.footprint()
            except Exception as failed:
                self._harness_event("footprint_failed", attrs={
                    "error": f"{type(failed).__name__}: {failed}"[:500]})
            else:
                if size is None:
                    return
                self._harness_event("footprint", attrs={"bytes": size})
            await asyncio.sleep(self.plan.scenario.soak.sample_s)

    def _fire(self, index: int, scheduled: int) -> None:
        credential = f"c{index:06d}"
        self.credentials.append(credential)
        self.fired.add(credential)
        self.driver.emit("requested", credentials=[credential], t=scheduled)
        self._harness_event("dispatched", credentials=[credential])
        sample = self.plan.scenario.lifecycle
        full = sample is None or index % sample.full_every == 0
        task = asyncio.get_running_loop().create_task(self._lifecycle(credential, full))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # --- faults ---

    async def _fault(self, fault: Fault, window_start: int) -> None:
        await asyncio.sleep(max(0, window_start + round(fault.at_s * 1e9) - self.clock()) / 1e9)
        attrs = {"kind": fault.kind, "target": fault.target}
        if fault.kind == "crash":
            names = [n for n in self.procs.names()
                     if n == fault.target or n.startswith(fault.target + ".")]
            running = [n for n in names if self.procs.process(n).proc.returncode is None]
            if not running:
                raise BenchError(E_FAULT_TARGET, f"The crash fault targets {fault.target}, which "
                                 f"has no running process the harness started, so nothing was "
                                 f"crashed.")
            self._harness_event("fault_started", attrs=attrs)
            for name in running:
                await self.procs.kill(name)
            await asyncio.sleep(fault.duration_s)
            for name in names:
                await self.procs.restart(name)
            self._harness_event("fault_ended", attrs=attrs)
            return
        link = self.links[fault.target]
        self._harness_event("fault_started", attrs={**attrs, "delay_ms": fault.delay_ms})
        if fault.kind == "partition":
            link.partition()
        elif fault.kind == "delay":
            link.delay(round(fault.delay_ms * 1e6))
        else:
            link.duplicate(True)
        await asyncio.sleep(fault.duration_s)
        if fault.kind == "partition":
            link.heal()
        link.clear()
        self._harness_event("fault_ended", attrs=attrs)

    # --- the run ---

    async def execute(self) -> dict:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.harness_clock = clocks.harness_domain()
        self.realtime_clock = clocks.realtime_domain()
        writer = self.writer = TimelineWriter(self.run_dir / "timeline.jsonl",
                                              Header(harness_commit=self.harness_commit,
                                                     harness_clock=self.harness_clock))
        self.sink = self._write
        self.fired: set[str] = set()
        self.procs = ProcessController(
            self.run_dir, clock=self.clock,
            on_event=lambda name, t, attrs: self._harness_event(
                name, t=t, attrs={k: v for k, v in attrs.items() if k != "announced"}))
        self.driver = self._context(DRIVER, {})
        self.relay = self._context(RELAY, {})
        if self.plan.scenario.recognition is not None:
            self.monitor = self._context(_monitor(self.plan.scenario.recognition.poll_ms), {})
        cpu_before = resource.getrusage(resource.RUSAGE_SELF)
        self.host_load = {"before": provenance.load_average()}
        load = self.plan.scenario.load
        reconciliation, components, cost, census = None, {}, {}, {}
        try:
            await self._bring_up()
            for fault in self.plan.scenario.faults:
                if fault.kind != "crash" and fault.target not in self.links:
                    raise BenchError(E_FAULT_TARGET, f"The {fault.kind} fault targets the link "
                                     f"{fault.target!r}; this run's links are "
                                     f"{sorted(self.links)}.")
            holder = self.adapters.get(Role.HOLDER)
            verifier = self.adapters.get(Role.VERIFIER)
            self.holder_identity = await holder.identity() if holder else {}
            self.verifier_identity = await verifier.identity() if verifier else {}
            issuer = self.adapters.get(Role.ISSUER)
            if issuer is not None and self.holder_identity:
                await issuer.meet(self.holder_identity)
            await self._preseed()
            sampler = None
            if self.plan.scenario.soak is not None:
                sampler = asyncio.get_running_loop().create_task(self._sample_footprint())
            await self._offsets()
            # Everything allocated so far is long-lived; freezing it keeps a full collection
            # during the window from walking it, which showed up as multi-millisecond send lag.
            gc.collect()
            gc.freeze()
            window_start = self.clock()
            self._harness_event("run_started", t=window_start)
            self.host_load["window"] = provenance.load_average()
            faults = [asyncio.get_running_loop().create_task(self._fault(f, window_start))
                      for f in self.plan.scenario.faults]
            self.loop_driver = OpenLoopDriver(self.rate, load.duration_s, clock=self.clock)
            await self.loop_driver.run(self._fire)
            if self.plan.scenario.stop is not None and not self.stopped:
                self.stopped = True
                self._harness_event("stop_reached", attrs={"reason": "duration",
                                                           "completed": len(self.completed)})
            self._harness_event("run_ended")
            gc.unfreeze()
            # Lifecycles still running when the drain ends are cancelled; the report counts them
            # as incomplete, censored at the window's end.
            try:
                await asyncio.wait_for(asyncio.gather(*self.tasks, return_exceptions=True),
                                       load.drain_s)
            except TimeoutError:
                pass
            for fault, outcome in zip(self.plan.scenario.faults,
                                      await asyncio.gather(*faults, return_exceptions=True)):
                if isinstance(outcome, Exception):
                    self._harness_event("fault_failed", attrs={
                        "kind": fault.kind, "target": fault.target,
                        "error": str(outcome)[:500]})
            if sampler is not None:
                sampler.cancel()
                await asyncio.gather(sampler, return_exceptions=True)
            await asyncio.gather(*self.watching, return_exceptions=True)
            if self.plan.scenario.recognition is not None:
                self._recognitions()
            if "sync" in self.plan.scenario.stages:
                await self.adapters[Role.VERIFIER].sync_cold()
            await self._offsets()
            reconciliation = await self._reconcile()
            components = {role.value: await adapter.versions()
                          for role, adapter in self.adapters.items()}
            census = {role.value: counted for role, adapter in self.adapters.items()
                      if (counted := await adapter.census()) is not None}
            cost = await self._cost(cpu_before)
        finally:
            for adapter in reversed(list(self.adapters.values())):
                await adapter.stop()
            await self.procs.stop_all()
            for link in self.links.values():
                await link.stop()
            writer.close()
        record = self._provenance(reconciliation, components, cost, census)
        provenance.write(self.run_dir / "provenance.json", record)
        return report.write_report(self.run_dir, generator_commit=self.harness_commit)

    async def _reconcile(self) -> dict | None:
        # A run that never verified has nothing to ask a verifier about, and without its answer
        # no credential has a fate, so there is no reconciliation (this.i 82r8lxy5).
        verifier = self.adapters.get(Role.VERIFIER)
        if verifier is None or "verify" not in self.plan.scenario.stages:
            return None
        pool = self.adapters.get(Role.WITNESS_POOL)
        gate = asyncio.Semaphore(RECONCILE_CONCURRENCY)

        async def ask(call, credential):
            async with gate:
                for attempt in range(RECONCILE_ATTEMPTS):
                    try:
                        return await call(credential)
                    except Exception:
                        if attempt + 1 < RECONCILE_ATTEMPTS:
                            await asyncio.sleep(RECONCILE_PAUSE_S)
                return None

        verdicts = await asyncio.gather(*(ask(verifier.status, c) for c in self.credentials))
        anchors = await asyncio.gather(*(ask(pool.anchors, c) for c in self.credentials)) \
            if pool else []
        return {"verifier": {c: v or {"status": "unknown", "said": None, "unanswered": True}
                             for c, v in zip(self.credentials, verdicts)},
                "anchors": dict(zip(self.credentials, anchors))}

    async def _cost(self, before) -> dict:
        after = resource.getrusage(resource.RUSAGE_SELF)
        links = {name: await measure_pass_through(samples=200) for name in self.links}
        for name, measured in links.items():
            measured["exchanges"] = self.exchanges.get(name, 0)
        return {"links": links, "harness_cpu_s": (after.ru_utime - before.ru_utime)
                + (after.ru_stime - before.ru_stime)}

    def _provenance(self, reconciliation, components, cost, census) -> dict:
        scenario = self.plan.scenario
        declarations = {role.value: cls.declaration.describe()
                        for role, cls in self.plan.classes.items()}
        for declaration in self.plan.harness:
            declarations[declaration.adapter] = declaration.describe()
        extra = {"measurement_cost": cost, "calibration": _calibration(self.calibration),
                 "unexpected_exits": list(self.procs.unexpected_exits)}
        shape = {}
        if scenario.soak is not None:
            shape["soak"] = {"bucket": scenario.soak.bucket,
                             "per_credentials": scenario.soak.per_credentials,
                             "sample_s": scenario.soak.sample_s,
                             "stop_credentials": scenario.stop.credentials
                             if scenario.stop else None}
        if scenario.preseed is not None:
            shape["preseed"] = {"credentials": scenario.preseed.credentials,
                                "concurrency": scenario.preseed.concurrency}
        if scenario.lifecycle is not None:
            shape["lifecycle"] = {"full_every": scenario.lifecycle.full_every}
        if reconciliation is not None:
            extra["reconciliation"] = reconciliation
        if census:
            extra["census"] = census
        return provenance.build(
            harness_commit=self.harness_commit, scenario_text=scenario.text,
            scenario_sha256=scenario.sha256, adapters=declarations,
            knobs={role.value: knobs for role, knobs in self.plan.knobs.items()},
            components=components, host=provenance.host_fingerprint(),
            clocks={"harness": self.harness_clock, "realtime": self.realtime_clock},
            run={"scenario": scenario.name, "rate": self.rate, "repetition": self.repetition,
                 "warmup_s": scenario.load.warmup_s, "duration_s": scenario.load.duration_s,
                 "lag_limit_ms": scenario.statistics.lag_limit_ms,
                 "measure": list(scenario.load.measure), "stages": list(scenario.stages),
                 "kind": "single-host, application-boundary faults (this.i 0wcr2rrj)",
                 "host_load": {**self.host_load, "end": provenance.load_average()},
                 **shape},
            **extra)


def _calibration(path: Path | None) -> dict | str:
    """The error budget a run's numbers carry (test plan, Part B #1): what the calibration
    measured on the harness, or a plain statement that no calibration was supplied."""
    if path is None:
        return "not calibrated"
    result = json.loads(Path(path).read_text())
    # A calibration written before the gates existed was judged on the two-sided bars.
    return {"path": str(path), "ok": result["ok"], "gate": result.get("gate", "strict"),
            "harness_commit": result["harness_commit"],
            "budget": result["intervals"]["budget"],
            "saturation_relative_error": result["saturation"].get("relative_error")}


async def run_once(scenario: Scenario, out: Path, *, rate: float, repetition: int,
                   harness_commit: str, calibration: Path | None = None) -> dict:
    resolved = plan(scenario)
    # ~6ih6: rates that print alike under :g share a directory.
    run_dir = Path(out) / f"rate-{rate:g}" / f"rep-{repetition}"
    return await Run(resolved, run_dir, rate, repetition, harness_commit,
                     calibration).execute()


def summarize(scenario: Scenario, reports: dict[float, list[dict]]) -> dict:
    """The sweep: per load step, the spread across repetitions; then the saturation point."""
    start, end = scenario.load.measure
    steps, table, refused = [], [], []
    for rate in scenario.load.rates:
        accepted = [r for r in reports.get(rate, []) if not r.get("refused")]
        refused += [{"rate": rate, "repetition": r["run"]["repetition"],
                     "reason": r["refused_reason"]} for r in reports.get(rate, [])
                    if r.get("refused")]
        achieved = [r["throughput"]["milestones"].get(end, {}).get("per_s", 0.0)
                    for r in accepted]
        # The measure interval's p99 with never-completed requests counted at their lower
        # bound: dropping them would hide exactly the queueing saturation produces.
        p99s = [r["incomplete"]["latency"]["p99"]["value"] for r in accepted
                if r["incomplete"]["latency"].get("measured", True)]
        row = {"offered": rate, "runs": len(accepted),
               "achieved": spread(achieved).__dict__ if achieved else None,
               "p99_ns": spread(p99s).__dict__ if p99s else None}
        table.append(row)
        if achieved and p99s:
            steps.append((rate, sum(achieved) / len(achieved), sum(p99s) / len(p99s)))
    found = None
    if len(steps) >= 2:
        hit = saturation(steps, epsilon=scenario.statistics.epsilon, k=scenario.statistics.k)
        if hit is not None:
            found = {"offered": hit.offered, "reason": hit.reason,
                     "achieved": steps[hit.index][1]}
    return {"scenario": scenario.name, "measure": [start, end], "steps": table,
            "saturation": found, "refused": refused,
            "epsilon": scenario.statistics.epsilon, "k": scenario.statistics.k}


async def sweep(scenario: Scenario, out: Path, *, harness_commit: str,
                calibration: Path | None = None, attempts: int | None = None) -> dict:
    """Every load step, until it has the scenario's repetitions accepted or has made
    ``attempts`` runs (by default exactly the repetitions). On a shared host a run the driver
    check refuses says nothing about the stack, so another is run; every refused run is still
    kept and listed."""
    wanted = scenario.load.repetitions
    attempts = wanted if attempts is None else attempts
    if attempts < wanted:
        raise BenchError(E_ATTEMPTS, f"A sweep needs at least as many attempts per step as the "
                         f"scenario's {wanted} repetitions, not {attempts}.")
    reports: dict[float, list[dict]] = defaultdict(list)
    for rate in scenario.load.rates:
        for repetition in range(1, attempts + 1):
            reports[rate].append(await run_once(scenario, out, rate=rate, repetition=repetition,
                                                harness_commit=harness_commit,
                                                calibration=calibration))
            if sum(1 for r in reports[rate] if not r.get("refused")) >= wanted:
                break
    summary = summarize(scenario, reports)
    summary["attempts"] = attempts
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / "sweep.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
