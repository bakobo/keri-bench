# Today's imbu against Utah's targets, October 2026

Utah has now stated targets for four of the five things the baselines measured ([`baseline-2026-10.md`](baseline-2026-10.md), [`baseline-2026-10-part2.md`](baseline-2026-10-part2.md)). This report measures today's imbu against them with the same instrument, host and method, and gives a verdict where Utah gave a target. Where it gave none, it reports the number without one.

In our words, from Utah's answers of 2026-10-25:

1. **Volume.** At most 10,000 credentials a day, a ceiling Utah does not yet have strong evidence for. The Drivers License Division's real peak is still to come, so this report takes the worst case, all 10,000 in one hour (2.8 a second), as the peak-hour target, and also judges 10,000 spread over an eight-hour office day (0.35 a second).
2. **Stock.** Up to a million SEDI credentials over a decade for Utah, and more for the coalition's larger states.
3. **Revocation freshness.** A revocation must reach verifiers within 30 minutes.
4. **Crash recovery.** No target yet.
5. **HSM.** Probably a Thales network HSM doing thousands of signatures a second, chosen so State staff cannot extract keys, not for speed.

## The verdicts

| target | verdict | in one line |
|---|---|---|
| 1. Volume, peak hour (2.8 a second) | **Fails** at 1 home and at 3 | One home saturates near 1.5 a second; three carry 2.8 for about 1,900 credentials and then fall behind for good. |
| 1. Volume, office day (0.35 a second) | **Fails** at 1 home, **passes** at 3 for one day | One home keeps up for 4,036 credentials and never recovers; three homes witness all 10,000 (p50 1.39 s, p99 2.50 s) but are on course to fall behind on the fourth or fifth day. |
| 2. Stock to a million | No pass/fail given; **storage is linear, issuance is not** | 13.2 KB a credential on disk, 13 GB at a million. Time per credential grows 0.76 ms with each credential in the issuer's KEL, so it crosses the office-day rate near 4,000 credentials per KEL; a million is extrapolated, not run. |
| 3. Revocation within 30 minutes | **Not established** at the volume target; the 9 measured are within it, 872–927 s | Only 9 of 100 sampled credentials reached revocation, all from the first hour; heti's holder and verifier timed out for the rest, so freshness later in the run, as the issuers' KELs grew, is unmeasured. |
| 4. Crash recovery | No target | Every role ready in 3.7 s or less, next success within 4.2 s, only in-flight credentials lost; an issuer holding 10,000 takes 10.8 s and 18.4 s. |
| 5. HSM | Ample headroom | 3 signatures a credential with Prefer: wait, 6.6 when polling; 8.4 or 18.5 a second at the peak, against thousands a second. |

The one finding under all of them: at imbu 670a3d8 an issuer's cost per credential grows with its own KEL, and every volume verdict is that growth meeting a rate.

## Target 1: volume

### The peak hour, 2.8 a second

**Fails at one home and at three.** Each run offered 2.8 credentials a second, open loop, and measured from the scheduled request to the moment imbu recorded the witnesses' receipts (requested to witnessed).

| | offered | witnessed | witnessed /s | p50, first 200 | where it broke |
|---|---|---|---|---|---|
| 1 home, 10 minutes | 1,680 | 85 | 0.14 | 22 s at the first 200 | from the start |
| 3 homes, 1 hour | 10,080 | 2,000 | 0.56 | 0.39 s | about 1,900 credentials, 11 minutes in |

One home saturates between 1.5 and 2 credentials a second even from an empty store (probes, `this.i` 9cuqzk0u), so 2.8 overloads it at once. It was run for ten minutes rather than the hour, because an hour of collapse says nothing more.

Three homes carry 2.8 a second at first, with p50 0.39 s and p99 0.51 s over the first 200 credentials, but not for long. p50 rose to 0.64 s by 1,100 credentials and 1.25 s by 1,700, and then imbu's anchoring queue tipped: p99 was 106 s at 1,900, and from 2,300 on no bucket of 200 had a credential witnessed within imbu's 60 s operation timeout. Each home was left holding about 1,980 pending operations, which it was still finishing 35 minutes later.

