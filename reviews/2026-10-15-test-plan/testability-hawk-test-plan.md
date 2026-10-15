# testability-hawk on docs/test-plan.md (c15530b)

Reviewer: the `testability-hawk` persona (`bakobo/dev/review/testability-hawk.md`), run as a Claude subagent on 2026-10-15 at Daniel's request (Q-WVN0). Read-only. The text is as returned.

The plan has 10 problems. The worst is that nothing checks that the harness measures correctly as a whole: Part B tests each component in isolation, and no test runs an issuance through the full chain (driver, proxy, adapter, timeline, report) and checks the reported numbers against known values. Six findings concern measurement validity and four concern the oracles in Part B.

**TST-1 · CRITICAL · Part B as a whole: no end-to-end calibration.** The harness could report a wrong M2→M3 interval while every unit test passes. Causes include swapped milestone labels, a polling adapter that stamps the poll time instead of the event time, and proxy overhead counted as stack latency. Proposed: add a calibration stack, a synthetic issuer, witness, distribution, holder and verifier, each with a seeded, known delay distribution and a known throughput cap per milestone transition. A full run against it must recover each injected interval's p50 and p99 within a stated tolerance, report its saturation point within ±X%, and show an injected stall in the tail. Every release runs it, and the error budget is printed in each report's provenance.

**TST-2 · HIGH · Outputs and `inside` labelling: no clock model.** The interview specified a monotonic clock in one harness process, but the plan never mentions clocks. Inside probes and KERIA operation timestamps come from other processes and are likely wall-clock. Pair 2 then spans an `inside` M3 and an `outside` M4, so mixed clocks give meaningless intervals. Proposed: every timestamp records its clock source. An interval whose endpoints come from different sources is reported only when the conversion between them was measured during the run, and it carries that error bound. Otherwise the interval is "not measured". Add a Part B test for this.

**TST-3 · HIGH · Measurement overhead and resolution are missing.** Test 5 checks that pass-through bytes are identical, not that pass-through adds no latency. Polled milestones are quantised to the poll interval, and the driver, proxy and stack share the box's CPU. Proposed: per link, the report states the proxy's pass-through latency, measured against a fake peer on the same host. Per milestone, it states the observation resolution: the poll interval, or "event-driven". It records the harness's own CPU use. Test 5 asserts a bound on the pass-through p99 added latency. A polled milestone is never reported more precisely than its poll interval.

**TST-4 · HIGH · The seven pairs: the result can be entirely "not measured".** "Unobserved" is legitimate, so a run where most pairs read "not measured" still satisfies the plan. M1 ("built") and M5 ("for example the Registrar's acknowledgement") have no operational definition. Proposed:
- An observability matrix per stack: each milestone with its observation point, method and resolution.
- Each of Utah's seven pairs must be measurable on at least the imbu stack. Any gap is a named deviation reported to Utah.
- Each milestone's definition names the observable event.

**TST-5 · HIGH · Soak: time-driven and size-driven decay can't be separated.** At constant offered load, the number of credentials issued and elapsed time are almost perfectly collinear. Proposed:
- Pair `soak` with `soak-preseeded`, which starts from a store pre-filled to size S.
- Compare runs with the same elapsed time and different store sizes, and runs with the same store size and different elapsed times.
- Define drift as the slope of p50 and p99 per 10^k credentials, with a confidence interval.
- Name the soak's stopping rule.

**TST-6 · HIGH · "At saturation" and "falls behind" have no definition, so no oracle.** Proposed definitions:
- Saturation: the offered-load step where achieved throughput stops rising by more than ε, or where p99 exceeds k times its low-load value. ε and k are recorded.
- Fallen behind: send lag at p99 exceeds L, or the backlog grows across a window.
- Test 3 refuses a run at L+δ and accepts one at L−δ.
- n runs per point, with the between-run spread reported.

**TST-7 · MEDIUM · Crash and duplicate outcomes have no oracle.** Proposed:
- A post-run reconciliation step queries the independent verifier and the witnesses' KELs for every credential id. It classifies each one, and flags anything anchored twice or verified under two SAIDs.
- Duplicate-fault scenarios report double effects as a metric.
- Part B tests the reconciliation step against seeded fake outcomes.

**TST-8 · MEDIUM · "Reliably recognize": R4 counts the first poll that returns revoked.** Proposed: R4 is the first time after which every later poll by every configured verifier returns revoked. The report also gives the gap between first-seen and stable recognition.

**TST-9 · MEDIUM · Tests 1 and 2: circular golden files and an ambiguous percentile reference.** Golden files produced by the code under test can only detect change. The plan also doesn't say which percentile definition it uses. "Coordinated-omission correction applied when latency is measured from the scheduled time" conflates two things: measuring from the schedule *is* the correction, and applying an HDR-style correction on top would double-count. Proposed:
- Small, hand-computed golden timelines.
- A named percentile definition, tested at bucket boundaries.
- Latency measured from M0 with no further correction, and a stall of D adds exactly the expected number of samples at D or above.

**TST-10 · MEDIUM · Real adapters are never guarded.** Real adapters run only behind the opt-in integration mark, so the code that maps stack states onto milestones is either mocked or excluded from coverage. The census test is circular. Proposed:
- A scheduled CI job runs the contract suite against each real adapter, with pinned stack versions.
- The census is checked against hand-traced counts: imbu signs three times per single issuance, the `ixn`, the grant and the HTTP response.
- `hsm-sweep` asserts that the signer's measured latency and rate match the configured profile before it reports a curve.

**Done well:** M0 is the scheduled time, `unobserved` is never interpolated, and unset knobs are refused.

**Not checked:** whether imbu-async timestamps are monotonic or wall-clock; whether a calibration stack fits the bench-baseline schedule; how the summit harness's `spawn` handles clocks.
