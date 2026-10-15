# maintainability-expert on docs/test-plan.md (c15530b)

Reviewer: the `maintainability-expert` persona (`bakobo/dev/review/maintainability-expert.md`), run as a Claude subagent on 2026-10-15 at Daniel's request (Q-WVN0). Read-only. The text is as returned.

There are 9 numbered findings (MNT-1 to MNT-9) plus one LOW (MNT-10). The plan defines outputs but not the contracts between parts:
- no versioning for the timeline, report or scenario files;
- the adapter contract exists only in its test;
- imbu-stack nouns leak into supposedly stack-agnostic names.

**MNT-1 · HIGH · Outputs, Part B #1: no schema versions.** Pinning byte-stable golden files means the first change to a milestone or knob forces a choice: regenerate every golden, so old runs silently stop being re-derivable, or freeze the vocabulary. That breaks `this.i` 8q4myzru.

Proposed:
- `timeline.jsonl` starts with a header carrying `timeline_schema`, `vocabulary_version` and `harness_commit`.
- `report.json` carries `report_schema` and the generator commit.
- Scenario files carry `scenario_schema`.
- The generator accepts every timeline schema it has ever emitted.
- Golden timelines are kept for every past version.

**MNT-2 · HIGH · Milestones are ordinal ids.** imbu-async will add "accepted" (the 202) between M0 and M1, and KERI-002/003 will add batch and Merkle milestones. Either change forces renumbering or `M1a`.

Proposed:
- The stable key is a snake_case name that is never reused or renamed.
- The ordinal is a display column only.
- The pairs table is written in names.
- Milestones are added, never renumbered, and a removed name stays reserved.

**MNT-3 · HIGH · `outside`/`inside` is too coarse.** Polling imbu-async's operation resource reads timestamps written by imbu's own clock. A maintainer will label that `outside`, which is exactly the conflation 7vsky4sk exists to prevent. KERIA's `depends` chain has the same issue. The interview's clock decision is also missing.

Proposed three methods:
- `observed`: the harness clock at a boundary.
- `polled`: the harness clock, with the poll interval as its error bound.
- `reported`: the stack's clock, with that clock's domain and its offset to the harness clock stated.

Every line carries `method` and `clock`. Also edit 7vsky4sk, whose sentence about async imbu is misleading.

**MNT-4 · HIGH · "One line per milestone per credential" can't represent batches.** One `ixn` anchors many credentials, and one Registrar batch publishes many states. Writing N identical lines skews the census and counts one event N times.

Proposed:
- `event` becomes the unit, with `credentials: [ids]` and an optional `batch_id`.
- Per-credential intervals are joined in the report.
- A golden timeline with a 3-credential batch is added now.

**MNT-5 · MEDIUM · M5 is defined with an imbu example, and pair 4 starts from M5.** KERIA has no Registrar. Grant delivery doesn't wait on publication, so with a 900 s window M5→M6 is negative or meaningless.

Proposed:
- Add `grant_sent` and measure `grant_sent` → `delivered`.
- Define `published` by role.
- Move stack examples into the adapter docs.

**MNT-6 · MEDIUM · Roles are never listed, and scenario names use imbu component nouns.** Examples are `fault-crash-observer` and the "Registrar batch". On KERIA these names point at nothing, and later renames break the scenario hashes recorded in old provenance.

Proposed:
- A Roles section listing each role's milestones.
- Scenarios named by role.
- imbu component names kept in Topologies.

**MNT-7 · HIGH · Nobody defines the knob set.** If the harness hardcodes imbu's knobs, KERIA scenarios must invent values. If adapters add knobs, archived scenarios can't be rerun.

Proposed:
- Each role adapter declares a versioned knob schema.
- A scenario gives every knob a value or `n/a`, and `n/a` is allowed only where the adapter declares the knob not applicable.
- The resolved set and the schema versions go into provenance.

**MNT-8 · MEDIUM · "Derived from the timeline only" conflicts with what the report must name.** The report names the verifier, channel, sweep axes and signer profile, and all of those live in provenance. Proposed: "derived from the timeline and `provenance.json` only, never from live state."

**MNT-9 · MEDIUM · Census rows that "read zero" contradict the never-interpolate rule, and the adapters will rot.** Proposed:
- Census rows read `not measured`.
- Each adapter declares the stack versions it was last contract-tested against, provenance records them, and the report warns on an untested stack version.
- The integration mark runs on a schedule.

**MNT-10 · LOW · Plan-item names from another repo appear inline as preconditions.** Proposed: a "Depends on" list with each item's status, and the body written in terms of roles.

**Done well:** the reasoning for M0 is stated in place, `unobserved` is never interpolated, and pass/fail is deferred cleanly.

**Not covered:** the adapter interface's shape, since the plan doesn't define it beyond its contract test.
