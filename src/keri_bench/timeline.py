"""The raw timeline every number is derived from (this.i 8q4myzru).

``timeline.jsonl`` opens with a header line naming the schema, the vocabulary version, the harness
commit and the harness's clock domain. Each following line is one event. An event that covers many
credentials, such as one anchor for a batch, is one line naming all of them.

Times are integer nanoseconds in the event's own clock domain, never floats, so a hand-computed
golden timeline compares exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import BenchError
from .vocabulary import HARNESS_EVENTS, MILESTONES, VOCABULARY_VERSION, Method, Role

TIMELINE_SCHEMA = 1
SUPPORTED_SCHEMAS = frozenset({1})
"""Every timeline schema this code has ever emitted. The reader never drops one (test plan,
Outputs: "The report generator accepts every timeline schema it has ever emitted")."""

HARNESS_ROLE = "harness"
MAX_LINE = 1 << 20
"""Bytes. A line longer than this is refused before it is parsed (input-handling standard)."""

E_NAME = "e.input.range.event.name.f"
E_METHOD = "e.input.range.event.method.f"
E_CLOCK = "e.input.missing.event.clock.f"
E_RESOLUTION = "e.input.range.event.resolution.f"
E_ROLE = "e.input.range.event.role.f"
E_EVENT_FORMAT = "e.input.format.event.f"
E_SCHEMA = "e.feature.unsupported.timeline.schema.f"
E_HEADER = "e.input.format.timeline.header.f"
E_LINE_FORMAT = "e.input.format.timeline.line.f"
E_LINE_SIZE = "e.input.range.timeline.line.f"

_ROLES = {role.value for role in Role} | {HARNESS_ROLE}


@dataclass(frozen=True)
class Event:
    name: str
    t: int
    """Nanoseconds in ``clock``'s domain."""
    method: str
    clock: str
    resolution: int
    """Nanoseconds. For ``polled`` this is the poll interval, and nothing built on the event is
    reported more precisely than it."""
    credentials: tuple[str, ...]
    role: str
    batch_id: str | None = None
    attrs: dict[str, Any] | None = field(default=None, compare=True)

    def __post_init__(self) -> None:
        if not isinstance(self.t, int) or isinstance(self.t, bool) \
                or not isinstance(self.resolution, int) \
                or not all(isinstance(c, str) for c in self.credentials):
            raise BenchError(E_EVENT_FORMAT, f"The event {self.name!r} must carry integer "
                             f"nanosecond times and string credential ids.")
        if self.name not in MILESTONES and self.name not in HARNESS_EVENTS:
            raise BenchError(E_NAME, f"{self.name!r} is neither a milestone nor a harness event, "
                             f"so the timeline cannot carry it.")
        if self.method not in set(Method):
            raise BenchError(E_METHOD, f"The event {self.name!r} gives the method "
                             f"{self.method!r}; it must be observed, polled or reported.")
        if not self.clock:
            raise BenchError(E_CLOCK, f"The event {self.name!r} names no clock domain, so its "
                             f"time cannot be compared with any other.")
        if self.resolution < 0 or (self.method == Method.POLLED and self.resolution == 0):
            raise BenchError(E_RESOLUTION, f"The event {self.name!r} gives resolution "
                             f"{self.resolution} ns; a polled event needs its poll interval and "
                             f"no event may be negative.")
        if self.attrs is not None and not isinstance(self.attrs, dict):
            raise BenchError(E_EVENT_FORMAT, f"The event {self.name!r} carries attrs that are "
                             f"not an object.")
        owner = MILESTONES.get(self.name, HARNESS_ROLE)
        if self.role not in _ROLES or self.role != owner:
            raise BenchError(E_ROLE, f"The event {self.name!r} belongs to the {owner} role, so "
                             f"the {self.role!r} role cannot emit it.")

    def to_dict(self) -> dict:
        out = {"name": self.name, "t": self.t, "method": self.method, "clock": self.clock,
               "resolution": self.resolution, "credentials": list(self.credentials),
               "role": self.role}
        if self.batch_id is not None:
            out["batch_id"] = self.batch_id
        if self.attrs is not None:
            out["attrs"] = self.attrs
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, raw: dict) -> Event:
        try:
            return cls(name=raw["name"], t=raw["t"], method=raw["method"], clock=raw["clock"],
                       resolution=raw["resolution"], credentials=tuple(raw["credentials"]),
                       role=raw["role"], batch_id=raw.get("batch_id"), attrs=raw.get("attrs"))
        except (KeyError, TypeError) as missing:
            raise BenchError(E_EVENT_FORMAT, f"An event line lacks a required field or has the "
                             f"wrong shape ({missing}).") from None