**What breaks it is witnessing, which grows with each issuer's KEL.** imbu's own stamps split the time (median of each 200 consecutive credentials, three homes):

| credentials into the run | accepted → anchored | anchored → witnessed | witnessed → published |
|---|---|---|---|
| 0 | 0.26 s | 0.12 s | 0.26 s |
| 600 | 0.27 s | 0.25 s | 0.27 s |
| 1,200 | 0.36 s | 0.43 s | 0.59 s |
| 1,600 | 0.33 s | 0.50 s | 0.62 s |
| 1,800 | 5.11 s | 1.20 s | 1.61 s |

Anchoring stays flat until the queue tips. Anchored to witnessed rises by about 0.65 ms for every credential a home already holds, the same slope the first baseline found at one home (0.13 s to 0.79 s over 1,050 credentials). imbu 670a3d8 no longer sends the KEL in its publications (imbu @7qgv63f5), and that did not change this slope, because it is not publication. During the run each issuer process ran at about 82% of a core and each witness under 10%, so the growing cost is in the issuer's process, imbu with heti and keripy beneath it, not in the witnesses. This host has no profiler, so which function grows is not measured here.

### The office day, 0.35 a second

**Fails at one home.** At 0.35 a second, one home witnessed every credential for the first 4,036, a little over three hours, and then collapsed the same way the peak hour did. Of the 10,080 offered, 4,036 were witnessed (0.14 a second over the eight hours). p50 requested to witnessed rose from 0.42 s at 100 credentials to 2.25 s at 3,300, and p99 from 0.57 s to 2.69 s.

The home did not recover. For the remaining 4.6 hours every request failed: most timed out in the client, and imbu answered 5,063 of the client's attempts with 401 `e.id.expired.signature.f`, because a signed request had aged past imbu's freshness bound while it waited in imbu's queue. imbu's store grew by only 1.8 MB in that time, about 135 credentials at the rate below, while the issuer process ran at a full core. A home pushed past its capacity at this imbu does not drain its backlog and resume. It stays saturated.

imbu's stamps, median of each 500 consecutive credentials:

| credentials into the run | accepted → anchored | anchored → witnessed | witnessed → published |
|---|---|---|---|
| 0 | 0.27 s | 0.25 s | 0.26 s |
| 1,000 | 0.31 s | 0.82 s | 0.26 s |
| 2,000 | 0.34 s | 1.31 s | 0.25 s |
| 3,000 | 0.38 s | 1.84 s | 0.25 s |
| 3,500 | 0.40 s | 2.11 s | 0.26 s |
| 4,000 | 6.65 s | 3.65 s | 1.91 s |

Anchored to witnessed rises about 0.53 ms for each credential the home holds, close to the three-home peak hour's 0.65 ms. The collapse comes where the three stages together, about 2.8 s at 3,500 credentials, reach the 2.86 s between arrivals at 0.35 a second. That a home falls behind exactly when its per-credential time reaches the gap between arrivals says it finishes one credential before the next starts its witnessing, whatever the client concurrency. So a home's capacity at this imbu is not a rate but a stock: at rate r it keeps up until the time per credential reaches 1/r, which at 0.35 a second is about 4,000 credentials and at 2.8 a second (0.36 s) is almost none.

The driver was not the limit. Its dispatch lag stayed under 1.2 ms at p99 throughout, because the driver spins on its own core by design rather than sleeping (`src/keri_bench/driver.py`), and the host's load average was 2.2–3.4 on 8 cores (`loadavg.log`).

**Passes at three homes, for one day.** Three homes, 0.35 a second between them, stopped when 10,000 credentials were witnessed, 7.9 hours in. Every credential was witnessed, with requested to witnessed p50 1.39 s and p99 2.50 s over the run. Every 101st credential also went through delivery, presentation, verification and revocation (target 3). The host's load average was 1.7 during the window.

It passes because each home held only about 3,333 credentials, and the same growth is there. p50 requested to witnessed, per 200 credentials:

| credentials into the run (about a third per home) | p50 | p99 |
|---|---|---|
| 100 | 0.38 s | 0.49 s |
| 2,900 | 0.96 s | 1.18 s |
| 5,700 | 1.54 s | 1.90 s |
| 8,500 | 2.07 s | 2.50 s |
| 9,900 | 2.41 s | 3.04 s |

