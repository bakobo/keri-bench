# keri-bench — Intent Tree (this.i)
#
# Source of truth for keri-bench's intentions and the decisions that follow from them
# (~/code/bakobo/dev/methodology.md). Each consequential decision is recorded here, in its own commit,
# before the code that implements it. Ids are minted by `i id` and never typed.
#
# Origin: the KERI-006 speculative interview, held 2026-10-15, recorded at
# bakobo/utah-keri-adoption design/bench-design/interview.md (branch bench-design).

Measure end-to-end KERI+ACDC issuance on any implementation, well enough to buy hardware on = goal:
  nid: 4y4rrghd
  why: >
    The State of Utah's KERI-006 asks for "Load and soak tests covering throughput, persistence,
    concurrency, restart recovery, witnessing, TEL replay, and wallet delivery", and its test-plan
    note says the numbers feed an HSM or accelerator purchase and must "measure end-to-end issuance,
    not simply the time required to generate an ACDC or compute a Merkle root". It names seven pairs
    the plan must keep apart. Chose a new stack-agnostic repo over load tests inside imbu, because
    Utah must be able to rerun the same instrument against KERIA or any other implementation
    (R-VQRD, 2026-10-14), and numbers produced by the vendor's own test suite are weaker evidence.
    Tradeoff: an adapter layer and a fault proxy that imbu-internal tests would not need.
  children:
    It is a measurement instrument, not a pass/fail suite = goal:
      nid: 9jhurd2v
      why: >
        Its job is numbers a purchase decision and an independent rerun can both trust; pass/fail
        is a thin later layer (plan item bench-targets) applied when Utah supplies targets. Rejected
        a suite with thresholds now, because Utah has given none and a pass against an invented
        target is worse than a number. Tradeoff: nothing turns red on its own until targets exist.
        Daniel confirmed, 2026-10-15 (Q1).
      children:
        Report distributions under open-loop load, with the driver checking itself = decision:
          nid: 808tr3fn
          why: >
            Percentiles from a histogram, with offered and achieved load, never a mean alone. Load
            arrives on a schedule independent of completions, because a closed loop hides queueing
            delay (coordinated omission), which is exactly what a saturated issuance lock produces.
            A run whose driver was the bottleneck is refused rather than reported. Tradeoff: a more
            complex driver than a simple request loop.
        Every number is re-derivable from a raw timeline and carries full provenance = decision:
          nid: 8q4myzru
          why: >
            Each run writes a versioned JSONL timeline of events (one line per event, naming every
            credential it covers, so one anchor or batch for many credentials is one line) and a
            report derived from it and the run's provenance, plus component commits, versions, policy knobs, host fingerprint and the
            scenario file. A disputed number can then be recomputed rather than argued. Rejected
            reporting only aggregates, because an aggregate cannot be audited. Tradeoff: large run
            directories.
        Soak reports drift against accumulated state, not only elapsed time = decision:
          nid: 4xtyfdd0
          why: >
            Persistence costs grow with the store (LMDB size, registry count; the SEDI pool target is
            about 12M registries), so degradation is plotted against credentials issued so far and
            database size. Rejected a fixed-duration soak with one before/after number, because it
            cannot tell time-driven from size-driven decay.
    The harness owns the lifecycle vocabulary; adapters report against it, per role = goal:
      nid: 7wt73dlu
      why: >
        The seven pairs define fixed milestones (requested, constructed, registry updated, anchored,
        witnessed, published, delivered, stored, proof generated, verified, revocation requested /
        anchored / witnessed / published / recognised, verifier synced full and incremental). A stack
        does not get to redefine "done". Rejected per-stack metrics, because KERIA's "operation done"
        and imbu's synchronous return mean different things, and Utah could not compare them. Adapters
        are per role (issuer, witness pool, state distribution, holder, verifier) so a run can mix a
        KERIA issuer with a heti verifier. Tradeoff: an adapter must map its stack's states onto ours,
        and a milestone it cannot observe is reported as not measured, never inferred. Daniel
        confirmed, 2026-10-15 (Q2).
      children:
        Every number is labelled with how it was observed, and on which clock = decision:
          nid: 7vsky4sk
          why: >
            Three methods: "observed" is the harness's monotonic clock at a boundary it controls;
            "polled" is the harness clock when a poll first saw a state, with the poll interval as
            its resolution; "reported" is a timestamp the stack wrote, usable only with its clock
            domain and an offset measured during the run. imbu's async operation resource (imbu tick
            ~73tw, Q-EVAG) yields "reported" timestamps, not harness-observed ones. An interval across
            clock domains with no measured offset is not measured. Rejected a two-way outside/inside
            label (the first draft), because it let stack-written timestamps pass as harness-measured
            (maintainability review MNT-3, testability review TST-2, 2026-10-15).
          children:
            The harness clock is the host's CLOCK_MONOTONIC, named by boot id; wall clocks are offset-measured = decision:
              nid: 98rxw488
              why: >-
                Harness events use time.monotonic_ns(), whose clock domain is named monotonic@<boot_id>. On Linux CLOCK_MONOTONIC is one clock for every process on the host since boot, so a stack process on the same box that stamps with it (the summit harness's Registrar publisher does) shares the harness's domain exactly, with zero offset and nothing to measure. A stack that stamps wall time is in domain realtime@<host>; the harness measures that domain's offset by bracketing a realtime read between two monotonic reads at the start and the end of every run, and the bound carried into each interval is the wider bracket plus the change in offset between the two samples, so an NTP step during the run widens the bound instead of hiding in it. A stack on another host needs its own offset source (a time route sampled the same way) and is not measured without one. Rejected wall time as the harness clock, because it steps and slews under NTP and an interval across a step is wrong by the step. Rejected assuming same-host wall clocks agree with zero bound, because that is exactly the unmeasured offset 7vsky4sk forbids.
        Independent verification means a verifier that is not the issuer's code path = decision:
          nid: 27mjdj7q
          why: >
            Utah's pair 3 separates generating a proof from independently verifying it. The verifier
            role is configured separately and the report names it. For imbu that is a separate heti
            process, and ideally also a keripy-based one, since heti is imbu's own substrate.
        Adapters are in-process Python classes per role, declaring before they emit = decision:
          nid: 2vu4969q
          why: >-
            Each role adapter is a Python class with a static declaration (role, the milestones it emits with each one's method, clock and resolution, a versioned knob schema, the stack versions it was contract-tested against), an async start that receives the harness's run context and configured peer endpoints and returns the endpoints it serves, an async stop, and the role's operations (issue, revoke, present, verify, status, sync) as async methods. Every event goes through the context's emit, which refuses a milestone, method or clock the declaration does not name, so the contract suite can check an adapter by running it rather than by reading it. Rejected out-of-process adapters behind a wire protocol: they would let a KERIA team write theirs in TypeScript, but they put one more process and one more clock boundary inside every measured interval and add a protocol to version, while every stack under test is reached over HTTP anyway, so a Python adapter can drive any of them. Rejected post-hoc adapters that scrape a stack's logs into a timeline after the run: an undeclared or misclocked event would be found only after the run, and the open-loop driver needs a live call to schedule. Tradeoff: adapter authors write Python, and an adapter's own overhead sits inside observed intervals; the proxy's pass-through cost is reported, the adapter's is bounded by the calibration stack using the same code paths.
    It measures a configured deployment, not the software in the abstract = goal:
      nid: 6f6he77q
      why: >
        Registrar window (default 900 s), witness count and toad, Observer gap grace and resync,
        verifier freshness bound and the number of homes are deployment policy, and they dominate
        some results: with defaults, revocation-to-recognition mostly measures the Registrar window.
        So every knob is part of a run's identity and scenarios sweep them; the harness never picks a
        "right" value. Rejected one headline number per pair, because it would silently report the
        defaults. Tradeoff: more runs. Daniel confirmed, 2026-10-15 (Q3).
      children:
        A scenario must set every policy knob explicitly = decision:
          nid: 4q4540c4
          why: >
            An unset knob is an error, not a default, so a report can never rest on a value nobody
            chose. Tradeoff: verbose scenario files.
    One box by default, with real faults at the application boundary, and honest about the rest = constraint:
      nid: 0wcr2rrj
      why: >
        The harness owns every inter-role link through a proxy it controls (delay, drop/partition,
        duplicate) and owns process lifecycle (SIGKILL and restart), so faults are reproducible and
        timestamped in the timeline. That is faithful to KERI's HTTP/CESR failure behaviour, but it
        is not a kernel-level partition and not many hosts; the report says which kind of run it was.
        Rejected container networking with tc/netem as the base, because it adds a dependency Utah
        must reproduce and cannot duplicate a message. Multi-host is a later deployment of the same
        design. Consequence: adapters must accept configured endpoints. Daniel agreed, 2026-10-15 (Q4).
    The HSM question is answered by sensitivity, through a real signer seam = decision:
      nid: 4jlx9k8q
      why: >
        An HSM rarely makes one signature faster; it moves signing off the box, adds latency per
        signature and caps the rate. So the instrument reports a signature census per role per
        lifecycle, a primitive microbenchmark, and end-to-end curves while a latency- and rate-capped
        signer is swept across HSM profiles. Daniel chose (2026-10-15, Q5) to build that signer as an
        implementation of a real external-signer interface in heti (plan item heti-signer, heti tick
        ~2ppq) rather than a test-only monkeypatch of keripy's Manager.sign, so the same seam later
        takes a real HSM. On KERIA the seam is Signify's ExternalModule. Tradeoff: bench-baseline
        waits on heti-signer.
