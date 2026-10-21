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
          children:
            The calibration stack is real processes behind the proxy, with injected delays logged as ground truth = decision:
              nid: 6he49n0k
              why: >-
                Part B #1 must show the harness measures correctly, so the synthetic stack is four small HTTP processes (issuer, state distribution, holder, verifier) started through process control, reached only through the fault proxy, and driven by synthetic adapters that use every observation path a real adapter uses: observed at an API boundary and at the proxy, polled, and reported on a stack clock deliberately skewed from wall time so the offset measurement is exercised. Each process draws every injected delay from a seeded log-normal, logs it to a truth file, and serializes construction behind a fixed service rate, so the throughput cap is known. The calibration compares the harness's report against the truth files. Rejected fake adapters that emit their injected delays directly into the timeline, because that would test arithmetic on numbers the harness never measured. Rejected a sleep-only in-process fake, because it would skip process control, the proxy and the clock offsets, which are where measurement error comes from. Tradeoff: the calibration takes tens of seconds in CI, and its tolerances must absorb loopback round trips (about 0.2 ms through the proxy), so injected delays are tens of milliseconds.
            'The driver holds a core: it yields to the event loop continuously instead of sleeping between sends = decision':
              nid: 0pqdn6ne
              why: >-
                Measured on kelun, 2026-10-15, at 20 sends per second against the calibration stack: sleeping until each send gave a p99 send lag of 5 to 22 ms, because a thread that blocks in epoll is woken late by the OS on a shared box (and asyncio rounds epoll timeouts to whole milliseconds); sleeping until 10 ms before each send and then yielding gave 2 to 5 ms, still refused now and then; yielding throughout gave 0.2 to 0.35 ms, even under nice 19. A run whose driver lags is refused (808tr3fn), so an imprecise driver throws away runs the stack did nothing to deserve. Rejected a driver thread with its own loop: it would timestamp sends precisely while the requests themselves still left late from the busy main loop, hiding exactly the lag the self-check exists to catch. Tradeoff: the harness uses one core for the whole window, which the report states as harness CPU, and on a small host that core is taken from the stack under test.
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
          children:
            ? Drift is an ordinary least-squares slope over consecutive buckets of credentials, against store count, elapsed time and bytes on disk, with a Student t interval = decision
            : nid: 7f9jfc99
              why: >-
                The test plan defines drift as the slope of p50 and p99 per 10^k credentials with a 95% confidence interval, and pairs soak with soak-preseeded so that size-driven decay can be told from time-driven decay. So a soak run's credentials, in the order they were requested, are cut into consecutive buckets of a declared size; each bucket gives a p50 and a p99 of the run's measure interval through the one named percentile definition; and each is regressed on three positions of the bucket's midpoint: credentials the store held (the preseed count plus those requested before it), seconds since the window opened, and bytes the issuer's store occupied, sampled during the run. The slope's 95% interval is the t interval with n-2 degrees of freedom, so it is honest at the few dozen buckets a soak on one box yields, where a normal interval would be too narrow. Every bucket's numbers go into the report, so a slope can be recomputed or refitted. Rejected a bootstrap interval: it needs a seed to be re-derivable and says nothing a t interval does not when the residuals are well-behaved, and the bucket table lets anyone check that. Rejected regressing raw per-credential latencies: one stalled credential would dominate, and a percentile per bucket is what the plan asks about. Tradeoff: bucket size trades resolution for noise, so it is a declared part of the scenario.
            ? A preseeded store is filled by issuing through the same stack before the window opens, and a soak stops on completions or time = decision
            : nid: 9yx6cg92
              why: >-
                soak-preseeded starts from a store pre-filled to size S (docs/test-plan.md, Soak). The store is filled by the issuer adapter issuing S credentials, issue stage only, with a declared number in flight, before the measured window, so the preseeded store holds exactly what this stack writes for a credential: its registry, its TEL and KEL events, its operation records, its witnesses' receipts, and the Registrar's and Observer's copies. Preseeded credentials carry no requested event, so no interval or throughput counts them, and the preseed's count, failures and duration are recorded as harness events. Rejected copying a prepared store into place: every role's store would have to agree with every other's, and a store built by anything but the stack is a claim about the stack nobody checked. A soak's [stop] ends the open-loop schedule at whichever comes first of a declared number of credentials that reached the measure's end milestone or a declared duration, and the timeline records which stopped it. Counting completions rather than requests makes the credential stop mean the store grew by that much. Tradeoff: preseeding costs S divided by the stack's saturated rate in wall time before every preseeded run.
        The October 2026 baseline's sizes on kelun, chosen from probes of today's imbu = decision:
          nid: 82r8lxy5
          why: >-
            The brief for bench-baseline leaves N, K, the load grid, the soak's stop and duration and the preseed size to this run, sized to what kelun (8 cores, 23 GB, shared with other workers) can sustain. Probes on 2026-10-20 at harness 7c3865d: one home issuing only, requested to published, kept up at 1 per second offered, reached 1.23 anchored per second at 2 offered, and collapsed to 0.56 at 5 offered as operations hit their 60 s timeout; three homes kept up at 4 per second with p50 0.75 s; five homes collapsed at 5 per second (accepted 4.8, anchored 2.8), so past three homes the shared witnesses, Registrar and box CPU, not the homes, set the ceiling. Hence N = 3, the most homes this box serves before the shared infrastructure saturates first, and K = 4 clients per home against K = 1. issue-steady issues only and measures requested to published, the issuer side end to end through witnessing and state distribution, so the holder and verifier, one process each, cannot become the bottleneck that N homes is meant to find. The grid steps to saturation: 0.25, 0.5, 0.75, 1, 1.25, 1.5 and 2 per second at one home, 60 s each; 1 to 6 per second at three homes, 45 s each, since more samples arrive at higher rates; 10 s warmup and 120 s drain everywhere, three repetitions per step. Restart runs the four-stage lifecycle to verified at about half of saturation (0.6 per second at one home, 2 at three), crashing at 20 s of a 60 s window: the issuer (every home at one home, home h0 alone at three, to show one home's crash does not stop the others), one witness of three at toad 2, the whole witness pool, state distribution, the holder and the verifier. Soak issues only at about 60% of saturation (0.7 per second at one home, 2.4 at three) for 30 minutes or 1200 and 4000 credentials, buckets of 50, the store sampled every 30 s; soak-preseeded starts from S equal to those stop counts, preseeded 2 and 6 in flight, so the preseeded run starts where the plain soak ends and the two can be compared at equal store size and at equal elapsed time. The Registrar window is 2 s, not the 900 s default, so published measures the stack rather than the window (6f6he77q). The driver's lag limit is 25 ms for these scenarios, not the plan's 5 ms: the probes' refusals were p99 send lags of 6 to 12 ms at three homes, a nice-19 driver preempted by other workers' normal-priority processes on a shared box, while the shortest interval measured here is about 700 ms, so a 25 ms lag is under 4% of it and cannot set the pace; latency still runs from the scheduled send, so lag only ever adds to it. Rejected running the driver at a higher priority than the stack: the box rule is nice 19 for everything this worker starts. Tradeoff: these are numbers for a shared host, and the report says so.
          children:
            ? "Corrections after the baseline's first pass: reconcile a few at a time, crash at a rate the lifecycle sustains, and run more repetitions where the host refuses them = decision"
            : nid: 2hn3mn3j
              why: >-
                The first pass of the matrix (2026-10-20, harness fc670cc) was stopped and rerun whole on one corrected harness, because three of its results were the harness's, not imbu's. First, reconciliation asked 32 status questions at once; on this stack each makes the one holder present again and the one verifier admit and verify, so they timed out and up to 30 of 36 credentials that had reached verified were counted lost. It now asks 4 at a time, asks a failed question twice more a second apart, calls a credential the verifier never answered unanswered rather than lost, and asks nothing in a run that never verified, where no credential has a fate to reconcile. Second, at three homes the crash scenarios offered 2 per second through the whole lifecycle, which overloads the one holder and the one verifier the homes share: p50 to verified was 16 s or more before every crash and about 80 s after, so the runs measured a queue rather than recovery. Every crash scenario now offers 0.6 per second at both topologies, where one home's lifecycle ran at p50 2.6 to 4 s, so the shared holder and verifier carry the same load at one home and at three and a restart is measured against normal load. Third, the driver check refused 15 of 18 three-home throughput runs on send lag of 27 to 190 ms while other workers' tests held cores at the same nice level; a refused run says nothing about the stack, so a sweep now keeps running repetitions until three are accepted or six have been made, and every refused run stays in the record. Rejected raising the lag limit again: at 100 ms or more the driver could set the pace of a 700 ms interval, which is exactly what the check exists to catch. Tradeoff: on a busy host a step can still end with fewer than three accepted runs, and the report says how many it has.
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
          children:
            An adapter may report another role's milestone it can see, under the owning role = decision:
              nid: 0ys878gy
              why: >-
                imbu's async operation resource reports witnessed and published, which belong to the witness-pool and state-distribution roles, and the issuer adapter is the only one that can read it. So a declaration may name any milestone, but every event is filed under the milestone's owning role and names the adapter that reported it, and the report's observability matrix says which adapter observed each milestone and how. Rejected forcing each role's adapter to observe its own milestones directly: on imbu the witness pool would have to poll witnesses for receipts and correlate them to credentials, a second and coarser measurement of a time imbu already records where it happens. Rejected filing the event under the reporting adapter's role: a timeline whose 'witnessed' sometimes belongs to the issuer cannot be compared across stacks. Tradeoff: two adapters may both declare one milestone; the run refuses that, so each milestone has exactly one source per run.
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
        ? On the imbu stack, N homes are N imbu issuers sharing one deployment's witnesses, Registrar, Observer, holder and verifier, and K clients are K controllers per home = decision
        : nid: 2lkqz9mq
          why: >-
            KERI-006 names concurrency, and the test plan measures it as K concurrent clients per home and N homes (docs/test-plan.md, KERI-006's named areas). An imbu home is one issuing party: its own lockbox, issuer AID, operation store and control plane, in its own process, so N homes are N such processes, and credentials are dealt to them round-robin. They share the witness pool, the Registrar, the Observer, the holder and the verifier, because that is how SEDI would scale issuance: more issuers against one state-distribution service, which is what N homes is meant to load. A client is a controller: a distinct key imbu authorises, signing its own requests over its own connection pool, and each home's credentials are dealt to its K clients round-robin, so K varies how many independent callers one home serves while the open-loop schedule fixes the total load. Both are knobs of the imbu issuer adapter, homes and clients, so they are part of a run's identity (6f6he77q) and a scenario must set them (4q4540c4). Rejected one issuer adapter per home in the scenario: the harness takes one adapter per role, and a role that is really N processes is the adapter's business, as the witness pool already is. Rejected separate witness pools per home: it would measure N copies of one home, not a shared deployment under N times the load. Tradeoff: per-home numbers come from the timeline's home attribute rather than separate reports.
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
      children:
        The fault proxy is an HTTP/1.1-aware reverse proxy per endpoint that never rewrites bytes = decision:
          nid: 6wlc25pz
          why: >-
            Every link between roles on the stacks under test is HTTP/1.1 (witnesses, the Registrar, the Observer, imbu's control plane), and the duplicate fault must replay one whole message, which a byte-level TCP proxy cannot delimit. So the harness puts one asyncio reverse proxy in front of every endpoint an adapter announces, reads each message whole within size limits, and applies delay, partition (requests are swallowed and their clients get no answer until the partition heals) and duplicate (an identical copy on a fresh upstream connection, its answer discarded). It rewrites nothing, not even Host, because clients sign @authority under RFC 9421 and the upstream checks it against the Host it receives. Rejected a TCP proxy with byte-offset faults, because it cannot duplicate a message. Rejected an off-the-shelf proxy (toxiproxy), because it adds a binary Utah must reproduce and still works at the byte level. Tradeoff: buffering adds about 0.2 ms at p50 and under 1 ms at p99 per hop on one host, which every report states per link; upgrades, 100-continue and HTTP/2 are not carried.
          children:
            The proxy ends a client's connection when the upstream ends it, not when a header says it will = decision:
              nid: 2q4mw7cs
              why: >-
                Found running real keripy witnesses behind the proxy, 2026-10-15: a witness answers Connection: close and keeps the socket open, and keripy's client sends its next request, the other witnesses' receipts, down that same socket. A proxy that honoured the header closed the connection, the receipts never arrived, every witness held the issuer's inception in escrow, and the issuer's OOBI answered 404, so nothing downstream could start. The proxy now watches the upstream between requests and closes the client's side only when the upstream closes or speaks unasked, which is what a client talking to the server directly would see. Rejected reconnecting upstream and retrying the next request, because that would hide a real close from the client and change the failure behaviour under test. Tradeoff: one extra pending read per idle connection.
        A crashed real-stack process restarts from what it had on disk, and each witness is a process of its own = decision:
          nid: 5ed29j5w
          why: >-
            Restart recovery is one of KERI-006's named areas, and the test plan reports time to ready and time to first new success after a crash on each role. A restart that rebuilt a role from nothing would measure re-provisioning instead: a fresh witness has a new AID nobody's KEL names, a fresh holder has a new AID nobody issued to. So every process the imbu stack starts keeps its state where a real deployment would, under the run directory, and reopens it when restarted: imbu reopens its home, each witness reopens its keystore with the same salt, and the holder and verifier reopen their lockboxes. The holder keeps which credential is which on disk too, and after a restart presents a held credential from heti's retained copy and the Observer's current chain, since the bench wrapper's in-memory index is not heti's and losing it would charge the wrapper's amnesia to heti. Each witness runs in its own process, named witness_pool.w<i>, and a crash fault may name one process of a role, so a run can kill one witness of three at toad 2, which is the redundancy a witness pool exists for, as well as the whole pool. Rejected crashing only whole roles: killing all three witnesses at once tests only the case the pool is designed to avoid. Tradeoff: the witnesses' keystores persist for the run, so a run directory is larger, and a witness process per witness costs memory per witness.
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
      children:
        ? The primitive microbenchmark times keripy's own Ed25519 and Blake3-256 on the host, one core, in its own process = decision
        : nid: 9n80j7ph
          why: >-
            The test plan's HSM section asks for a microbenchmark of Ed25519 sign and verify and Blake3-256 on the host, so a reader can set the end-to-end curves against what one signature costs here. It times keripy's Signer.sign, Verfer.verify and Diger with Blake3_256, the calls the stack under test makes, over fixed payload sizes, in a process of its own under the same nice level as the runs, and reports per-operation p50 and p99 and operations per second, with the host fingerprint. Rejected timing libsodium or the blake3 package directly: the cost the stack pays includes keripy's CESR wrapping, and that is what an HSM would replace. Tradeoff: one core's numbers, not a multi-core aggregate; the doc says so.
        ? hsm-sweep signs imbu's principal through heti's ThrottledSigner in imbu's own process, instrumented, and the census and the profile check come from that signer = decision
        : nid: 2p26rn9h
          why: >-
            imbu ccc8f6c can mint its principal with an external signer and attaches one on every start (imbu @42n54pc7); heti f568f7e ships SoftwareSigner, deterministic from a salt and a key index, and ThrottledSigner, which makes any signer behave like an HSM with a latency per operation and a cap on operation starts per second (heti @5dmsz9h5). So the imbu issuer adapter gains a signer knob: lockbox keeps the principal's keys in the lockbox as before, and external mints and attaches a ThrottledSigner over a SoftwareSigner whose salt is fixed for the run, so a restart attaches the same keys. Its HsmProfile is a subclass that records, for every operation, when it claimed its start and when its latency ended, on the host's monotonic clock, which is the harness's clock domain (98rxw488); the issuer process serves those records on a loopback port, and the adapter reads them once the drain ends into provenance, from which the report derives the census and the profile check. The census counts the signer's operations by kind and sets them against what the timeline implies: imbu signs every answer to an authenticated request (imbu src/imbu/control/signing.py), so every operation poll costs a signature, and the adapter counts the requests it made; what remains, per credential issued, is the KEL and exchange signing, which the stack test pins. The profile check (test plan, HSM sensitivity) refuses a run unless the median measured latency is within 5% of the configured one (or under 1 ms when none is configured), and, when the cap was ever the reason an operation waited, the median spacing of those starts is within 5% of 1/rate; a cap that never bound is reported as not exercised, not as a pass. Rejected a signer in its own process behind HTTP: the profile's latency already stands for the trip to a device, and a second hop would add a cost no profile names. Rejected counting signatures by patching keripy (Daniel's Q5, 2026-10-15). Tradeoff: the signer shares imbu's event loop, so a profile's latency is honoured only as well as that loop is free, which is exactly what the 5% check is there to catch.
          children:
            ? 'The hsm-sweep grid: no added latency, then 15 to 120 ms per signature, and at 30 ms caps of 10 and 5 starts a second, each stepped to saturation at one home = decision'
            : nid: 02fe3uk5
              why: >-
                The brief asks for a grid that spans the range a cloud KMS such as AWS KMS would plausibly fall in, without calibrating against one (Daniel's Q-SNJA). A signature from a cloud KMS in the same region is a network round trip plus the device's time, so tens of milliseconds, with a tail past 100 ms; so the latency points are 15, 30, 60 and 120 ms, uncapped, beside the external signer with no added latency, which is the same code path as an on-box HSM's best case and the comparator for the rest. Points below 15 ms were measured and cannot be honoured on this host: imbu's own event loop wakes the profile's timer 0.2 to 0.6 ms late, which is more than 5% of 5 ms, so the profile check would refuse every run; the no-latency point stands for that end of the range. A cap is a device's limit on operation starts, and a KMS's request quotas are per account rather than per key, so caps are swept at the one latency, 30 ms, at 10 and 5 starts a second, which bind at this stack's rates: on imbu ccc8f6c a credential costs 2 signatures plus one per answer to an authenticated request, so with the adapter's 50 ms poll about 7 signatures per credential, and a cap of 10 holds one home near 1.4 credentials a second. Each profile steps offered load at 0.25, 0.5, 0.75, 1 and 1.5 per second, 60 s each, 10 s warmup, 120 s drain, up to six attempts for three accepted repetitions, issuance only, measured requested to witnessed as the test plan names. One more curve runs three homes at 30 ms uncapped, at 1 to 4 per second, since each home's principal has its own signer. The poll interval stays at 50 ms, the interval of every earlier run, and the census reports what the polls cost, because a client's poll rate is a deployment choice the report should expose rather than tune away. Rejected a full latency-by-cap cross product: about 30 profiles, more than a day of this host, for curves the one-dimensional sweeps already bound. Tradeoff: no point between the no-latency signer and 15 ms.