Each home gets a credential every 8.6 s at this rate. Its time per credential reaches that at about 13,500 credentials in its KEL by this run's own slope (0.61 ms a credential per home), or about 11,200 by the one-home fit through 10,000 (target 2). **The volume target is a daily rate, but a home's KEL keeps everything it has issued.** Each office day adds about 3,333 credentials to each of three homes, so three homes at this imbu would fall behind during the fourth or fifth day, and one home falls behind on the first.

## Target 2: stock

**Storage no longer has a wall. Issuance does, in each issuer's KEL, and it arrives at thousands of credentials, not a million.** The stock curve comes from one home: the office day's buckets from empty to 4,000 credentials, before that home fell behind (later buckets measure its backlog, not its stock), and `stock-preseeded-1home`, a store filled with 10,000 credentials through the same issuance path and then offered 0.1 a second for 30 minutes, a rate a home at that stock can carry, with the issuer killed at 900 s. Both were fitted with `keri-bench growth` (`results/targets-2026-10/growth/imbu-670a3d8-1home.json`), 26 buckets. The preseed was sized down from 40,000 because of the curve itself (`this.i` 0kc32dlp): filling 10,000 took 14.1 hours.

| | fit | R² | at 10,000 | at 100,000 | at 1,000,000 |
|---|---|---|---|---|---|
| store bytes, imbu 670a3d8 | 13.2 KB a credential, linear | 0.99999 | 132 MB (measured 133 MB) | 1.3 GB | 13 GB |
| store bytes, imbu 963dfec (first baseline) | 413 bytes × n², quadratic | 0.9996 | 41 GB | 4.1 TB | 413 TB |
| p50 requested → witnessed | 0.07 s + 0.76 ms a credential, linear | 0.993 | 7.6 s (measured 7.9 s) | 76 s | 13 minutes |
| p50, quadratic fit | 0.41 s + 0.52 ms × n + 2.1e-8 × n² | 0.997 | 7.7 s | 4.4 minutes | 6 hours |

Storage: the quadratic that the first baseline found came from publishing the issuer's KEL with every credential, and imbu 670a3d8 does not (imbu @7qgv63f5). What is left is 13.2 KB a credential, straight. A quadratic fit gives it a squared term of 0.008 bytes, which is noise. A million credentials is 13 GB on one home's disk.

Latency: time per credential grows with the credentials already in the issuer's KEL, 0.76 ms each, nearly all of it in anchored → witnessed (6.9 s of the 7.9 s p50 at 10,000). With only 4,000 and 10,000 as anchors, linear and quadratic fits agree at 10,000 and part beyond it. Everything past 10,000 in this table is extrapolated, and the two fits differ by a factor of 30 at a million.

Where it crosses the volume target: a home keeps up while its time per credential is under the gap between arrivals. At the office day's 0.35 a second that gap is 2.86 s, which the fits reach at 3,700 (linear) and 4,050 (quadratic) credentials. The measured collapse was at 4,036. At the peak hour's 2.8 a second it is reached almost at once.

Where the wall bites: the cost grows per credential, so the work to fill one KEL grows with the square of its length. 10,000 took 14.1 hours to preseed with 4 in flight. By the linear fit a million in one KEL would take about 12 years of issuing time, and the last credential alone 13 minutes. Utah's million therefore has to be spread across many issuer identifiers, each kept to a few thousand credentials if it is to carry the office-day rate, or imbu has to stop the per-credential cost from growing with its KEL. Which of those is right is imbu-prj's question, not this report's. Nothing here measured more than 10,180 credentials in one KEL, and no one-million run was attempted.

Restart at 10,000: imbu killed at 900 s was ready again in 10.8 s and completed its next credential 18.4 s after the kill, where the crash scenarios below, at a few dozen credentials, show 1.4 s and 2.7–3.5 s. It lost the 2 credentials in flight. p50 was 7.8 s before and 8.0 s after. One point says restart time grows with stock but not how; it was not fitted.

## Target 3: revocation freshness

