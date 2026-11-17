# keri-bench test plan

Status: approved by Daniel on 2026-10-15 (Q-WVN0), on the condition that the testability and maintainability reviews were dispositioned and applied first. They were: see `reviews/2026-10-15-test-plan/review-panel-test-plan.md`. The plan expands the KERI-006 interview (`bakobo/utah-keri-adoption`, `design/bench-design/interview.md`), and the intentions it rests on are in `this.i`.

The plan has two parts. Part A, the measurement plan, is what Utah asked for. Part B covers how the harness's own code is tested, including how we know the harness measures correctly.

## Part A: the measurement plan

### Acceptance standard

Utah's KERI-006 asks for "Load and soak tests covering throughput, persistence, concurrency, restart recovery, witnessing, TEL replay, and wallet delivery." Its test-plan note adds: "Testing should measure end-to-end issuance, not simply the time required to generate an ACDC or compute a Merkle root". It also lists seven pairs that the plan must at minimum distinguish. They are quoted verbatim in the pairs table below.

### Roles

A run is assembled from one adapter per role (`this.i` 7wt73dlu). Each role emits only its own milestones.

| Role | Emits |
|---|---|
| issuer | `requested`, `accepted`, `constructed`, `registry_updated`, `anchored`, `grant_sent`, `revocation_requested`, `revocation_anchored` |
| witness pool | `witnessed`, `revocation_witnessed` |
| state distribution | `published`, `revocation_published` |
| holder | `delivered`, `stored`, `proof_generated` |
| verifier | `verified`, `revocation_recognised`, `sync_full_done`, `sync_increment_applied` |

### Milestones

Each milestone's stable key is its snake_case name. Names are added, never renumbered, renamed or reused. A removed name stays reserved. The definitions say which observable event counts.

| Name | Observable event |
|---|---|
| `requested` | The scheduled send time of the request, on the driver's clock. This is not the actual send time; see Statistics. |
| `accepted` | The issuer acknowledged the request. For an async API this is the 202 response; for a sync one it is absent. |
| `constructed` | The ACDC body exists, as reported by the issuer. |
| `registry_updated` | The TEL event exists. |
| `anchored` | The KEL event carrying the TEL seal is signed by the issuer. |
| `witnessed` | Receipts reached the configured toad. |
| `published` | The state-distribution role acknowledged the TEL event. |
| `grant_sent` | The grant left the issuer side, or the harness relay when the stack doesn't deliver. |
| `delivered` | The grant was accepted by the holder's endpoint. |
| `stored` | The holder's admit returned after persisting. |
| `proof_generated` | The holder produced a presentation. |
| `verified` | The independent verifier returned valid. |
| `revocation_requested`, `revocation_anchored`, `revocation_witnessed`, `revocation_published` | The same definitions as above, for the revocation event. |
| `revocation_recognised` | The first time after which every later poll by every configured verifier returns revoked. The report also gives the gap from the first poll that returned revoked. |
| `sync_full_done` | A cold verifier finished its initial synchronization. |
| `sync_increment_applied` | The verifier applied one incremental update. |

Stack-specific bindings (for example, "on the imbu stack, `published` is the Registrar's 2xx") live in each adapter's documentation, not here.

### Observation method and clocks

Every timeline event carries a `method` and a `clock`:
- `observed`: the harness's monotonic clock at a boundary it controls (the driver, the proxy, or an API call it makes).
- `polled`: the harness's clock when a poll first saw the state. The poll interval is recorded as the event's resolution, and no interval built on it is reported more precisely than that.
- `reported`: a timestamp the stack itself wrote, for example in imbu's async operation resource. It carries its clock domain, and it is usable only when the offset from that domain to the harness's clock was measured during the run. The measured bound is carried into every interval that uses it.

An interval whose endpoints are in different clock domains, with no measured offset, is "not measured". A milestone an adapter cannot observe is `unobserved`, never interpolated.

### Observability matrix

For each stack, the harness publishes a matrix covering every milestone: the observation point, the method, and the resolution. Each of Utah's seven pairs must be measurable on the imbu stack. A pair that isn't measurable is a named deviation in the report to Utah, not a silent blank.

### The seven pairs

