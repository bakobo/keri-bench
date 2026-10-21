# Baseline of today's imbu, October 2026

This is the baseline Utah asked for before any hardware security module (HSM) or accelerator purchase: today's imbu stack, measured end to end with keri-bench, so a purchase can be sized against it. It is a baseline, not a pass or fail. Utah has given no targets yet (plan item utah-answers), so nothing here is judged against one.

KERI-006 asks for "Load and soak tests covering throughput, persistence, concurrency, restart recovery, witnessing, TEL replay, and wallet delivery". This baseline covers throughput, concurrency, restart recovery, persistence (soak) and the host half of the HSM section, at one home and at three. What it does not cover is listed under [Not measured](#not-measured), each item with the reason.

## What was measured, and on what

| | |
|---|---|
| Stack | imbu `963dfec` (async operations), heti `b6104e4`, keripy `2.1.0.dev1` through heti, bakobo/witness Registrar `4522151`, heti Observer, three keripy witnesses at toad 2, one heti holder, one independent heti verifier |
| Topologies | 1 home and 3 homes (`this.i` 2lkqz9mq), each with 1 client and with 4 clients per home |
| Host | kelun: 8 cores (Intel Haswell, no TSX), 23 GB, Linux 7.0, Python 3.14.6. One host for every role, shared with other workers' test runs, everything at nice 19 |
| Harness | keri-bench `8d69e59` for every run. The soaks' reports were rebuilt from their timelines by `0780847`, after a fault in the size sampler was fixed (see [First pass](#what-the-first-pass-got-wrong)). The calibration passed at `8d69e59`: saturation within 0.4%, every injected interval within its budget (`results/baseline-2026-10/calibration/calibration.json`) |
| Dates | 2026-10-21, 01:14 to 07:12 UTC |
| Sizes | Chosen from probes and recorded before the runs, in `this.i` 82r8lxy5, with the corrections in 2hn3mn3j |

Every number below comes from a run directory under [`results/baseline-2026-10/`](../results/baseline-2026-10/), which holds each run's `timeline.jsonl`, `provenance.json`, `report.json` and `report.md`, and each sweep's `sweep.json`. `SHA256SUMS` covers every committed file. The soak timelines are over a megabyte, so they are committed gzipped; `gunzip` one before `keri-bench report` rebuilds its report, which reproduces the committed report exactly. The scenarios are in [`scenarios/baseline/`](../scenarios/baseline/), and `scripts/baseline.sh` reruns and collects all of it.

Latency is measured from `requested`, the scheduled send time, under open-loop load (`this.i` 808tr3fn). Percentiles are HDR upper bounds, and every reported step has three accepted repetitions. The driver's lag limit is 25 ms rather than the plan's 5 ms. The reason is in `this.i` 82r8lxy5: on a shared host a nice-19 driver is preempted for tens of milliseconds, and the shortest interval measured here is about 700 ms. No run in the final matrix was refused.

## What the numbers say

1. **One home issues about 1.5 credentials per second; three homes about 4.** Below saturation, p50 from request to the Registrar's acknowledgement is 0.62 to 0.69 s at every load. Past saturation, goodput collapses rather than flattens: one home offered 2 per second completes 0.86, and three homes offered 6 complete 2.45, because operations queue past imbu's 60 s timeout.
2. **Every issuance costs more than the last, and that, not signing, is the first wall.** Each credential gets a registry of its own, and imbu's publication of each registry snapshot carries the issuer's whole KEL. The KEL gains one event per credential, so the snapshot grows: 1.7 KB for the first credential and 1.0 MB by the 1200th. After 1200 credentials one home's publication outbox was 609 MB and the Registrar's store 582 MB. Witnessing slows the same way. p50 rises by 2.7 s per 1000 credentials at one home, and a store preseeded with 1200 credentials could no longer sustain 0.7 per second. At SEDI's target of about 12 million registries this growth would stop issuance long before any signer did.
3. **Signing time on this host is a negligible share of issuance today.** keripy's Ed25519 sign takes 37 µs at p50 here. The test plan's hand-traced census is three issuer signatures per issuance, about 0.11 ms of the 650 ms p50. This is an inference from a hand count, not a measured census (see Not measured). What an HSM changes is latency per signature and a rate cap, which `hsm-sweep` will measure once imbu signs through heti's external signer.
4. **Every role restarts from its own disk in 1 to 3.3 s, and the next credential completes within 4.4 s of that.** The exception is a defect: a restarted imbu stops sealing registry salts to a holder it knew before the crash, so revocation of what it issues next cannot be recognised from a fresh presentation until it resolves the holder again.

## Throughput

Issuance only, requested to published (the Registrar acknowledged the TEL snapshot), offered load stepped to saturation, three accepted repetitions per step. Throughput is `published` completions per second in the window. p50 is each repetition's, and p99 is the mean of the three, with credentials still incomplete at the window's end counted at their lower bound.

One home (`results/baseline-2026-10/issue-steady-1home/sweep.json`):

| offered /s | achieved /s (min–max) | p50 s (each run) | p99 s |
|---|---|---|---|
| 0.25 | 0.24 | 0.63, 0.62, 0.61 | 0.68 |
| 0.5 | 0.50 | 0.62, 0.66, 0.63 | 0.79 |
| 0.75 | 0.74–0.76 | 0.64, 0.65, 0.65 | 0.82 |
| 1.0 | 1.00 | 0.64, 0.64, 0.64 | 0.76 |
| 1.25 | 1.26 | 0.64, 0.63, 0.63 | 1.05 |
| 1.5 | 1.46–1.50 | 0.77, 0.89, 1.26 | 2.08 |
| 2.0 | 0.81–0.93 | 13.9, 11.0, 13.8 | 57.0 |

Three homes (`issue-steady-3homes/sweep.json`):

| offered /s | achieved /s (min–max) | p50 s (each run) | p99 s |
|---|---|---|---|
| 1 | 1.00 | 0.65, 0.64, 0.64 | 0.77 |
| 2 | 2.00 | 0.67, 0.66, 0.65 | 0.85 |
| 3 | 3.00 | 0.69, 0.68, 0.67 | 0.83 |
| 4 | 4.00–4.03 | 0.69, 0.68, 0.66 | 1.18 |
| 5 | 3.59–4.05 | 4.93, 4.52, 10.4 | 16.4 |
| 6 | 2.35–2.50 | 30.5, 27.7, 31.5 | 58.5 |

The plan's saturation detector (`docs/test-plan.md`, Statistics) flags 2 per second at one home and 5 at three, both because throughput stopped rising. The highest step each sustained is 1.5 and 4. Three homes give 2.7 times one home's capacity, not 3. A probe on 2026-10-20 at five homes collapsed at 5 per second, so on this box the shared witnesses, Registrar and CPU become the ceiling at about three homes. That is a statement about one 8-core host and says nothing about a deployment that spreads the roles across hosts.

The two halves of Utah's first pair, construction and registry update, cannot be told apart on imbu, because imbu stamps neither milestone (`docs/adapters.md`). Every throughput figure here is end-to-end issuance through publication, as Utah's note asks: "measure end-to-end issuance, not simply the time required to generate an ACDC".

## Concurrency

K = 4 clients per home, each a distinct controller key with its own connection pool (`this.i` 2lkqz9mq), against K = 1 at the same offered load:

| | sustained /s | p50 at that step | p99 at that step | at the next step |
|---|---|---|---|---|
| 1 home, 1 client | 1.5 | 0.77–1.26 s | 2.08 s | 0.86 /s achieved |
| 1 home, 4 clients | 1.5 | 0.65–0.66 s | 1.26 s | 0.95 /s achieved |
| 3 homes, 1 client | 4 | 0.66–0.69 s | 1.18 s | 3.86 /s achieved |
| 3 homes, 4 clients | 4 | 0.66–0.69 s | 1.25 s | 4.22 /s achieved |

Spreading one home's load across four callers does not lower its capacity. It eases the knee slightly at one home, but the home sets the capacity, not the number of clients calling it. Sweeps: `issue-steady-1home-4clients/` and `issue-steady-3homes-4clients/`.

## Restart recovery

Each scenario runs the lifecycle to `verified` (issue, deliver, present, verify) at 0.6 per second for 60 s and SIGKILLs one target at 20 s. The harness restarts it at once on its own disk (`this.i` 5ed29j5w). Time to ready runs from the first kill to the last of the target's processes announcing ready. Time to first success runs from ready to the next `verified`. The p50s are requested to verified, for credentials requested before the crash and after it. Reconciliation asks the independent verifier about every credential at the end and counts them as done, resumed (its lifecycle overlapped the fault), lost or unanswered, summed over the three repetitions. All 36 runs were accepted. Ranges are across the three repetitions.

| crashed | homes | ready s | first success s | p50 before s | p50 after s | p99 after s | done / resumed / lost / unanswered |
|---|---|---|---|---|---|---|---|
| imbu (every home) | 1 | 1.35–1.63 | 3.49–4.35 | 2.27–2.79 | 3.18–3.26 | 4.80–5.08 | 33 / 3 / 72 / 0 |
| imbu home h0 of 3 | 3 | 1.29–1.76 | 0.21–3.60 (h0's own: 5.38–6.03) | 1.96–2.42 | 2.37–2.56 | 3.66–4.12 | 80 / 4 / 24 / 0 |
| one witness of 3 (toad 2) | 1 | 1.04–1.10 | 0.30–0.44 | 2.39–2.61 | 3.31–3.57 | 4.63–5.24 | 102 / 6 / 0 / 0 |
| one witness of 3 (toad 2) | 3 | 1.01–1.12 | 0.47–1.64 | 1.95–2.17 | 2.61–2.78 | 3.78–4.01 | 102 / 6 / 0 / 0 |
| all three witnesses | 1 | 3.08–3.13 | 1.23–1.71 | 2.34–2.63 | 3.46–3.63 | 4.40–4.59 | 99 / 9 / 0 / 0 |
| all three witnesses | 3 | 3.11–3.26 | 0.96–1.22 | 2.00–2.40 | 2.54–2.56 | 4.10–4.18 | 99 / 9 / 0 / 0 |
| Registrar and Observer | 1 | 2.44–2.65 | 1.00–1.31 | 2.37–2.67 | 3.25–3.54 | 4.23–5.01 | 99 / 9 / 0 / 0 |
| Registrar and Observer | 3 | 2.60–3.01 | 0.96–3.31 | 2.12–2.44 | 2.45–2.76 | 4.07–4.10 | 99 / 9 / 0 / 0 |
| holder | 1 | 0.98–1.21 | 0.13–4.10 | 2.34–2.52 | 3.23–3.44 | 4.29–4.89 | 102 / 2 / 4 / 0 |
| holder | 3 | 0.97–1.00 | 0.42–2.78 | 2.22–2.54 | 2.59–2.63 | 3.94–4.51 | 102 / 3 / 3 / 0 |
| verifier | 1 | 1.00–1.12 | 1.23–1.35 | 2.48–2.59 | 3.46–3.58 | 4.48–4.91 | 102 / 6 / 0 / 0 |
| verifier | 3 | 1.02–1.11 | 1.02–1.37 | 2.36–2.41 | 2.30–2.56 | 3.73–4.35 | 102 / 6 / 0 / 0 |

What this shows:

- **The first success after a crash is the deployment's, not the crashed process's.** For one home of three, the report's first success came from the other homes, which never stopped, in 0.2 to 3.6 s. h0's own first credential after its restart completed 5.4 to 6.0 s after it was ready, read from the committed timelines by the `home` attribute on its events. The report does not yet make that distinction itself (tick `~76aw`).

- **One witness of three at toad 2 costs almost nothing.** No credential was lost, and the next one completed 0.3 to 1.6 s after the witness was back, which is what the pool is for. The whole pool took about 3.1 s to come back; nothing was lost, and the credentials in flight resumed.
- **Losing the holder loses the one or two grants in flight to it.** Delivery of those grants failed while it was down, and nothing redelivers them. The harness relays grants itself here, because imbu does not deliver them (`docs/adapters.md`), so this measures the relay as much as the holder.
- **A restarted imbu has a defect.** Every credential issued after the restart was accepted at `verified` during the run, but when the holder later presents it afresh, the presentation lacks the blinded registry's disclosures (heti refuses it `e.input.missing.blinder.f`). The verifier therefore cannot judge its current state, which is why reconciliation counts those credentials lost: 72 of 108 at one home, and 24, home h0's share, at three. Having imbu resolve the holder's OOBI again after the restart, with a new idempotency key, cures it. The issuee key state heti needs to seal a registry's salt to the holder therefore does not survive the restart, though the home does. The practical consequence is that a verifier could not recognise the revocation of a credential imbu issued between a restart and its next contact with that holder. Reported to the plan's PM for imbu-prj on 2026-10-21; reproduce it with `scenarios/baseline/fault-crash-issuer-1home.toml`.
- **p50 after a crash runs 0.4 to 1 s above p50 before it, for every target, including the verifier, whose crash touches no issuance.** Part of that is the backlog the crash left, and part is the growth described under Soak, since credentials after the crash are issued into a larger store. These runs do not separate the two.

## Soak (persistence)

Issuance only, requested to published, at about 60% of saturation. Each soak stops at its credential count or after 30 minutes, whichever comes first. The preseeded runs start from a store that the same stack filled with as many credentials as the plain run's stop count (`this.i` 9yx6cg92). Drift is the least-squares slope of each 50-credential bucket's p50 and p99 against the credentials in the store, with a 95% Student t interval (`this.i` 7f9jfc99). A bucket's store position counts the credentials requested before it, not those that completed.

| run | rate /s | stopped by | completed | p50 first → last bucket | p99 first → last bucket | p50 slope per 1000 credentials (95% CI) | p99 slope per 1000 (95% CI) |
|---|---|---|---|---|---|---|---|
| soak, 1 home | 0.7 | 1200 credentials, 1717 s | 1200 | 0.61 → 3.41 s | 0.75 → 7.32 s | 2.67 s (1.58–3.76), 24 buckets | 7.54 s (5.77–9.30) |
| soak, preseeded 1200, 1 home | 0.7 | 30 minutes | 152 of 1260 | 3.33 s at 1225 → 49.7 s | 8.6 s → 51.0 s | 290 s (−238 to 819), 4 buckets | 339 s (−174 to 852) |
| soak, 3 homes | 2.4 | 30 minutes | 2642 of 4320 | 0.63 → 38.1 s | 0.79 → 51.4 s | 4.90 s (2.78–7.03), 55 buckets | 11.1 s (7.44–14.8) |
| soak, preseeded 4000, 3 homes | 2.4 | 30 minutes | 44 of 4320 | 2.86 s at 4025 → 20.5 s | 29.9 s → 46.6 s | not measured: 2 buckets | not measured |

At three homes the store kept up until about 2300 credentials. Then anchoring itself began to queue, and p50 reached 38 s by the end. A rerun of that soak on 2026-10-21 (`soak-3homes-rerun-refused/`) collapsed at about 1175 credentials instead. It ran while another worker's test suite held all eight cores at the same priority, with a load average of 12, and the driver check refused it (send lag 38 ms). It is kept as a measure of how far contention on a shared host moves this number, not as a result.

The preseeded store filled at 0.86 credentials per second at one home (1200 in 1392 s, 2 in flight) and at 1.67 per second at three homes (4000 in 2388 s, 6 in flight), with no failures. Of the 1108 and 4276 failures in the preseeded windows, 905 and 3890 were operations that passed imbu's 60 s timeout. Nearly all the rest were imbu's control plane closing the connection while the harness polled an operation (203 and 377, a 502 through the proxy), and 9 were the harness's own HTTP timeout. All are what an overloaded issuer looks like from outside.

Comparing the pairs at equal elapsed time, the plain soak starts at 0.61 s and its preseeded twin cannot keep up at all. Comparing them at equal store size, the plain soak was still completing at about 1150 credentials, and the preseeded run, starting at 1200, was not. So the decay is driven by store size, not by elapsed time, which is the separation the test plan asks this pair to make. The preseeded store fares worse than the plain soak did at the same size. The likeliest reason is work the preseed left queued, such as unacknowledged publications that imbu retries each second, but these runs do not show it, so it is a hypothesis.

Where the time goes, at one home, by median of each 150 consecutive credentials (`soak-1home`'s timeline, imbu's own stamps):

| credentials into the run | accepted → anchored | anchored → witnessed | witnessed → published | requested → published |
|---|---|---|---|---|
| 0 | 0.24 s | 0.13 s | 0.25 s | 0.64 s |
| 300 | 0.26 s | 0.30 s | 0.27 s | 0.84 s |
| 600 | 0.27 s | 0.47 s | 0.30 s | 1.08 s |
| 900 | 0.28 s | 0.66 s | 0.83 s | 1.82 s |
| 1050 | 0.33 s | 0.79 s | 2.24 s | 4.74 s |

imbu's own work, from accepting a request to anchoring it, stays flat. Witnessing grows steadily with the issuer's KEL. Publication then grows faster: every row of imbu's publication outbox carries the issuer's whole KEL (`registry-publications.sqlite3`, the `kel` column, 1693 bytes in the first row and 1,003,175 in the last). Storage is therefore quadratic in credentials issued, and so is the Registrar's, which keeps what it is sent. One home's footprint reached 618 MB after 1200 credentials, and the Registrar's store 582 MB.

## HSM sensitivity

**The host microbenchmark** (`results/baseline-2026-10/microbench.json`, `this.i` 9n80j7ph) uses keripy's own primitives, one core, nice 19, 2000 operations per cell:

| payload | Ed25519 sign p50 / p99 | sign /s | Ed25519 verify p50 / p99 | verify /s | Blake3-256 p50 / p99 | digest /s |
|---|---|---|---|---|---|---|
| 32 B | 35.2 / 80.2 µs | 24,256 | 85.3 / 174.8 µs | 10,703 | 10.1 / 37.0 µs | 65,574 |
| 256 B | 37.2 / 95.3 µs | 21,172 | 86.3 / 139.5 µs | 10,946 | 10.5 / 28.3 µs | 73,896 |
| 1024 B | 39.8 / 87.6 µs | 21,664 | 88.7 / 147.2 µs | 10,522 | 11.9 / 31.0 µs | 67,411 |
| 4096 B | 61.8 / 116.2 µs | 14,320 | 99.9 / 162.7 µs | 9,516 | 13.8 / 41.2 µs | 57,060 |

The signature census and `hsm-sweep` are not measured; see below.

## Not measured

Each of these is named, not inferred:

- **`hsm-sweep` and the signer census.** imbu `963dfec` pins heti `b6104e4`, from before heti's external-signer interface (heti `utah/next` `6a898b8`), and never signs through it. So there was no seam to put a throttled signer in, and no signer to count the operations of. imbu-prj has since landed the seam on imbu `utah/next` (`ccc8f6c`, heti `f568f7e`), and both are plan item bench-baseline-2. The census row in every report reads "not measured".
- **`bulk-steady`** (tick `~42si`). imbu's only bulk route is the SEDI proofed ceremony: a proofing agent's signed IAR per ceremony, a citizen SMAID anchoring its nonce, Core and Residence pairs to distinct holder AIDs, a Utah-root agency credential that imbu checks through a second Registrar and Observer, and a provisioned registry pool. Porting that from the summit harness is a build of its own, and it moved to bench-baseline-2 by the PM's decision of 2026-10-20.
- **Construction separately from registry update (pair 1).** imbu stamps neither `constructed` nor `registry_updated`, so both are `unobserved` in every report's observability matrix. Throughput here is end-to-end issuance instead.
- **Incremental synchronization (pair 5's second half).** No adapter observes `sync_increment_applied`.
- **Revocation recognition (pair 6), proof generation against independent verification (pair 3), and delivery against storage (pair 4) under load.** No scenario in this matrix revokes. The restart scenarios measure verification only end to end, and the per-pair intervals are in each crash run's report. `scenarios/imbu-smoke.toml` exercises revocation at low rate, but that is not a baseline.
- **Faults other than crashes.** Partitions, delays and duplicate messages are supported by the harness but were not part of this baseline.
- **Verifier-side numbers are lower bounds where the adapter cannot see a milestone.** `published` is imbu's record of the Registrar's acknowledgement. The Registrar's delivery to the Observer goes to the Observer's own callback, past the fault proxy (tick `3zja`), so nothing observes when a verifier's source of truth first held a snapshot. `verified` is the first acceptance after retries every 250 ms, so it can be up to 250 ms late. Neither is interpolated.
- **The issuer's store size counts imbu's homes only.** The Registrar's and Observer's stores are not sampled, though the Registrar grew as fast.

## Limits of this baseline

- **One shared host.** Every role ran on one 8-core box at nice 19, beside other workers' test runs. Absolute numbers are this host's, and the three-home ceiling is partly this host's CPU. A multi-host deployment is a later use of the same harness (`this.i` 0wcr2rrj).
- **No Utah targets.** Nothing is pass or fail. When targets arrive, plan item bench-targets applies them.
- **A 2 s Registrar window, not the 900 s default.** `published` therefore measures the stack rather than the batching window (`this.i` 6f6he77q). With the default window it would mostly measure the window.
- **One registry per credential,** as imbu `963dfec` issues on its plain route (imbu `@2tdzyh85`), which is what makes the publication growth above per-credential.
- **Soaks are single runs.** A soak's interval comes from its buckets, not from repetitions. Each was run once, so between-run spread for drift is not measured.

## What the first pass got wrong

The matrix ran once on 2026-10-20 at harness `fc670cc` and was stopped, because three of its results were the harness's, not imbu's (`this.i` 2hn3mn3j):

- Reconciliation asked 32 status questions at once of the one holder and one verifier, timed out, and counted verified credentials as lost. It now asks 4 at a time, retries twice, calls a credential the verifier never answered unanswered, and runs only when the scenario verifies.
- At three homes the crash scenarios offered 2 per second through a lifecycle whose shared holder and verifier saturate well below that. They now offer 0.6 per second at both topologies.
- Contention from other workers on the shared host got 15 of 18 three-home throughput runs refused by the driver check. A sweep now runs up to six repetitions until three are accepted; in the final matrix none needed to.

After the final matrix, the three-home soak's store-size sampler was found to have died at 300 s, when a file vanished during the walk, and the report reused that last size for every later bucket. Both are fixed (`463c674`): imbu's sizing skips a vanished file, the sampler records a failed read and goes on, and a bucket uses a size only if it is at most two sampling intervals old. Every soak report was rebuilt from its unchanged timeline with the fixed generator. The three-home soak's size slope therefore rests on its first 17 buckets, the ones a sample covers, while its count and time slopes use all 55.

## Reproducing it

```
uv sync --extra stacks
systemd-run --user --unit=keri-bench-baseline -p LimitNOFILE=524288 --nice=19 \
    -E PATH="$PATH" -E HOME="$HOME" --working-directory="$PWD" \
    scripts/baseline.sh runs/baseline results/baseline-2026-10
```

On kelun the whole matrix took six hours. `keri-bench report <run-dir>` rebuilds any run's `report.json` and `report.md` from its timeline and provenance alone.