**Not established at the volume target.** The nine revocations that could be measured were recognised within 30 minutes, with about 15 minutes to spare, but they were all issued in the run's first hour, and the other 91 sampled credentials never reached revocation. That is not enough to say the target holds through an office day, so this report gives no pass. The office day at three homes sent every 101st credential through the whole lifecycle and revoked it once a verifier had accepted it. The deployment was configured as Utah would run it: the Registrar's 900 s window, verifier freshness 900 s, so the design bound W + F is the 30-minute target exactly, and recognition is polled every 10 s until two polls agree.

| | p50 | max | of 1,800 s |
|---|---|---|---|
| revocation requested → published (anchored, witnessed, published by imbu) | 0.84 s | 1.28 s | |
| published → recognised by the verifier | 911 s | 930 s | |
| requested → recognised | 907 s | 927 s | 870 s margin at the worst |

All nine were recognised, between 872 s and 927 s. imbu's part is about a second; the rest is waiting for state to move. Both the Registrar's window and the verifier's freshness are 900 s here, so this run cannot say which of them carried each revocation. What it can say is that the Registrar path was struggling: heti's Observer refused 25 of the Registrar's 900 s batches during the run, each because judging it ran past heti's 120 s bound (`BATCH_JUDGE_TIMEOUT`, heti `observer/service.py`), and resubscribed after each. Its log does not record which batches it accepted. On this stack each recognition poll has the holder present again on the chain the Observer holds (`docs/adapters.md`), so the 900 s steps may as well come from the Observer's resubscriptions as from verifier freshness.

What this does not show:

- **Most of the sample never got to revocation.** Of the 100 sampled credentials, 9 were verified. Verification at the heti verifier exceeded the harness's 60 s client timeout from credential 707 on (12 failed), and delivery to the heti holder from credential 2,121 on (79 failed). So nothing past the first 1,212 credentials was revoked, and how revocation behaves when the issuers hold 3,000 credentials each is not measured. The holder and verifier are heti's, and why they slow is not measured here.
- **Nine is a small sample.** The spread, 872 to 927 s, is the verifier's freshness timer plus polling, and nine draws say little about its tail.
- **At the peak rate it could not be measured at all** (the peak hour above): the Observer refused every batch and nothing verified.

## Target 4: crash recovery

