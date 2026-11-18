# Baseline of today's imbu, October 2026: the HSM sweep and bulk issuance

This is the second half of the baseline in [`baseline-2026-10.md`](baseline-2026-10.md), which left two things unmeasured. The HSM sweep and the signer census waited for imbu to sign through an external signer. Bulk issuance waited for the SEDI proofed ceremony to be ported into keri-bench. Both are measured here, at one issuer and at three, on the same host and with the same method as the first half. Like it, this is a baseline and not a pass or fail. No Utah targets exist yet.

## What was measured, and on what

| | |
|---|---|
| Stack | imbu `8512c1d` (utah/next, with the external-signer seam and the restart fix), heti `841fcbd`, the same Registrar, Observer, witnesses, holder and verifier as the first half |
| Host | kelun: 8 cores (Intel Haswell), 23 GB, Linux 7.0, Python 3.14.6, shared with other workers, everything at nice 19 |
| Harness | keri-bench `a4fc364` for the HSM sweep and bulk issuance. The calibration passed at `a4fc364` (`results/baseline-2026-10-part2/calibration/calibration.json`). The crash recheck ran at `1341e1d` |
| Dates | 2026-10-21, 09:56 to 16:03 UTC |
| Design | `this.i` 2p26rn9h (the signer and census), 0v8kmser and 63txu3fm (the device and its floor), 02fe3uk5 (the grid), 4f987y88 and 9wm4y7g4 (the bulk ceremony and its sizes) |

Every number below comes from [`results/baseline-2026-10-part2/`](../results/baseline-2026-10-part2/), which holds each run's timeline, provenance and report and each sweep's `sweep.json`; `SHA256SUMS` covers every file. The scenarios are in [`scenarios/hsm-sweep/`](../scenarios/hsm-sweep/) and [`scenarios/bulk/`](../scenarios/bulk/). Each step has three accepted repetitions, and every refused run stays in the record with its reason.

## What the numbers say

1. **Each credential costs an HSM about 7 signatures on today's imbu, and only 2 of them are the credential's.** imbu signs every answer to an authenticated request with the principal's key, and that includes every operation poll. At the adapter's 50 ms poll that is 4 to 6 signed answers per credential. The other two are the `ixn` anchoring the credential's registry and the grant. The census pins the 2 in a stack test.
2. **HSM latency up to 120 ms per signature barely moves issuance.** Below saturation, p50 from request to witnessed rises from 0.37 s with no added latency to 0.50 s at 120 ms. About one signature's latency is on the critical path. One issuer still sustains 1.45 to 1.47 credentials a second at every latency, because imbu's asynchronous operations overlap signatures.
3. **A cap on the HSM's rate binds at a seventh of its value.** At 30 ms and 10 signatures a second, one issuer still sustains about 1.42 credentials a second. At 5 a second it collapses past 0.73: offered 1, it completes 0.27, since 5 signatures a second at about 7 per credential is about 0.7 credentials a second. Signing poll answers with a key the HSM does not hold, or answering without polls, would cut that load by about three and a half times. That is a design choice for imbu, not a measurement, and is offered as one.
4. **Bulk issuance is slower per credential than plain issuance, and registry inception is faster than either.** One issuer sustains about 0.1 SEDI ceremonies a second: 0.8 credentials a second, at 8 credentials a ceremony with 3 copies, p50 9.3 s. Three issuers sustain about 0.27 ceremonies, 2.2 credentials a second. Building the ceremony takes 8.2 s of that p50 before imbu anchors it. Provisioning the registry pool ahead of time runs at 4.0 to 4.2 registry inceptions a second per issuer. That is the split Utah's first pair asks for, credential construction against registry update, which the plain route cannot show.
5. **imbu-prj's restart fix holds.** At imbu `8512c1d`, a restarted imbu still seals registry salts to a holder it knew before the crash. Reconciliation after an issuer crash now loses 1 credential of 36, the one in flight at the kill, where it lost 24 at `963dfec`.

## The HSM sweep

Issuance only, request to witnessed, one issuer unless noted. The issuer's principal signs through heti's `ThrottledSigner` over a `SoftwareSigner`, whose schedule runs on a device loop of its own (`this.i` 0v8kmser). Each step is checked against its profile before it is reported (`docs/test-plan.md`, HSM sensitivity): the device's median latency within 5% of the configured value and, where the cap held an operation back, the spacing of those starts within 5% of 1/rate.