| Pair (Utah's words) | Measured as | Scenarios |
|---|---|---|
| "Credential construction throughput from registry update throughput." | `constructed` and `registry_updated` completions per second, at saturation (defined under Statistics), reported separately. | `issue-steady`, `bulk-steady` |
| "TEL anchoring latency from witness confirmation latency." | `registry_updated`→`anchored` and `anchored`→`witnessed`. | `issue-steady`, `witness-sweep` |
| "Generation of a proof from independent verification of that proof." | `stored`→`proof_generated` and `proof_generated`→`verified`. The verifier is named. | `present-verify` |
| "Delivery to the holder from acceptance and secure storage by the holder." | `grant_sent`→`delivered` and `delivered`→`stored`. The channel is named. | `deliver-steady` |
| "Initial synchronization from incremental updates." | The time to `sync_full_done` from a cold verifier as a function of registry count, and per-update `sync_increment_applied` latency. | `sync-cold`, `sync-incremental` |
| "Successful revocation creation from the point when a verifier can reliably recognize that revocation." | `revocation_requested`→`revocation_published` and `revocation_published`→`revocation_recognised`, swept over the distribution window and the verifier poll interval. | `revoke-window-sweep` |
| "Performance under normal load from performance after crashes, partitions, delays, duplicate messages, and recovery." | Every steady scenario rerun with one scheduled fault, reporting the before, during and after windows measured from the fault's timeline event. | `fault-*` |

### KERI-006's named areas

| Area | Scenarios |
|---|---|
| Throughput | `issue-steady` and `bulk-steady` at 1 home and N homes, with offered load stepped to saturation |
| Persistence | `soak` and `soak-preseeded` (see Soak) |
| Concurrency | `issue-steady` with K concurrent clients per home, and N homes |
| Restart recovery | `fault-crash-issuer`, `fault-crash-witness`, `fault-crash-state-distribution`, `fault-crash-holder` |
| Witnessing | `witness-sweep` (witness count, toad), `fault-partition-witness` |
| TEL replay | `sync-cold`, `fault-crash-state-distribution`, `fault-duplicate-state-batch` |
| Wallet delivery | `deliver-steady`, `fault-partition-holder` |

### Statistics

- **Latency.** Latency is measured from `requested`, the scheduled time. That is the coordinated-omission correction, and no second correction is applied on top. Percentiles use one named definition: HDR histogram with 3 significant digits, quoted with the bucket's upper bound.
- **Saturation.** Saturation is the offered-load step at which achieved throughput rises by less than ε = 2% over the previous step, or p99 exceeds k = 5 times its value at the lowest step. The scenario may override ε and k, and both are recorded.
- **Driver lag.** The driver has fallen behind when p99 send lag (actual minus scheduled send) exceeds L = 5 ms, or when the backlog grows across three consecutive one-second windows. Such a run is refused.
- **Repetitions.** Each reported point is the result of n ≥ 3 runs, and the report gives the spread between runs as well as the pooled distribution.
- **Measurement cost.** For each link, the report gives the proxy's pass-through latency, measured against a fake peer on the same host in the same run. It also gives the CPU used by the harness's own processes.

### Soak

Throughput measured against store size, and against time, can't be told apart under constant load, so `soak` is paired with `soak-preseeded`. The preseeded run starts from a store pre-filled to size S. Comparing runs at equal elapsed time with different store sizes, and at equal store size with different elapsed times, separates the two causes of drift. Drift is the slope of p50 and p99 per 10^k credentials, with a 95% confidence interval. A soak stops at a declared credential count or a declared duration, whichever comes first, and both are recorded.

### Faults

Every fault is scheduled in the scenario file and recorded as a timeline event (`this.i` 0wcr2rrj):
- **crash** SIGKILLs a role mid-operation and restarts it.
- **partition** has the proxy drop a link for a set duration.
- **delay** has the proxy add a fixed or distributed latency to a link.
- **duplicate** has the proxy replay real messages between roles: an event POST to the witness pool, a state batch to the verifier, or a grant to the holder.

After every run, a reconciliation step asks the independent verifier, and the witness pool's KELs, about every credential id in the timeline. It classifies each one as done, resumed after a fault, or lost. It flags any credential anchored twice or verified under two SAIDs. Double effects are a first-class metric in duplicate-fault scenarios. For crashes, the report also gives the time to ready and the time to the first new success.

### HSM sensitivity (`this.i` 4jlx9k8q)

The report includes three things:
- **A signature census per role, per lifecycle.** It counts the signer's own operations and checks them against the counts the timeline implies.
- **A microbenchmark** of Ed25519 sign and verify and Blake3-256 on the host.
- **`hsm-sweep`.** The issuer signs through an external-signer interface with a reference signer that caps latency and rate. Latency and cap are swept over a grid, and the curves are for `requested`→`witnessed` throughput and latency. Before any curve is reported, the sweep checks that the signer's measured per-signature latency and rate match the configured profile within 5%.

Hashing and Merkle census rows read `not measured` until batch commitment exists in a stack under test.

### Knobs

Each role adapter declares a versioned schema of its policy knobs. Examples are the distribution window, witness count and toad, the gap grace, the verifier's freshness bound and poll interval, and the number of homes. A scenario must give every declared knob a value, or `n/a` where the adapter declares that knob not applicable. Anything else is refused (`this.i` 4q4540c4). The resolved knob set and every schema version go into provenance.

### Topologies

- **imbu stack.** The imbu issuer uses async operations, so `accepted`, `anchored` and `witnessed` come as `reported` timestamps with a measured offset. The witness pool is three keripy witnesses at toad 2. State distribution is the Bakobo Registrar plus the heti Observer, with imbu's publisher wired. The holder is heti and the verifier is heti, plus a keripy verifier where one exists. It runs at 1 home and at N homes.
- **KERIA stack.** KERIA is driven through signifypy, with the same witnesses and verifier. The report states that client signing is inside KERIA's numbers.

### Outputs

One directory per run:
- `timeline.jsonl` opens with a header line: `timeline_schema`, `vocabulary_version`, `harness_commit`. Each following line is one event, with `name`, `t`, `method`, `clock`, `resolution`, `credentials: [ids]` and an optional `batch_id`. One event that anchors or publishes many credentials is one line, not N lines.
- `provenance.json` holds component commits and versions, adapter schema versions, the resolved knobs, the host fingerprint, the scenario file and its hash, and the stack versions each adapter was last contract-tested against.
- `report.md` and `report.json` carry `report_schema` and the generator commit. Both are derived from the timeline and `provenance.json` only, never from live state. The report warns when a run used a stack version its adapter was never contract-tested against.

The report generator accepts every timeline schema it has ever emitted.

## Part B: tests of the harness itself

Development is strict TDD, with 100% branch coverage of new code, run by `uv run pytest`. Unit tests need no KERI stack.

1. **Calibration (end-to-end).** A synthetic stack provides one fake adapter per role, each with a seeded, known delay distribution and a known throughput cap for every milestone transition. A full run (driver, proxy, adapters, timeline, report) against this stack must:
   - recover each injected interval's p50 and p99 within the larger of 5% or the observation resolution;
   - report the saturation point within ±10%;
   - show an injected stall in the tail.

   Those three bars are the strict gate. Each of them can be moved by a busy host, because every harness-observed interval also counts the round trip to the role, so on a shared CI runner they fail about one run in ten without any change to the harness. The calibration therefore also has a bounds gate, which checks only what load cannot move: every injected transition is measured for every credential; each measured interval, widened by its resolution and offset bounds, is at least what had elapsed inside the role when it began the effect ending the transition; each offset to a skewed stack clock agrees with the realtime offset less the injected skew, within the offsets' bounds; the stall's credential reaches the stall's length; and the saturation detector fires, at no more than the cap plus 10%.

   CI runs the calibration live on every change at the bounds gate, and replays a calibration recorded on the bench host (`tests/golden/calibration/`) at the strict gate, rebuilding its reports with the code under test, so nothing in CI depends on timing. The bench host runs it live at the strict gate before every run of record, and its measured error budget is printed in each real run's provenance; a run whose calibration passed only the bounds gate says so in its warnings. It is the test that the harness measures correctly (`this.i` 0hykugwr).
2. **Timeline to report.** The golden timelines are small and hand-computed, with their expected values derived inside the test file, not produced by the code under test. One timeline has a 3-credential batch. Golden timelines are kept for every past `timeline_schema`, and each must still produce its recorded report.
3. **Statistics.** The named percentile definition is tested at bucket boundaries. A synthetic stall of duration D adds exactly the expected number of samples at D or above. The saturation detector is tested on fixed throughput curves just above and just below ε and k.
4. **Clocks and methods.** An interval across clock domains with no measured offset yields "not measured". A `polled` interval is never reported finer than its resolution. A `reported` timestamp without its domain is refused.
5. **Open-loop driver.** Requests keep going out on schedule while the system under test stalls. A run is refused at L + δ send lag and accepted at L − δ.
6. **Scenario and provenance.** Missing knobs, an `n/a` the adapter doesn't allow, and a missing schema version are each refused with a coded error. Provenance contains every required field.
7. **Fault proxy.** Each mode is tested against a fake peer:
   - delay: the added latency is within tolerance;
   - partition: no bytes pass, then traffic resumes;
   - duplicate: the peer sees exactly two copies;
   - pass-through: the bytes are identical and the added latency is at p99 under 1 ms on one host.
8. **Process control.** A killed fake role is restarted, crash events land in the timeline, and children die with the harness.
9. **Reconciliation.** Seeded fake outcomes (done, resumed, lost, double-anchored, two SAIDs) are classified correctly.
10. **Adapter contract.** One contract suite per role. It checks that the adapter declares its milestones, methods and clocks, accepts configured endpoints, emits no undeclared milestone, and has a valid knob schema. It runs against the fakes on every change. A scheduled CI job runs it weekly against each real adapter on its pinned stack versions, and a drift fails that job.
11. **Census.** On the imbu stack, the census matches the hand-traced count: three issuer signatures per single issuance (the `ixn`, the grant and the HTTP response), plus one receipt per witness. A change in the count fails the scheduled job until the expected value is updated.

## Depends on

The status of each dependency is current as of 2026-10-15, from plan `utah-keri-adoption`:

- An external-signer interface in heti (plan item heti-signer, proposed). `hsm-sweep` needs it.
- Async operations with per-milestone timestamps in imbu (plan item imbu-async, proposed). This is what makes the issuer's milestones observable as `reported` rather than `unobserved`.
- Holder delivery in imbu (proposed item imbu-delivery, imbu tick `~56zt`). Until it exists, the harness relays the grant and the report names the channel as the relay.

## Left to later items

- Pass/fail against Utah's targets, once Utah answers.
- Multi-host runs, which are a later deployment of the same harness and are labelled as such.
- Batch-commitment scenarios (KERI-002/003), which the event-based timeline can already represent.