**No verdict, since Utah has given no target. Every role is ready again within 3.7 s of being killed, the next credential completes within 4.2 s of that, and no crash loses more than the credentials in flight.** These are the first baseline's twelve crash scenarios, unchanged except for the issuer adapter's knobs (a waiting client, imbu's default answer budget), so the 2 s Registrar window and 0.6 lifecycles a second are the baseline's and the numbers compare with its own (`baseline-2026-10.md`, Restart recovery). Each kills one target at 20 s of a 60 s window and restarts it on its own disk. All 36 runs were accepted (`results/targets-2026-10/crash-*`). Ranges are across three repetitions, p50 and p99 are requested to verified, and reconciliation is summed over the three.

| crashed | homes | ready s | first success s | p50 before s | p50 after s | p99 after s | done / resumed / lost / unanswered |
|---|---|---|---|---|---|---|---|
| imbu (every home) | 1 | 1.36–1.47 | 2.74–3.47 | 2.49–2.54 | 3.18–3.40 | 4.17–4.64 | 102 / 3 / 3 / 0 |
| imbu home h0 of 3 | 3 | 1.38–1.44 | 2.96–3.43 (h0's own: 5.61–7.21) | 2.15–2.42 | 2.59–2.75 | 3.81–4.01 | 102 / 3 / 3 / 0 |
| one witness of 3 (toad 2) | 1 | 1.02–1.08 | 1.41–2.03 | 2.37–2.45 | 3.43–3.76 | 4.41–4.71 | 102 / 6 / 0 / 0 |
| one witness of 3 (toad 2) | 3 | 1.09–1.16 | 0.07–2.38 | 2.09–2.41 | 2.59–2.67 | 3.73–4.14 | 102 / 6 / 0 / 0 |
| all three witnesses | 1 | 3.15–3.33 | 1.43–2.00 | 2.23–2.44 | 3.39–4.70 | 4.29–11.13 | 98 / 10 / 0 / 0 |
| all three witnesses | 3 | 3.29–3.68 | 1.16–1.65 | 2.36–2.50 | 2.48–2.64 | 3.83–3.96 | 98 / 10 / 0 / 0 |
| Registrar and Observer | 1 | 2.51–2.96 | 1.07–3.77 | 2.49–2.60 | 3.35–4.79 | 4.57–6.44 | 99 / 9 / 0 / 0 |
| Registrar and Observer | 3 | 2.85–2.94 | 3.13–3.43 | 2.19–2.88 | 2.60–2.77 | 3.91–4.04 | 99 / 9 / 0 / 0 |
| holder | 1 | 0.98–1.03 | 3.82–4.18 | 2.29–2.64 | 3.24–3.39 | 4.32–4.93 | 102 / 2 / 4 / 0 |
| holder | 3 | 1.00–1.05 | 2.61–4.23 | 2.03–2.37 | 2.51–2.71 | 3.94–4.14 | 102 / 3 / 3 / 0 |
| verifier | 1 | 1.05–1.08 | 1.14–1.47 | 2.30–2.68 | 3.41–3.43 | 4.45–4.91 | 102 / 6 / 0 / 0 |
| verifier | 3 | 1.03–1.16 | 1.04–1.21 | 2.41–2.47 | 2.52–2.75 | 3.83–4.13 | 102 / 6 / 0 / 0 |

Against the baseline at 963dfec, every time to ready is within a few tenths of a second of what it was. The change is the issuer's losses: the restart fix (imbu `8512c1d`) holds at this imbu, so a crashed imbu loses only the one credential in flight in each repetition, where 963dfec lost 72 of 108 at one home and 24 at three. "h0's own" is the first credential home h0 itself completed after its restart, read from the timelines by the `home` attribute, as the baseline did; the report's first success at three homes comes from the homes that never stopped (tick `~76aw`).

What these do not show is how recovery changes with stock: every one of these restarts a store holding a few dozen credentials. The stock run (target 2) kills a home holding 40,000.

## Target 5: HSM headroom

**Today's imbu asks an HSM for 3 signatures a credential when its client waits, and about 6.6 when it polls every 50 ms.** At the peak-hour target that is 8.4 or 18.5 signatures a second across the deployment, against the thousands a second Utah's device does. A network HSM's latency does not move issuance at that rate either.

One profile was run, a signer per home at 15 ms per signature and uncapped, standing for a network HSM in the same data centre. 15 ms is this host's floor for a simulated device (`this.i` 63txu3fm); a real one on the same network would likely answer in a few milliseconds, so this errs slow. Three homes, 2.8 a second, three 120 s repetitions of each client (`results/targets-2026-10/hsm-15ms-*`):

| client | signatures per credential | of which signed answers | witnessed /s | p50 | p99 | signer median |
|---|---|---|---|---|---|---|
| waits (Prefer: wait) | 3.01 | 1.0 | 2.79–2.80 | 0.43 s | 0.52–0.58 s | 15.24 ms |
| polls every 50 ms, budget lifted | 6.53–6.71 | 4.5–4.7 | 2.80 | 0.42–0.43 s | 0.50–0.53 s | 15.45 ms |
| polls every 50 ms, imbu's default budget | 6.60–6.71 | 4.6–4.7 | 2.80 | 0.43–0.44 s | 0.51–0.54 s | 15.45 ms |

Each credential's own signatures are 2, the ixn that anchors its registry and the grant, in every run. Prefer: wait (imbu @8f8e7hkm) cuts the signed answers from about 4.6 a credential to 1, which is the cut imbu-prj predicted. The other 0.01 is the one answer resolving the holder before the window opened. Latency is the same either way, because waiting replaces polls rather than adding a wait.

imbu's default answer budget, 2 signed answers a second per controller with a burst of 20 (imbu @6u3jm9e4), refused nothing here. The load was dealt to 4 controllers a home, and each polls only while its own operation is in flight, about 1.1 answers a second each. The budget binds on one controller that polls faster than that, which the stack test exercises (`tests/stacks/test_stack.py`) and this run does not.

Against the device: a credential needs 3 signatures, so 2.8 credentials a second need 8.4 signatures a second, and 18.5 if every client polls at 50 ms. A device doing even a thousand a second has about 120 times that headroom with waiting clients and about 54 times with polling ones, and more for each further thousand. At the office-day rate, 0.35 a second, it is 1.1 signatures a second. The HSM is not what limits issuance today; witnessing is (target 1).

What this does not show:

- **No real device was measured.** The profile is a simulation, so neither a Thales HSM's actual latency nor its behaviour under tenants sharing it is in these numbers (Daniel's open question Q-SNJA still stands).
- **Only issuance is counted.** Revocation, rotation and the principal's other answers also cost signatures; the census counts what this scenario asked.
- **Polling at 50 ms is the adapter's choice.** A client that polls less often signs less, and one that polls more often is what the budget is for.

## Measured on

All on kelun, a shared 8-core host with 23 GB of RAM, Linux 7.0.0-38, Python 3.14, every stack under a named `systemd-run --user` unit at nice 19 with `LimitNOFILE=524288`. Other sessions' work shared the host. Each run's provenance records the load average before, during and at the end of its window, and `results/targets-2026-10/loadavg.log` has it every 60 s across all of them. The long runs' windows ran at load 1.4 to 2.6, and the HSM runs, three homes at 2.8 a second for two minutes, at up to 4.6, most of it their own stack.

| what | commit |
|---|---|
| imbu | 670a3d874a367aeb3c8cea132b5d0ae98646a3a1 (utah/next, with Prefer: wait @8f8e7hkm, the answer budget @6u3jm9e4 and publication without the issuer's KEL @7qgv63f5) |
| heti | 841fcbd3c6952be729666015ecaa0e28155004cf |
| keripy | 910dcb350e63d3129c91345429c1fa10f2dae5e5 (bakobo fork, through heti) |
| witness (Registrar) | ed1dcfc563ebff6545738f820d7410043dcbfbce |
| harness, probes | 4a328f0 |
| harness, peak hour | d09bde9 |
| harness, office day at 1 home, crash and HSM runs | dab9469 |
| harness, office day at 3 homes and stock | 1e92aa0 |

Between dab9469 and 1e92aa0 the harness's HTTP client was fixed to read an answer to its end rather than what had arrived (1e92aa0). Before the fix it cut revocations' operation documents short in the first attempt at the three-home office day, which was stopped and rerun; no timeline from an earlier run in these results has the resulting error.

Sizes and every change of plan are recorded in `this.i` under 4j5395l8 (method), 8654nxrl (lifecycle sampling), 9cuqzk0u (sizes from the probes), 2p4c5jhw (revocation moves to the office day) and 0kc32dlp (the stock run resized).

## What this does not measure

- **More than one host.** Every party ran on one machine over loopback, so network latency between issuer, witnesses and verifiers is absent and contention between them is present.
- **A million credentials.** The largest KEL measured held 10,180. Everything beyond is a fitted curve, and the linear and quadratic fits differ by a factor of 30 at a million.
- **Why issuance grows with the KEL.** The growth is in the issuer's process, mostly in anchored → witnessed. This host allows no profiler, so the function is not named.
- **Why heti's Observer, holder and verifier slow.** They are measured as Utah would deploy them and reported as they behaved; their causes belong to heti.
- **Repetitions for the long runs.** The peak hour, office day and stock runs ran once each, as soaks; their spread is across their buckets, not across repetitions. The crash and HSM runs have three repetitions each.

## Reproduce

```
uv sync --extra stacks
systemd-run --user --unit=keri-bench-targets -p LimitNOFILE=524288 --nice=19 \
    -E PATH="$PATH" -E HOME="$HOME" --working-directory="$PWD" \
    scripts/targets.sh runs/targets results/targets-2026-10 scenarios/targets/<name>.toml ...
uv run keri-bench growth results/targets-2026-10/growth/office-day-1home-before-collapse.json \
    results/targets-2026-10/stock-preseeded-1home/rate-0.1/rep-1/report.json \
    --at 10000 --at 100000 --at 1000000 --bound-s 2.857 --out growth.json
```

`office-day-1home-before-collapse.json` is the office day's soak buckets up to 4,000 credentials, cut from its `report.json`; the file says so. Each result directory has the run's timeline, provenance and report, and `SHA256SUMS` covers them all. Timelines over 1 MB are gzipped.