| profile | device latency, median | 0.25 /s | 0.5 /s | 0.75 /s | 1 /s | 1.5 /s: achieved | 1.5 /s: p50 | 1.5 /s: p99 |
|---|---|---|---|---|---|---|---|---|
| no added latency | under 0.001 ms | 0.36–0.37 s | 0.36–0.37 s | 0.37–0.38 s | 0.37–0.39 s | 1.47 /s | 0.40–0.61 s | 1.15 s |
| 15 ms | 15.38–15.71 ms | 0.37–0.41 s | 0.38–0.40 s | 0.39 s | 0.40 s | 1.47 /s | 0.58–1.29 s | 1.52 s |
| 30 ms | 30.35–30.85 ms | 0.39–0.40 s | 0.40 s | 0.41 s | 0.40–0.43 s | 1.47 /s | 0.55–0.74 s | 1.31 s |
| 60 ms | 60.63–60.95 ms | 0.42–0.43 s | 0.42–0.44 s | 0.44 s | 0.44 s | 1.46 /s | 0.90–1.46 s | 2.13 s |
| 120 ms | 120.90–121.02 ms | 0.48–0.49 s | 0.49–0.52 s | 0.51 s | 0.50–0.51 s | 1.45 /s | 1.49–2.42 s | 3.51 s |
| 30 ms, cap 10 /s | 30.35–30.39 ms; starts 101 ms apart | 0.39–0.40 s | 0.40–0.41 s | 0.40–0.41 s | 0.40–0.41 s | 1.42 /s | 0.70–1.05 s | 8.34 s |
| 30 ms, cap 5 /s | 30.36–30.84 ms; starts 201 ms apart | 0.42–0.43 s | 0.43–0.47 s | 0.45–0.65 s | collapses: 0.27 /s, p50 23–46 s | 0.10 /s | 23.6 s | 50.2 s |

The p50 cells are the range across three repetitions. Below 1.5 per second, achieved throughput equalled what was offered, except for the cap of 5 at 1 per second, where it collapsed. Sweeps: `hsm-<profile>/sweep.json`.

Three issuers, each with a signer of its own at 30 ms, uncapped (`hsm-30ms-3homes/`): p50 0.46 to 0.51 s at 1, 2 and 3 credentials a second offered, all achieved. Offered 4, they achieve 3.55, with p50 2.1 to 4.5 s. That is a little below the 4.0 the lockbox signer sustained in the first half.

**The 5 ms profile is not reported.** The device's median measured 5.34 to 5.40 ms, 7 to 8% over, and the check refused all 30 runs (`hsm-5ms/`, kept as the record). The lateness is this host's wake-up of a nice-19 thread, the same 0.35 to 0.9 ms at every latency, so the grid's floor is 15 ms (`this.i` 63txu3fm). The no-latency profile bounds the low end.

**A first pass was stopped.** It ran the device's schedule on imbu's own event loop, which woke its timers 1.5 to 2 ms late, so most 30 ms runs were refused at 31.2 to 32.1 ms. The device has its own loop now, so the check tests the device and imbu's lateness stays in imbu's numbers. That pass is not in the results.

### The signer census

The census counts the signer's own operations (`this.i` 2p26rn9h). It sets them against the answers imbu signed, which the adapter counts as signed headers, and against the credentials imbu accepted:

| profile | signatures per credential | signed answers per credential | other signatures per credential |
|---|---|---|---|
| no added latency | 7.2 | 4.9–5.8 | 2.0 |
| 15 ms | 6.9 | 4.6–5.5 | 1.94–2.0 |
| 30 ms | 6.5 | 4.2–5.0 | 1.98–2.0 |
| 60 ms | 7.1 | 5.0–5.3 | 1.80–2.0 |
| 120 ms | 7.2 | 5.0–6.6 | 1.73–2.0 |
| 30 ms, cap 10 /s | 5.7 | 3.2–6.2 | 1.84–2.0 |

"Other" is 2 wherever every accepted credential finished in the window. It reads lower where some were still running when the window closed: they count as accepted but have not yet signed their `ixn` and grant. Past saturation the ratios stop meaning anything (cap 5 at 1 per second reads 0.3 to 1.4 other and up to 12.6 answers per credential), and they are left out above. On top of these, the principal signs its inception once.

## Bulk issuance (`bulk-steady`)

imbu's SEDI proofed bulk ceremony (`this.i` 4f987y88). Each request is one ceremony. A citizen's SMAID presents an IAR the proofing agent signed for it, having anchored the IAR's nonce, and imbu issues the management Core and Residence pair plus 3 copy pairs, 8 credentials, each in a registry reserved from a pool provisioned before the window. Two citizens. Requests and polls are signed by the citizen through the citizens process. Measured request to published.

