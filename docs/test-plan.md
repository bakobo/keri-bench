# keri-bench test plan

Status: proposed 2026-10-15 and awaiting Daniel's approval. It expands the outline in the KERI-006 interview (`bakobo/utah-keri-adoption`, `design/bench-design/interview.md`), whose six intent questions he confirmed that day. The intentions it rests on are in `this.i`.

The plan has two parts. Part A is what the instrument measures, which is the plan Utah asked for. Part B is how the harness's own code is tested.

## Part A: the measurement plan

### Acceptance standard

Utah's KERI-006: "Load and soak tests covering throughput, persistence, concurrency, restart recovery, witnessing, TEL replay, and wallet delivery." Its test-plan note: "Testing should measure end-to-end issuance, not simply the time required to generate an ACDC or compute a Merkle root," and the plan must at minimum distinguish seven pairs. They are quoted verbatim in each row of the table below.

### Milestones

These are the milestones recorded per credential. The harness owns this vocabulary (`this.i` 7wt73dlu).

| Id | Milestone | Meaning |
|---|---|---|
| M0 | requested | The driver sent the request (driver clock, at the scheduled time, not the send time). |
| M1 | constructed | The ACDC was built. |
| M2 | registry updated | The TEL event was created. |
| M3 | anchored | The KEL event carrying the seal was signed locally. |
| M4 | witnessed | Receipts reached toad. |
| M5 | published | State was handed to distribution, for example the Registrar's acknowledgement. |
| M6 | delivered | The grant reached the holder's endpoint. |
| M7 | stored | The holder admitted it and persisted it. |
| M8 | proof generated | The holder produced a presentation. |
| M9 | verified | The independent verifier returned valid. |
| R0–R4 | revocation requested, anchored, witnessed, published, recognised | Recognised means the independent verifier returns revoked. |
| S1 / S2 | verifier full sync done / incremental update applied | |

Each recorded milestone carries its observation method, `outside` or `inside` (7vsky4sk). A milestone an adapter cannot observe is recorded as `unobserved`, never interpolated.

### The seven pairs

| Pair (Utah's words) | Measured as | Scenario |
|---|---|---|
| "Credential construction throughput from registry update throughput." | The completion rates of M1 and M2 per second, at saturation, reported separately. | `issue-steady`, `bulk-steady` |
| "TEL anchoring latency from witness confirmation latency." | M2→M3 and M3→M4 distributions. | `issue-steady`, `witness-sweep` |
| "Generation of a proof from independent verification of that proof." | M7→M8 and M8→M9. The verifier role is named in the report. | `present-verify` |
| "Delivery to the holder from acceptance and secure storage by the holder." | M5→M6 and M6→M7. The delivery channel is named. | `deliver-steady` |
| "Initial synchronization from incremental updates." | Time to S1 from a cold verifier as a function of registry count, and S2 latency per update. | `sync-cold`, `sync-incremental` |
| "Successful revocation creation from the point when a verifier can reliably recognize that revocation." | R0→R3 and R3→R4, swept over the Registrar window and the verifier poll interval. | `revoke-window-sweep` |
| "Performance under normal load from performance after crashes, partitions, delays, duplicate messages, and recovery." | Every steady scenario is rerun with a scheduled fault. The report shows the before, during and after windows, measured from the fault's timestamp. | `fault-*` |

### KERI-006's named areas

| Area | Scenarios |
|---|---|
| Throughput | `issue-steady`, `bulk-steady` at 1 home and N homes, offered load stepped to saturation |
| Persistence | `soak` (drift against credentials issued and database size, 4xtyfdd0) |
| Concurrency | `issue-steady` with K concurrent clients per home, and N homes |
| Restart recovery | `fault-crash-issuer`, `fault-crash-witness`, `fault-crash-observer`, `fault-crash-holder` |
| Witnessing | `witness-sweep` (witness count and toad), `fault-witness-partition` |
| TEL replay | `sync-cold`, `fault-crash-observer` (a restart forces a full resync), `fault-duplicate-batch` |
| Wallet delivery | `deliver-steady`, `fault-holder-partition` |

### Faults

Every fault is scheduled in the scenario file and recorded in the timeline (0wcr2rrj):

- **crash:** SIGKILL a role mid-operation, then restart it. Report the time to ready, the time to the first new success, and the fate of each in-flight credential: done, resumed or lost.
- **partition:** the proxy drops a link for a set duration.
- **delay:** the proxy adds a fixed or distributed latency to a link.
- **duplicate:** the proxy replays real messages: an event POST to a witness, a Registrar batch to the Observer, a grant to the holder.

### HSM sensitivity (4jlx9k8q)

- A signature census per role per lifecycle, taken from the timeline and the signer's own counter.
- A microbenchmark of Ed25519 sign and verify and Blake3-256 on the host.
- `hsm-sweep`: the issuer runs heti's external-signer interface (plan item heti-signer) with the reference latency and rate-capped signer. Latency and cap are swept over a grid, and the report gives end-to-end M0→M4 throughput and latency curves. The KERIA equivalent goes through Signify's `ExternalModule`.

### Topologies and stacks

- **imbu stack:** imbu (async, per plan item imbu-async) with its publisher wired, three witnesses at toad 2 by default, the Registrar, the heti Observer, a heti holder, and a heti verifier, plus a keripy verifier where one exists. Run at 1 home and N homes, where N is set per scenario.
- **KERIA stack:** KERIA driven through signifypy, with the same witnesses and the same independent verifier. The report states that client signing is inside KERIA's numbers.
- Every policy knob is set explicitly in the scenario file (4q4540c4).

### Outputs

Each run produces one directory containing:
- `timeline.jsonl`
- `provenance.json`: commits, versions, knobs, host fingerprint, scenario file and its hash.
- `report.md` and `report.json`, both derived from the timeline only.

## Part B: tests of the harness itself

All tests use strict TDD, at 100% branch coverage of new code, run with `uv run pytest`. They need no KERI stack. The real stacks appear only in an opt-in `integration` mark.

1. **Timeline to report.** Golden timelines produce golden reports. Recomputing from a timeline is byte-stable. An `unobserved` milestone yields "not measured" and never a number.
2. **Statistics.** The percentiles match a reference implementation on fixed data. The coordinated-omission correction is applied when latency is measured from the scheduled time, and a synthetic stall shows up in the tail.
3. **Open-loop driver.** Requests go out on schedule while the system under test stalls. A driver that falls behind is detected and the run is refused.
4. **Provenance.** A scenario with any unset knob is refused with a coded error. Provenance contains every required field.
5. **Fault proxy.** Run against a fake peer, it checks delay (the added latency appears within tolerance), partition (no bytes pass, then traffic resumes), duplicate (the peer sees exactly two copies), and pass-through (bytes are identical).
6. **Process control.** A killed fake role is restarted. Crash timestamps land in the timeline, and children die with the harness (PDEATHSIG).
7. **Adapter contract.** One contract suite per role, run against a fake adapter for each role and later against every real adapter. It checks the declared milestones, `outside`/`inside` labelling, configured endpoints, and that an undeclared milestone is never emitted.
8. **Signer census.** The census counts from a fake signer match what the timeline implies.

## What this plan leaves to later items

- Pass/fail against Utah's targets: plan item bench-targets, once Utah answers.
- Multi-host runs: a later deployment of the same harness, labelled as such.
- KERI-002/003 accelerator scenarios (Merkle and batch): census rows exist and read zero until those items exist.
