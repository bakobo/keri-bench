# Glossary

The words these pages use without stopping to explain them. KERI's own terms follow the [KERI specification](https://trustoverip.github.io/kswg-keri-specification/) and the [ACDC specification](https://trustoverip.github.io/kswg-acdc-specification/); the definitions here are short and practical, not normative.

## What is being measured

| term | meaning |
|---|---|
| KERI-006 | The State of Utah's request for "load and soak tests covering throughput, persistence, concurrency, restart recovery, witnessing, TEL replay, and wallet delivery", whose numbers feed a decision about hardware. keri-bench is the instrument built to answer it. |
| SEDI | State-Endorsed Digital Identity, Utah's program of digital credentials issued by the State. Utah expects up to a million SEDI credentials over a decade. |
| Issuance, end to end | Everything from a request for a credential until an independent verifier accepts it: building it, recording it in its registry, getting that record witnessed and published, delivering it to the holder, and the holder presenting it. |
| Utah's targets | The numbers Utah gave on 2026-10-25: at most 10,000 credentials a day, a million in stock, revocations reaching verifiers within 30 minutes, no crash-recovery target yet, and a network HSM doing thousands of signatures a second. |

## KERI and ACDC

| term | meaning |
|---|---|
| KERI | Key Event Receipt Infrastructure. A way to give an identifier its own verifiable history of key changes, so that anyone can check who controls it without trusting a central registry. |
| KEL | Key event log. The signed, hash-chained history of one identifier's keys. An issuer's KEL grows with every credential it issues here, because each issuance is anchored in it, and several findings turn on that growth. |
| ACDC | Authentic Chained Data Container, KERI's verifiable credential format. "Credential" on these pages means an ACDC. |
| TEL and registry | A transaction event log records whether credentials in a registry are issued or revoked, and is anchored in the issuer's KEL. A verifier checks a credential's current state there. |
| Witness | A server that an identifier's controller names to receipt its key events, so that others can trust what the KEL says. The runs here use three witnesses. |
| toad | The number of witness receipts an event needs before it counts. These runs use two of three, so one witness can be down without stopping issuance. |
| Rotation | A KEL event that replaces an identifier's keys with new ones. |
| Interaction event (ixn) | A KEL event that records something, such as a registry update, without changing keys. Each credential issued here adds one to the issuer's KEL. |
| Grant | The message that offers a credential to its holder. imbu hands it back to the caller, and the harness delivers it. |
| Ed25519, Blake3-256 | The signature and digest algorithms KERI uses by default. |
| SAID | Self-addressing identifier: a digest of a piece of content, embedded in that content. A credential is identified by its SAID, so "two SAIDs" for one credential would mean it was issued twice with different content. |
| HSM | Hardware security module: a device that holds signing keys and signs with them without ever releasing them. Utah expects to use a Thales network HSM. |

## The stack under test

| term | meaning |
|---|---|
| imbu | Bakobo's issuing service. Each principal (here, the issuing agency) runs one; it accepts requests for credentials over HTTP and carries them out as asynchronous operations. |
| Home | One imbu process with its own store and its own issuing identifier. "One home" and "three homes" are the two deployment sizes measured. Credentials are dealt to the homes in turn, so three homes share the load three ways and each grows its own KEL a third as fast. |
| Client | A controller key authorised to ask a home for credentials, with its own connections. Runs use one or four clients per home. |
| heti | Bakobo's library for taking part in KERI and ACDC as a holder or verifier. These runs use it for the holder (the citizen's wallet), the verifier, the Observer and the external signer. |
| keripy | The reference Python implementation of KERI, which the witnesses run and heti builds on. |
| Registrar | The service imbu publishes each registry update to, so that verifiers can learn a credential's current state. A credential counts as published when the Registrar has acknowledged it. |
| Observer | heti's component that follows what the Registrar publishes and holds each registry's current chain, so a verifier can check that a credential is still valid. |
| `Prefer: wait` | An HTTP request header asking imbu to answer only when an operation has finished, instead of answering at once and being polled. It changes how many signed responses, and so how many signatures, a credential costs. |

## How the harness measures

| term | meaning |
|---|---|
| Milestone | A named moment in a credential's life, such as `requested`, `witnessed`, `stored` or `verified`. The [test plan](test-plan.md) defines each one by the event that counts, and [Adapters](adapters.md) says how this stack shows it. |
| Interval | The time between two milestones, written `start->end`, such as `delivered->stored`. |
| The seven pairs | Seven pairs of things Utah asked to be measured apart, such as building a credential versus updating its registry. Each run report has a section for them. |
| Open loop | Requests are sent on a fixed timetable whatever the system does. A system that falls behind therefore builds a visible backlog, instead of quietly slowing the arrivals and hiding its queueing delay. |
| Offered load | How many credentials a second the harness asks for. Achieved throughput is how many a second actually reached a milestone. |
| Fault proxy | A proxy the harness puts on every link between the parts of the stack. It records the traffic and is where faults are injected, so a fault happens on the wire rather than inside the code under test. |
| TEL replay | Rebuilding a registry's state by reading its whole log, as a verifier does the first time it meets a registry; the reports call it a full sync. |
| Saturation | The offered load past which achieved throughput stops rising and latency climbs without limit. |
| p50, p99, p99.9 | Percentiles. p50 is the median; p99 is the time 99 credentials in 100 beat. The tail shows stalls that a median hides. |
| Soak | A long run that watches whether latency grows as the store fills. |
| Preseeded | A run that starts from a store already filled with credentials through the same stack, to measure behaviour at that stock. |
| Fault | A deliberate failure injected during a run, such as killing a process (a crash) or stopping a witness for a while (an outage). |
| Calibration | A run against a synthetic stack whose delays and capacity are known in advance, which measures how accurately the harness itself measures. |
| Refused run | A run the harness would not report, because its own timetable slipped. Refused runs are kept and listed, never silently dropped. |

## Reading the citations

| term | meaning |
|---|---|
| `this.i` and its ids | The repository's record of its design decisions and the reasons for them. An id such as `0hykugwr` names one decision; its text is in [`this.i`](https://github.com/bakobo/keri-bench/blob/main/this.i) and can be found there by searching for the id. |
| Commit hashes | Short hexadecimal strings such as `imbu 670a3d8` or `heti a830fcd` name the exact version of a component that was measured, so a run can be repeated on the same code. |
| Error codes | Codes such as `e.party.imbu.refused.f` are a component's own name for a failure. The last letter says whether retrying could help: `r` retryable, `f` final. |
| `@` ids | Ids such as `@7qgv63f5` are cross-references into other Bakobo repositories' design records. |
| Ticks, `~26me` | Ids in the repository's task ledger: a known gap or follow-up that is tracked rather than forgotten. |
| Work names, `imbu-prj`, `heti-dead-lane`, `bench-stock-2` | The names of the pieces of work that changed the code being measured, or that ran a measurement. |
