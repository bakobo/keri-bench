"""Reconciliation: what became of every credential the timeline names (test plan, Faults; B #9).

After a run the independent verifier and the witness pool's KELs are asked about each credential.
Each one is done, resumed (done, but its lifecycle overlapped a fault), or lost, and is flagged
when it was anchored twice or seen under two SAIDs. Those double effects are a first-class metric
in duplicate-fault scenarios. This module is a pure function of what was asked and answered.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .errors import BenchError
from .timeline import Event

E_STATUS = "e.input.range.reconcile.status.f"

FATES = ("done", "resumed", "lost")
FLAGS = ("double_anchored", "two_saids")
_DONE = frozenset({"valid", "revoked"})
_KNOWN = _DONE | {"unknown"}


@dataclass(frozen=True)
class Outcome:
    fate: str
    double_anchored: bool
    two_saids: bool
    saids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"fate": self.fate, "double_anchored": self.double_anchored,
                "two_saids": self.two_saids, "saids": list(self.saids)}


def _overlaps(start: int, end: int, faults: Sequence[tuple[int, int]]) -> bool:
    return any(start < fault_end and end > fault_start for fault_start, fault_end in faults)


def classify(events: Iterable[Event], verifier: Mapping[str, Mapping],
             anchors: Mapping[str, int | None], *,
             faults: Sequence[tuple[int, int]]) -> dict[str, Outcome]:
    """Classify every credential the run asked for: named by a ``requested`` event, or asked
    about in the verifier's answers. Ids that name something else, such as a cold sync, are not
    credentials and are left out.

    A lifecycle runs from the credential's ``requested`` event to its latest event on that same
    (harness) clock; events stamped on other clocks are not placed against fault windows.
    """
    by_credential: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        for credential in event.credentials:
            by_credential[credential].append(event)
    out = {}
    for credential, own in by_credential.items():
        if credential not in verifier and not any(e.name == "requested" for e in own):
            continue
        answer = verifier.get(credential, {"status": "unknown", "said": None})
        status = answer.get("status")
        if status not in _KNOWN:
            raise BenchError(E_STATUS, f"The verifier answered {status!r} for {credential}; "
                             f"reconciliation understands only valid, revoked and unknown.")
        saids = {e.attrs["said"] for e in own
                 if e.attrs and isinstance(e.attrs.get("said"), str)}
        if answer.get("said") is not None:
            saids.add(str(answer["said"]))
        fate = "lost"
        if status in _DONE:
            fate = "done"
            requested = [e for e in own if e.name == "requested"]
            if requested:
                start = min(e.t for e in requested)
                end = max(e.t for e in own if e.clock == requested[0].clock)
                if _overlaps(start, end, faults):
                    fate = "resumed"
        count = anchors.get(credential)
        out[credential] = Outcome(fate, double_anchored=count is not None and count > 1,
                                  two_saids=len(saids) > 1, saids=tuple(sorted(saids)))
    return out


def summary(outcomes: Mapping[str, Outcome]) -> dict[str, int]:
    counts = {"total": len(outcomes)}
    for fate in FATES:
        counts[fate] = sum(1 for o in outcomes.values() if o.fate == fate)
    for flag in FLAGS:
        counts[flag] = sum(1 for o in outcomes.values() if getattr(o, flag))
    return counts
