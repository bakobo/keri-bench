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
from collections.abc import Callable
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
               "revoke": {Role.VERIFIER}}

E_ROLE_MISSING = "e.input.missing.scenario.role.f"
E_ROLE_MISMATCH = "e.input.range.scenario.adapter.role.f"
E_SOURCE = "e.state.conflict.run.milestone-source.f"
E_FAULT_TARGET = "e.input.range.scenario.fault.target.f"
E_RECOGNITION = "e.state.pending.run.recognition.r"

HARNESS_VERSION = "1"
OFFSET_READS = 15


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
        if fault.kind == "crash" and fault.target not in scenario.roles:
            raise BenchError(E_FAULT_TARGET, f"The crash fault targets {fault.target!r}, which "
                             f"is not a configured role.")
    return Plan(scenario, classes, knobs, harness)


@dataclass
class Run:
    plan: Plan
    run_dir: Path
    rate: float
    repetition: int
    harness_commit: str
    clock: Callable[[], int] = time.monotonic_ns
    links: dict[str, Link] = field(default_factory=dict)
    adapters: dict[Role, Adapter] = field(default_factory=dict)
    peers: dict[Role, Endpoints] = field(default_factory=dict)
    watchers: dict[str, list] = field(default_factory=lambda: defaultdict(list))
    tasks: set = field(default_factory=set)
    credentials: list[str] = field(default_factory=list)

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
                              realtime_clock=self.realtime_clock, observe=self._observe)

    def _observe(self, link: str, callback) -> None:
        self.watchers[link].append(callback)

    def _exchange(self, exchange) -> None:
        for callback in self.watchers.get(exchange.link, ()):
            callback(exchange)

    # --- topology ---

    async def _proxy(self, role: Role, endpoints: Endpoints) -> Endpoints:
        proxied: Endpoints = {}
        for name, value in endpoints.items():
            urls = value if isinstance(value, list) else [value]
            fronted = []
            for index, url in enumerate(urls):
                link_name = f"{role.value}.{name}" + (f".{index}" if isinstance(value, list)
                                                      else "")
                scheme, _, rest = url.partition("://")
                hostport, slash, path = rest.partition("/")
                link = Link(link_name, f"{scheme}://{hostport}", clock=self.clock,
                            on_exchange=self._exchange)
                await link.start()
                self.links[link_name] = link
                fronted.append(link.url + slash + path)
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

    async def _offsets(self) -> None:
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

    async def _lifecycle(self, credential: str) -> None:
        stages = self.plan.scenario.stages
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
                await self.adapters[Role.VERIFIER].verify(credential, presentation)
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

    async def _recognise(self, credential: str) -> None:
        """Poll the verifier until ``stable_polls`` polls in a row say revoked; the milestone is
        the first poll of that final streak, with the first revoked poll kept beside it."""
        rule = self.plan.scenario.recognition
        verifier = self.adapters[Role.VERIFIER]
        first_seen, streak_start, streak = None, None, 0
        while streak < rule.stable_polls:
            polled = self.clock()
            answer = await verifier.status(credential)
            if answer.get("status") == "revoked":
                first_seen = polled if first_seen is None else first_seen
                streak_start = polled if streak == 0 else streak_start
                streak += 1
            else:
                streak = 0
            if streak < rule.stable_polls:
                await asyncio.sleep(rule.poll_ms / 1e3)
        self.monitor.emit("revocation_recognised", credentials=[credential], t=streak_start,
                          attrs={"first_seen": first_seen, "polls": rule.stable_polls})

    def _fire(self, index: int, scheduled: int) -> None:
        credential = f"c{index:06d}"
        self.credentials.append(credential)
        self.driver.emit("requested", credentials=[credential], t=scheduled)
        self._harness_event("dispatched", credentials=[credential])
        task = asyncio.get_running_loop().create_task(self._lifecycle(credential))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # --- faults ---

    async def _fault(self, fault: Fault, window_start: int) -> None:
        await asyncio.sleep(max(0, window_start + round(fault.at_s * 1e9) - self.clock()) / 1e9)
        attrs = {"kind": fault.kind, "target": fault.target}
        if fault.kind == "crash":
            names = [n for n in self.procs.names()
                     if n == fault.target or n.startswith(fault.target + ".")]
            self._harness_event("fault_started", attrs=attrs)
            for name in names:
                await self.procs.kill(name)
            await asyncio.sleep(fault.duration_s)
            for name in names:
                await self.procs.restart(name)
            self._harness_event("fault_ended", attrs=attrs)
            return
        link = self.links.get(fault.target)
        if link is None:
            raise BenchError(E_FAULT_TARGET, f"The {fault.kind} fault targets the link "
                             f"{fault.target!r}; this run's links are {sorted(self.links)}.")
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
        writer = TimelineWriter(self.run_dir / "timeline.jsonl",
                                Header(harness_commit=self.harness_commit,
                                       harness_clock=self.harness_clock))
        self.sink = writer.write
        self.procs = ProcessController(
            self.run_dir, clock=self.clock,
            on_event=lambda name, t, attrs: self._harness_event(
                name, t=t, attrs={k: v for k, v in attrs.items() if k != "announced"}))
        self.driver = self._context(DRIVER, {})
        self.relay = self._context(RELAY, {})
        if self.plan.scenario.recognition is not None:
            self.monitor = self._context(_monitor(self.plan.scenario.recognition.poll_ms), {})
        cpu_before = resource.getrusage(resource.RUSAGE_SELF)
        load = self.plan.scenario.load
        reconciliation, components, cost = None, {}, {}
        try:
            await self._bring_up()
            holder = self.adapters.get(Role.HOLDER)
            verifier = self.adapters.get(Role.VERIFIER)
            self.holder_identity = await holder.identity() if holder else {}
            self.verifier_identity = await verifier.identity() if verifier else {}
            await self._offsets()
            # Everything allocated so far is long-lived; freezing it keeps a full collection
            # during the window from walking it, which showed up as multi-millisecond send lag.
            gc.collect()
            gc.freeze()
            window_start = self.clock()
            self._harness_event("run_started", t=window_start)
            faults = [asyncio.get_running_loop().create_task(self._fault(f, window_start))
                      for f in self.plan.scenario.faults]
            await OpenLoopDriver(self.rate, load.duration_s, clock=self.clock).run(self._fire)
            self._harness_event("run_ended")
            gc.unfreeze()
            if self.tasks:
                await asyncio.wait(set(self.tasks), timeout=load.drain_s)
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, *faults, return_exceptions=True)
            await self._offsets()
            reconciliation = await self._reconcile()
            components = {role.value: await adapter.versions()
                          for role, adapter in self.adapters.items()}
            cost = await self._cost(cpu_before)
        finally:
            for adapter in reversed(list(self.adapters.values())):
                await adapter.stop()
            await self.procs.stop_all()
            for link in self.links.values():
                await link.stop()
            writer.close()
        record = self._provenance(reconciliation, components, cost)
        provenance.write(self.run_dir / "provenance.json", record)
        return report.write_report(self.run_dir, generator_commit=self.harness_commit)

    async def _reconcile(self) -> dict | None:
        verifier = self.adapters.get(Role.VERIFIER)
        pool = self.adapters.get(Role.WITNESS_POOL)
        if verifier is None and pool is None:
            return None
        gate = asyncio.Semaphore(32)

        async def ask(call, credential):
            async with gate:
                try:
                    return await call(credential)
                except Exception:
                    return None

        verdicts = await asyncio.gather(*(ask(verifier.status, c) for c in self.credentials)) \
            if verifier else []
        anchors = await asyncio.gather(*(ask(pool.anchors, c) for c in self.credentials)) \
            if pool else []
        return {"verifier": {c: v or {"status": "unknown", "said": None}
                             for c, v in zip(self.credentials, verdicts)},
                "anchors": dict(zip(self.credentials, anchors))}

    async def _cost(self, before) -> dict:
        after = resource.getrusage(resource.RUSAGE_SELF)
        links = {name: await measure_pass_through(samples=200) for name in self.links}
        return {"links": links, "harness_cpu_s": (after.ru_utime - before.ru_utime)
                + (after.ru_stime - before.ru_stime)}

    def _provenance(self, reconciliation, components, cost) -> dict:
        scenario = self.plan.scenario
        declarations = {role.value: cls.declaration.describe()
                        for role, cls in self.plan.classes.items()}
        for declaration in self.plan.harness:
            declarations[declaration.adapter] = declaration.describe()
        extra = {"measurement_cost": cost}
        if reconciliation is not None:
            extra["reconciliation"] = reconciliation
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
                 "kind": "single-host, application-boundary faults (this.i 0wcr2rrj)"},
            **extra)


async def run_once(scenario: Scenario, out: Path, *, rate: float, repetition: int,
                   harness_commit: str) -> dict:
    resolved = plan(scenario)
    run_dir = Path(out) / f"rate-{rate:g}" / f"rep-{repetition}"
    return await Run(resolved, run_dir, rate, repetition, harness_commit).execute()


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


async def sweep(scenario: Scenario, out: Path, *, harness_commit: str) -> dict:
    reports: dict[float, list[dict]] = defaultdict(list)
    for rate in scenario.load.rates:
        for repetition in range(1, scenario.load.repetitions + 1):
            reports[rate].append(await run_once(scenario, out, rate=rate, repetition=repetition,
                                                harness_commit=harness_commit))
    summary = summarize(scenario, reports)
    (Path(out) / "sweep.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