One issuer (`bulk-steady-1home/`):

| offered ceremonies /s | achieved (credentials /s) | p50 (each run) | p99 | accepted → anchored p50 | anchored → witnessed | witnessed → published |
|---|---|---|---|---|---|---|
| 0.05 | 0.05 (0.4) | 9.4, 9.2, 9.1 s | 9.8 s | 8.25 s | 1.17 s | 0.09 s |
| 0.075 | 0.081 (0.65) | 9.6, 9.1, 9.6 s | 10.1 s | 8.35 s | 1.08 s | 0.06 s |
| 0.1 | 0.10 (0.8) | 9.6, 9.3, 9.3 s | 10.5 s | 8.16 s | 1.13 s | 0.06 s |
| 0.125 | 0.08 (0.64) | 17.4, 39.4, 30.4 s | 99 s | 15.7 s | 1.18 s | 0.14 s |
| 0.15 | 0.05 (0.4) | 24.5, 28.3, 23.8 s | 84 s | 22.8 s | 1.88 s | 0.12 s |
| 0.2 | 0.025 (0.2) | 76.5, 101 s | 95 s | 67.5 s | 8.4 s | not seen |

Three issuers (`bulk-steady-3homes/`):

| offered ceremonies /s | achieved (credentials /s) | p50 (each run) | p99 | accepted → anchored p50 |
|---|---|---|---|---|
| 0.1 | 0.10 (0.8) | 8.7, 8.8, 8.5 s | 9.6 s | 7.7 s |
| 0.2 | 0.196 (1.57) | 9.5, 9.5, 9.4 s | 10.6 s | 8.4 s |
| 0.3 | 0.272 (2.18) | 10.8, 10.4, 11.3 s | 21.2 s | 9.2 s |
| 0.4 | 0.144 (1.15) | 22.0, 22.8, 24.0 s | 96 s | 20.0 s |

The segment p50s are from the first repetition of each step.

Past saturation goodput collapses, as on the plain route: operations pass their 120 s timeout. Provisioning the pool ran at 3.9 to 4.2 registries a second at one issuer and 4.7 to 5.1 per issuer at three, timed from imbu's own stamps on each pool operation and carried in the census (`census.issuer.pool`).

What bulk-steady does not show:

- **The far node is read locally.** The Utah agent credential that every Core names is issued by a second facet inside the same issuer. A deployment whose agent credential lives on another issuer's registry adds one far-Observer read per ceremony, and this does not measure it.
- **Two of every eight credentials are counted on imbu's word.** imbu's result names the copy pairs, each SAID checked distinct and new, but not the management pair, which it issues to the citizen and keeps. Counting 8 credentials a ceremony rests on that design (tick `~7soc`); on the copies alone, credentials a second are three quarters of the figures above.
- **Nothing is delivered.** Copy holders are fresh keys' AIDs, so neither delivery to six holders nor their admission is in these numbers.
- **The citizen's own work is inside every interval.** That is the IAR, the anchoring of its nonce and the SMAID's signatures, as a citizen's wallet's would be.
- **A crashed bulk issuer can refuse the ceremony it was running.** In the stack test that crashes one, a ceremony imbu resumed after the restart failed `e.state.conflict.registry.reconciliation.f`: the reserved registry could not be reconciled with the issuance evidence imbu had kept. New ceremonies went on. bulk-steady itself schedules no crash, so this is an observation from a test, reported to the plan's PM, not a measurement.

## The crash recheck

`scenarios/baseline/fault-crash-issuer-1home.toml`, rerun at imbu `8512c1d` (`fault-crash-issuer-1home-recheck/`). Ready 1.38 to 1.48 s after the kill, and the first new success 2.74 to 3.76 s after ready. Reconciliation: 34 done, 1 resumed, 1 lost in each of three repetitions, against 11, 1 and 24 at `963dfec`. The one lost is the issuance in flight when imbu was killed.

## Limits

The first half's limits all still hold: one shared host, no Utah targets, a 2 s Registrar window, and one registry per credential. In addition:

- **The HSM is simulated.** The latencies and caps are profiles, not measurements of any device. Calibrating one point against a real KMS is Daniel's open question Q-SNJA.
- **The poll interval is the adapter's 50 ms** (100 ms for the bulk ceremony). It sets how many answers imbu signs, so the census figures are per deployment choice, and a client that polls less signs less.
- **The grid has no point between the no-latency signer and 15 ms**, for the reason above.
