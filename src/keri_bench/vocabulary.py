"""The lifecycle vocabulary the harness owns; adapters report against it (this.i 7wt73dlu).

A milestone's key is its snake_case name. Names are added, never renumbered, renamed or reused,
and a removed name moves to ``RESERVED`` so it can never come back meaning something else
(test plan, Milestones). Bump ``VOCABULARY_VERSION`` whenever a name is added or reserved.
"""

from dataclasses import dataclass
from enum import StrEnum

VOCABULARY_VERSION = 1


class Role(StrEnum):
    ISSUER = "issuer"
    WITNESS_POOL = "witness_pool"
    STATE_DISTRIBUTION = "state_distribution"
    HOLDER = "holder"
    VERIFIER = "verifier"


class Method(StrEnum):
    """How an event's time was obtained (this.i 7vsky4sk)."""

    OBSERVED = "observed"
    """The harness's clock at a boundary it controls."""
    POLLED = "polled"
    """The harness's clock when a poll first saw the state; resolution is the poll interval."""
    REPORTED = "reported"
    """A timestamp the stack wrote, in its own clock domain."""


MILESTONES: dict[str, Role] = {
    "requested": Role.ISSUER,
    "accepted": Role.ISSUER,
    "constructed": Role.ISSUER,
    "registry_updated": Role.ISSUER,
    "anchored": Role.ISSUER,
    "grant_sent": Role.ISSUER,
    "revocation_requested": Role.ISSUER,
    "revocation_anchored": Role.ISSUER,
    "witnessed": Role.WITNESS_POOL,
    "revocation_witnessed": Role.WITNESS_POOL,
    "published": Role.STATE_DISTRIBUTION,
    "revocation_published": Role.STATE_DISTRIBUTION,
    "delivered": Role.HOLDER,
    "stored": Role.HOLDER,
    "proof_generated": Role.HOLDER,
    "verified": Role.VERIFIER,
    "revocation_recognised": Role.VERIFIER,
    # Added with the harness: pair 5 times a cold sync from the moment the verifier starts it.
    "sync_full_started": Role.VERIFIER,
    "sync_full_done": Role.VERIFIER,
    "sync_increment_applied": Role.VERIFIER,
}

RESERVED: frozenset[str] = frozenset()
"""Milestone names that were removed. They stay here forever so they are never reused."""

HARNESS_EVENTS: dict[str, str] = {
    "dispatched": "The driver handed a scheduled request to the issuer adapter (send lag).",
    "clock_offset": "One measured offset from a stack clock domain to the harness's.",
    "fault_started": "A scheduled fault began on a link or a role.",
    "fault_ended": "A scheduled fault ended.",
    "process_killed": "The harness SIGKILLed a role's process.",
    "process_ready": "A role's process announced it was ready, at start or after a restart.",
    "run_started": "The measured window began.",
    "run_ended": "The measured window ended.",
    "stage_failed": "A credential's lifecycle stage failed; attrs name the stage and the error.",
    "fault_failed": "A scheduled fault could not be applied; the run is refused.",
    "preseed_started": "The harness began filling the store before the window (this.i 9yx6cg92).",
    "preseed_ended": "The preseed finished; attrs give how many were requested and failed.",
    "footprint": "One sample of the bytes the issuer's store occupies (this.i 7f9jfc99).",
    "footprint_failed": "A store-size sample could not be read; attrs give why. Sampling goes on.",
    "stop_reached": "A soak's schedule ended; attrs say whether credentials or time ended it.",
}
"""Events the harness itself writes. They share the timeline with milestones but never a name."""


def milestones_of(role: Role) -> set[str]:
    return {name for name, owner in MILESTONES.items() if owner is role}


@dataclass(frozen=True)
class Pair:
    """One of the seven pairs KERI-006 says the plan must keep apart, and how it is measured."""

    key: str
    utah: str
    """Utah's words, verbatim, without the surrounding quotation marks. The seventh line of Utah's
    list has no closing full stop, so neither does its copy here."""
    intervals: tuple[tuple[str, str], ...] = ()
    throughput: tuple[str, ...] | None = None
    """Milestones whose completion rates are reported separately, for a throughput pair."""

    def milestones(self) -> set[str]:
        names = {name for interval in self.intervals for name in interval}
        return names | set(self.throughput or ())


PAIRS: tuple[Pair, ...] = (
    Pair("construction_vs_registry",
         "Credential construction throughput from registry update throughput.",
         throughput=("constructed", "registry_updated")),
    Pair("anchor_vs_witness",
         "TEL anchoring latency from witness confirmation latency.",
         intervals=(("registry_updated", "anchored"), ("anchored", "witnessed"))),
    Pair("proof_vs_verification",
         "Generation of a proof from independent verification of that proof.",
         intervals=(("stored", "proof_generated"), ("proof_generated", "verified"))),
    Pair("delivery_vs_storage",
         "Delivery to the holder from acceptance and secure storage by the holder.",
         intervals=(("grant_sent", "delivered"), ("delivered", "stored"))),
    Pair("sync_full_vs_incremental",
         "Initial synchronization from incremental updates.",
         intervals=(("sync_full_started", "sync_full_done"),
                    ("published", "sync_increment_applied"))),
    Pair("revocation_vs_recognition",
         "Successful revocation creation from the point when a verifier can reliably recognize "
         "that revocation.",
         intervals=(("revocation_requested", "revocation_published"),
                    ("revocation_published", "revocation_recognised"))),
    Pair("normal_vs_faulted",
         "Performance under normal load from performance after crashes, partitions, delays, "
         "duplicate messages, and recovery"),
)


ISSUANCE_CHAIN = ("requested", "accepted", "constructed", "registry_updated", "anchored",
                  "witnessed", "published", "grant_sent", "delivered", "stored",
                  "proof_generated", "verified")
REVOCATION_CHAIN = ("revocation_requested", "revocation_anchored", "revocation_witnessed",
                    "revocation_published", "revocation_recognised")
"""A credential's lifecycle in order. Each adjacent pair of milestones a run observes is reported
as a step, so a whole lifecycle reads as a sum of its parts."""
