# Adapters and their stack bindings

The test plan defines each milestone by the event that counts and leaves stack-specific bindings to the adapters (`docs/test-plan.md`, Milestones). This file records those bindings. The interface is in `src/keri_bench/adapters/base.py` (`this.i` 2vu4969q). Every adapter declares its milestones, with method, clock and resolution, before it runs, and the contract suite (`keri-bench contract`) checks it against what it actually emits.

## The imbu stack

| Milestone | Adapter | Method, clock | What counts |
|---|---|---|---|
| `requested` | harness driver | observed, harness | The scheduled send time. |
| `accepted` | `imbu.issuer` | observed, harness | imbu's 202 answer to the issuance request. |
| `constructed`, `registry_updated`, `anchored` | `imbu.issuer` | reported, realtime | The times imbu's operation resource records. `constructed` and `registry_updated` are only there if imbu-async stamps them; the bench asked for this through the plan's PM on 2026-10-15. |
| `witnessed` | `imbu.issuer`, filed under the witness pool | reported, realtime | Receipts reached toad, as imbu's operation records it (`this.i` 0ys878gy). |
| `published` | `imbu.issuer`, filed under state distribution | reported, realtime | The Registrar acknowledged the snapshot (its 2xx means the snapshot is the new head), as imbu records it. |
| `grant_sent` | harness relay | observed, harness | imbu returns the grant to its caller and delivers nothing itself, so the harness relays it. The channel is named `harness-relay`. |
| `delivered` | `heti.holder` | observed, harness | The holder's endpoint answered 202 to the grant bytes. |
| `stored` | `heti.holder` | observed, harness | heti's `admit` returned. It verifies the credential and keeps it. |
| `proof_generated` | `heti.holder` | observed, harness | The holder returned a grant addressed to the verifier. |
| `verified` | `heti.verifier` | observed, harness | The first acceptance by an independent heti lockbox, checking currency against the Observer (`this.i` 27mjdj7q). Refusals are retried until `deadline_s`, and the attempts are recorded. |
| `revocation_requested` | `imbu.issuer` | observed, harness | The revocation request left the adapter. |
| `revocation_anchored`, `revocation_witnessed`, `revocation_published` | `imbu.issuer` | reported, realtime | Times from imbu's revocation operation. |
| `revocation_recognised` | harness monitor | polled, harness | The first poll of the final unbroken run of `stable_polls` polls in which the verifier says revoked. The first poll that said revoked is kept beside it. |
| `sync_full_started`, `sync_full_done` | `heti.verifier` | observed, harness | A cold read and parse of every registry the verifier has met, after the drain. |
| `sync_increment_applied` | none | unobserved | No adapter observes it yet, so pair 5's incremental half is a named gap. |

## The processes behind the imbu stack

- **Witnesses.** These are real keripy witnesses (`keri_bench.stacks.witnesses`), each advertising the harness's proxy, so receipts pass through it.
- **Registrar.** bakobo/witness's Registrar, run with uvx at the summit harness's pin.
- **Observer.** heti's Observer, subscribed to the Registrar through the proxy.
- **imbu.** imbu's own `Service` and control plane, with the Registrar publisher injected as the summit harness does, because `imbu serve` wires none (imbu tick `~3p7h`).

## Known limits

- **Registrar to Observer.** The Registrar's batches go to the callback address the Observer registers itself, so that one link bypasses the fault proxy.
- **Shared registry on simple issuance.** imbu's `/v1/imbu/issuances` puts every credential in one shared registry. heti judges a registry by its head against the Observer, so only the newest credential verifies independently (tick `~4sfp`). The bench needs one registry per credential, as SEDI's pool has, before verification numbers on imbu mean anything at more than one credential in flight.
- **imbu issuer adapter untested against imbu.** It is written against imbu-async's brief and has never run against real imbu. Its integration test skips, with the reason, until imbu has operations.
- **Signer census.** It reads `not measured` until heti's external-signer interface exists (plan item heti-signer).

## The calibration stack

`synthetic.*` adapters drive four small processes whose delays are injected and logged as truth (`this.i` 6he49n0k). The issuer reports on a stack clock deliberately skewed from wall time, so the offset path is exercised. State distribution's `published` is observed at the proxy, so that path is exercised too.
