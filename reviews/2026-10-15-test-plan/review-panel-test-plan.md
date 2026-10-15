# Test-plan review, 2026-10-15: synthesis and dispositions

status: closed. All 20 findings were accepted and applied. None remain open.

- **Target:** `docs/test-plan.md` at c15530b, the first draft.
- **Reviewers:** `testability-hawk` (TST-1..10) and `maintainability-expert` (MNT-1..10), from `bakobo/dev/review/`. Daniel asked for these two lenses (Q-WVN0) and named them as keri-review-panel personas, but that panel has no testability or maintainability lens, so bakobo's were used.
- **Reports:** `testability-hawk-test-plan.md` and `maintainability-expert-test-plan.md` beside this file.
- **Approval:** Daniel approved the plan on the condition that these findings were dispositioned and applied.

Three overlaps were merged before dispositioning:
- TST-2 with MNT-3: there is no clock model.
- TST-4 with MNT-5: `published` is defined only by an imbu example.
- TST-10 with MNT-9: the real adapters are never exercised.

| Id | Sev | Disposition | Where it landed |
|---|---|---|---|
| TST-1 | CRITICAL | Accepted. A calibration stack with known injected delays and caps, run in CI on every change. The tolerances are now numbers: 5% or the resolution for intervals, ±10% for saturation. | Part B #1 |
| TST-2 / MNT-3 | HIGH | Accepted. Three methods (`observed`, `polled`, `reported`), each with a clock domain. An interval across domains without a measured offset is not measured. | Part A "Observation method and clocks", Part B #4; `this.i` 7vsky4sk (f26150d) |
| TST-3 | HIGH | Accepted. The report gives proxy pass-through latency per link and the harness's own CPU. Polled resolution is enforced. Pass-through p99 must stay under 1 ms. | Statistics, Part B #7 |
| TST-4 | HIGH | Accepted. Each milestone is defined by the event that counts. A per-stack observability matrix is published, all seven pairs must be measurable on imbu, and any gap is a named deviation. | Milestones, Observability matrix |
| MNT-5 | MEDIUM | Accepted. `grant_sent` was added and pair 4 now starts from it. `published` is defined by role, and stack bindings move to adapter docs. | Milestones, pairs table |
| TST-5 | HIGH | Accepted. `soak-preseeded` separates drift caused by store size from drift over elapsed time. Drift is a slope with a 95% CI, and the soak has a stopping rule. | Soak |
| TST-6 | HIGH | Accepted. Saturation (ε, k), driver lag (L) and n ≥ 3 repetitions with spread are all defined. | Statistics, Part B #3 and #5 |
| TST-7 | MEDIUM | Accepted. A post-run reconciliation step classifies each credential's fate and counts double effects. | Faults, Part B #9 |
| TST-8 | MEDIUM | Accepted. `revocation_recognised` is the stable time across all verifiers, and the gap from the first time the revocation is seen is reported. | Milestones |
| TST-9 | MEDIUM | Accepted. Golden timelines are hand-computed. The percentile definition is named (HDR, 3 significant digits). No second coordinated-omission correction is applied. | Statistics, Part B #2 and #3 |
| TST-10 / MNT-9 | MEDIUM | Accepted. A weekly scheduled contract run against the real adapters. The census is checked against the hand-traced count. The hsm-sweep signer profile is checked before curves are drawn. Census rows read `not measured`, not zero. Adapters declare the stack versions they were tested against, and the report warns on any other version. | Part B #10 and #11, HSM sensitivity, Outputs |
| MNT-1 | HIGH | Accepted. Schema versions on the timeline, report and scenario. The generator reads every past timeline schema, and golden files are kept per version. | Outputs, Part B #2 |
| MNT-2 | HIGH | Accepted. Milestones are snake_case names, never renumbered, renamed or reused. | Milestones |
| MNT-4 | HIGH | Accepted. Events are the timeline's unit, each with `credentials: [ids]` and `batch_id`. A 3-credential batch golden timeline is added now. | Outputs, Part B #2; `this.i` 8q4myzru (f26150d) |
| MNT-6 | MEDIUM | Accepted. A Roles section was added, scenarios are named by role, and imbu component names stay in Topologies. | Roles, named areas |
| MNT-7 | HIGH | Accepted. Adapters declare versioned knob schemas, `n/a` is allowed only where declared, and the resolved set goes into provenance. | Knobs, Part B #6 |
| MNT-8 | MEDIUM | Accepted. Reports derive from the timeline plus `provenance.json`, never from live state. | Outputs |
| MNT-10 | LOW | Accepted. A "Depends on" list with each item's status. | Depends on |

Three things the reviewers did not check are carried to bench-harness:
- whether imbu-async's operation timestamps are monotonic or wall-clock (the `reported` method handles either, provided the offset is measured);
- how the summit harness's `spawn` handles clocks;
- the shape of the adapter interface, which is the first design decision bench-harness records in `this.i`.