@dataclass(frozen=True)
class Header:
    harness_commit: str
    harness_clock: str
    timeline_schema: int = TIMELINE_SCHEMA
    vocabulary_version: int = VOCABULARY_VERSION

    def to_json(self) -> str:
        return json.dumps({"timeline_schema": self.timeline_schema,
                           "vocabulary_version": self.vocabulary_version,
                           "harness_commit": self.harness_commit,
                           "harness_clock": self.harness_clock})


class TimelineWriter:
    """Appends events as they happen and flushes each line, so a crashed run keeps its timeline."""

    def __init__(self, path: Path, header: Header) -> None:
        self.path = Path(path)
        self._sink = open(self.path, "w", encoding="utf-8")
        self._sink.write(header.to_json() + "\n")
        self._sink.flush()

    def write(self, event: Event) -> None:
        self._sink.write(event.to_json() + "\n")
        self._sink.flush()

    def close(self) -> None:
        self._sink.close()

    def __enter__(self) -> TimelineWriter:
        return self

    def __exit__(self, *_) -> None:
        self.close()


def _lines(path: Path):
    with open(path, "rb") as source:
        for number, raw in enumerate(source, start=1):
            if len(raw) > MAX_LINE:
                raise BenchError(E_LINE_SIZE, f"Line {number} of {path} is longer than "
                                 f"{MAX_LINE} bytes, so it was not read.")
            yield number, raw


def _parse(number: int, raw: bytes, path: Path) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        raise BenchError(E_LINE_FORMAT, f"Line {number} of {path} is not JSON.") from None


def read_timeline(path: Path) -> tuple[Header, list[Event]]:
    """Read a timeline of any supported schema; refuse anything else with a coded error."""
    path = Path(path)
    lines = _lines(path)
    first = next(lines, None)
    if first is None:
        raise BenchError(E_HEADER, f"{path} is empty; a timeline opens with a header line.")
    raw = _parse(*first, path)
    if not isinstance(raw, dict) or not {"timeline_schema", "vocabulary_version",
                                        "harness_commit", "harness_clock"} <= raw.keys():
        raise BenchError(E_HEADER, f"The first line of {path} is not a timeline header.")
    if raw["timeline_schema"] not in SUPPORTED_SCHEMAS:
        raise BenchError(E_SCHEMA, f"{path} uses timeline schema {raw['timeline_schema']!r}, "
                         f"which this harness never emitted; it reads "
                         f"{sorted(SUPPORTED_SCHEMAS)}.")
    header = Header(harness_commit=raw["harness_commit"], harness_clock=raw["harness_clock"],
                    timeline_schema=raw["timeline_schema"],
                    vocabulary_version=raw["vocabulary_version"])
    events = []
    for number, line in lines:
        if not line.strip():
            continue
        item = _parse(number, line, path)
        if not isinstance(item, dict):
            raise BenchError(E_LINE_FORMAT, f"Line {number} of {path} is not a JSON object.")
        try:
            events.append(Event.from_dict(item))
        except BenchError as wrong:
            raise BenchError(wrong.code, f"Line {number} of {path}: {wrong.message}") from None
    return header, events
