"""Clock domains, the offsets measured between them, and the intervals built across them.

The harness clock is the host's CLOCK_MONOTONIC, named by boot id, so a stack process on the same
box that stamps with it shares the domain exactly (this.i 98rxw488). Any other domain is usable
only through an offset measured during the run, and an interval across domains without one is not
measured (this.i 7vsky4sk).
"""

from __future__ import annotations

import math
import socket
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .errors import BenchError
from .timeline import Event

BOOT_ID = Path("/proc/sys/kernel/random/boot_id")

E_BOOT_ID = "e.env.clock.boot-id.f"
E_SAMPLES = "e.input.missing.clock.samples.f"
E_MIXED = "e.input.multi.clock.domain.f"
E_OFFSET_EVENT = "e.input.format.event.clock-offset.f"


def harness_domain(boot_id: Path = BOOT_ID) -> str:
    try:
        return f"monotonic@{boot_id.read_text().strip()}"
    except OSError:
        raise BenchError(E_BOOT_ID, f"The harness could not read the boot id at {boot_id}, so it "
                         f"cannot name its clock domain. It runs on Linux only.") from None


def realtime_domain() -> str:
    """Wall time on this host. It steps under NTP, so it is never the harness's own clock."""
    return f"realtime@{socket.gethostname()}"


@dataclass(frozen=True)
class OffsetSample:
    domain: str
    offset_ns: int
    """Add to a time in ``domain`` to get harness time."""
    bracket_ns: int
    """The width of the harness reads around the foreign read: the sample's uncertainty."""


def sample_offset(domain: str, read_domain: Callable[[], int],
                  mono: Callable[[], int] = time.monotonic_ns) -> OffsetSample:
    before = mono()
    foreign = read_domain()
    after = mono()
    return OffsetSample(domain, (before + after) // 2 - foreign, after - before)


async def sample_offset_async(domain: str, read_domain: Callable[[], Awaitable[int]],
                              mono: Callable[[], int] = time.monotonic_ns) -> OffsetSample:
    """The same bracket around a read that has to ask a stack over the network."""
    before = mono()
    foreign = await read_domain()
    after = mono()
    return OffsetSample(domain, (before + after) // 2 - foreign, after - before)


@dataclass(frozen=True)
class Offset:
    domain: str
    offset_ns: int
    bound_ns: int
    """The true offset at any moment in the run lies within this many ns of ``offset_ns``."""


def combine(samples: Iterable[OffsetSample]) -> Offset:
    """One offset for a run from its samples (at least the start and the end).

    The bound is the widest bracket plus the whole change in offset between samples, so a clock
    step during the run widens every interval that uses it instead of hiding inside one.
    """
    samples = list(samples)
    if not samples:
        raise BenchError(E_SAMPLES, "No offset samples were taken, so the domain has no offset.")
    domains = {s.domain for s in samples}
    if len(domains) > 1:
        raise BenchError(E_MIXED, f"Offset samples from several domains ({sorted(domains)}) "
                         f"cannot be combined into one offset.")
    offsets = [s.offset_ns for s in samples]
    low, high = min(offsets), max(offsets)
    return Offset(samples[0].domain, (low + high) // 2,
                  max(s.bracket_ns for s in samples) + (high - low))


@dataclass(frozen=True)
class Interval:
    value_ns: int
    resolution_ns: int

    def reported(self) -> int:
        """The value rounded up to its resolution, never below zero: what a report may show."""
        if self.resolution_ns <= 1:
            return self.value_ns
        return max(0, self.resolution_ns * math.ceil(self.value_ns / self.resolution_ns))


@dataclass(frozen=True)
class NotMeasured:
    reason: str


@dataclass
class Clocks:
    """The harness's domain and every foreign domain whose offset was measured in the run."""

    harness: str
    offsets: dict[str, Offset] = field(default_factory=dict)

    @classmethod
    def from_events(cls, harness: str, events: Iterable[Event]) -> Clocks:
        samples: dict[str, list[OffsetSample]] = defaultdict(list)
        for event in events:
            if event.name != "clock_offset":
                continue
            attrs = event.attrs or {}
            try:
                sample = OffsetSample(attrs["domain"], int(attrs["offset_ns"]),
                                      int(attrs["bracket_ns"]))
            except (KeyError, TypeError, ValueError):
                raise BenchError(E_OFFSET_EVENT, "A clock_offset event must carry domain, "
                                 "offset_ns and bracket_ns.") from None
            samples[sample.domain].append(sample)
        return cls(harness, {domain: combine(group) for domain, group in samples.items()})

    def to_harness(self, event: Event) -> tuple[int, int] | None:
        """The event's time on the harness clock and its offset bound, or None if unmeasured."""
        if event.clock == self.harness:
            return event.t, 0
        offset = self.offsets.get(event.clock)
        if offset is None:
            return None
        return event.t + offset.offset_ns, offset.bound_ns


def interval(start: Event, end: Event, clock_map: Clocks) -> Interval | NotMeasured:
    """The interval between two events, or why it is not measured.

    An end stamped before its start by more than the interval's resolution is inconsistent
    clock evidence, and is not measured rather than reported as zero, which would pull the
    percentiles down with a number nobody observed. Within the resolution it is uncertainty,
    and reports as zero.
    """
    resolution = max(start.resolution, end.resolution, 1)
    if start.clock == end.clock:
        result = Interval(end.t - start.t, resolution)
    else:
        begin, finish = clock_map.to_harness(start), clock_map.to_harness(end)
        for event, mapped in ((start, begin), (end, finish)):
            if mapped is None:
                return NotMeasured(f"{event.name} is stamped in {event.clock}, which has no "
                                   f"offset to the harness clock measured in this run.")
        result = Interval(finish[0] - begin[0], resolution + begin[1] + finish[1])
    if result.value_ns < -result.resolution_ns:
        return NotMeasured(f"{end.name} is stamped {-result.value_ns} ns before {start.name}, "
                           f"more than the {result.resolution_ns} ns resolution, so the clock "
                           f"evidence is inconsistent.")
    return result
