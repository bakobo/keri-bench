# Reading a run report

Each page under Results that is titled "keri-bench report" is the harness's own record of one run: one scenario, at one offered load, one repetition. The write-ups are where the runs are interpreted. A run report is the evidence underneath, printed exactly as the harness wrote it, so that anyone can check a write-up's numbers against it or regenerate it from the run's raw timeline. The note at the top of each report names the write-up it belongs to.

The terms below are defined in the [glossary](glossary.md).

## Units, first

Every duration is in nanoseconds, because that is the harness's clock. A billion nanoseconds is one second, so:

| as printed | means |
|---|---|
| 1000 ns | one microsecond |
| 1000000 ns | one millisecond |
| 984612863 ns | just under one second |
| 3042967551 ns | about three seconds |

Large numbers that are not durations, such as `565379855592545 ns` for a moment in time, are readings of the machine's clock since it started. Only the difference between two of them means anything.

Latencies are given as percentiles. `p50` is the median: half the credentials took less, half took more. `p99` is the time 99 in 100 credentials beat. The tail, `p99.9` and `max`, is where a stall shows.

## The sections, in order

### Header

The harness and report versions, the scenario's hash (its file is named in the write-up and kept under `scenarios/`), the offered load in credentials a second, which repetition this was, and how many credentials the measured window covered.

### Driver check

Whether the harness itself kept to its schedule. The harness sends requests on a fixed timetable whatever the system does, so that a slow system cannot slow the arrivals and hide its own queueing. If the harness fell behind its own timetable, the run is refused rather than reported, and this section says why.

### The seven pairs

Utah's KERI-006 asks that seven pairs of things be measured apart, such as building a credential versus updating its registry, or delivering a credential versus the holder storing it. Each pair gets the intervals that separate it, or a sentence saying why this stack could not observe them. "Not measurable in this run" is a named gap, not a failure.

### Measured interval

The one interval the run was judged on, from the moment a credential was requested to the milestone in brackets, such as `requested->witnessed`. When some credentials never got there before the window closed, their latency is counted at the window's end and marked "(lower bound)", because dropping them would hide exactly the backlog an overloaded system builds.

### All intervals

Every milestone-to-milestone interval the run observed, in the same table form. `n` is how many credentials it was measured for, `missing` how many started it and never finished, and `resolution` the finest step the observation could see.

### Throughput

How many credentials reached each milestone, and how many a second, over the measured window. When a later milestone's rate falls below the offered load, the system is not keeping up. `unconvertible` counts events stamped on a clock the harness could not relate to its own, which are left out rather than placed by guesswork.

### Observability matrix

For each milestone, which adapter saw it, how (observed by the harness, reported by the stack, or polled), on which clock, at what resolution, and how many times. `unobserved` means this stack exposes no way to see that moment.

### Faults

Present when the scenario injected one, such as a crash. It gives when the fault started and ended, how long the crashed part took to be ready again, how long until the next credential succeeded, and the intervals before, during and after.

### Soak drift

Present on long runs. It splits the run into buckets of credentials and fits how the median and tail latencies grew as the store filled, against the number of credentials, elapsed time and bytes on disk.

### Reconciliation

What became of every credential the run asked for, checked afterwards against an independent verifier and the witnesses: `done`; `resumed` (done, though a fault interrupted it); `lost` (never usable); `unanswered` when the verifier never replied, so its fate is unknown; and two flags for things that should never happen, anchored twice or seen under two identifiers.

### Measurement cost

How long the harness's own fault proxy added to each link it sat on, and how much processor time the harness used, so that its overhead is visible rather than assumed small.

### Calibration (error budget)

The harness's own accuracy, measured shortly before the run against a synthetic stack whose delays are known in advance. The worst error per transition bounds how far any number in the report can be trusted.

### Signer census

Present when the issuer signed through an external signer, standing in for a hardware security module: how many operations and signatures it made, how many per credential, and whether the signer kept to the latency and rate it was configured with. This is what sizes an HSM. Otherwise it says the census was not measured.

### Warnings

Anything the harness wants a reader to know before trusting the report, such as a calibration made by a different version of the harness.
