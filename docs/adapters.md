# Adapters and their stack bindings

The test plan defines each milestone by the event that counts and leaves stack-specific bindings to the adapters (`docs/test-plan.md`, Milestones). This file records those bindings. The interface is in `src/keri_bench/adapters/base.py` (`this.i` 2vu4969q). Every adapter declares its milestones, with method, clock and resolution, before it runs, and the contract suite (`keri-bench contract`) checks it against what it actually emits.

## The imbu stack

| Milestone | Adapter | Method, clock | What counts |
|---|---|---|---|
| `requested` | harness driver | observed, harness | The scheduled send time. |
| `accepted` | `imbu.issuer` | reported, realtime | The time imbu's operation records accepting the request (imbu 670a3d8 stamps it itself; adapter version 4). |
| `anchored` | `imbu.issuer` | reported, realtime | The time imbu's operation resource records, RFC 3339 with microseconds (imbu `src/imbu/operations.py`). |
| `constructed`, `registry_updated` | none | unobserved | imbu's operation stamps neither, so pair 1's split before anchoring is a named gap. |
| `witnessed` | `imbu.issuer`, filed under the witness pool | reported, realtime | Receipts reached toad, as imbu's operation records it (`this.i` 0ys878gy). |
| `published` | `imbu.issuer`, filed under state distribution | reported, realtime | The Registrar acknowledged the snapshot (its 2xx means the snapshot is the new head), as imbu records it. |
| `grant_sent` | harness relay | observed, harness | imbu returns the grant to its caller and delivers nothing itself, so the harness relays it. The channel is named `harness-relay`. |
| `delivered` | `heti.holder` | observed, harness | The holder's endpoint answered 202 to the grant bytes. |
| `stored` | `heti.holder` | observed, harness | heti's `admit` returned. It verifies the credential and keeps it. |
| `proof_generated` | `heti.holder` | observed, harness | The holder returned a grant addressed to the verifier. |
| `verified` | `heti.verifier` | observed, harness | The first acceptance by an independent heti lockbox, checking currency against the Observer (`this.i` 27mjdj7q). Refusals are retried until `deadline_s`, and the attempts are recorded. |
| `revocation_requested` | `imbu.issuer` | observed, harness | The revocation request left the adapter. |
| `revocation_anchored`, `revocation_witnessed`, `revocation_published` | `imbu.issuer` | reported, realtime | Times from imbu's revocation operation. |
| `revocation_recognised` | harness monitor | polled, harness | The first poll of the final unbroken run of `stable_polls` polls in which the verifier says revoked. The first poll that said revoked is kept beside it. On this stack each poll is a fresh presentation: the verifier asks the holder to present again on the chain the Observer holds now, and judges that. |
| `sync_full_started`, `sync_full_done` | `heti.verifier` | observed, harness | A cold read and parse of every registry the verifier has met, after the drain. |
| `sync_increment_applied` | none | unobserved | No adapter observes it yet, so pair 5's incremental half is a named gap. |

## The processes behind the imbu stack

- **Witnesses.** These are real keripy witnesses (`keri_bench.stacks.witnesses`), each advertising the harness's proxy, so receipts pass through it. Each runs in its own process, `witness_pool.w<i>`, with its keystore under the run directory and its salt derived from one seed per run, so a crash fault can kill one witness or the whole pool and a restart is the same witness holding what it held (`this.i` 5ed29j5w).
- **Registrar.** bakobo/witness's Registrar, run with uvx at the summit harness's pin.
- **Observer.** heti's Observer, subscribed to the Registrar through the proxy.
- **imbu.** imbu's own `Service` and control plane with its own `RegistrarPublisher`, stood up as `imbu serve --registrar-url` stands them up. Every issuance gets a registry of its own (imbu `@2tdzyh85`). Before the measured window every home resolves the holder's OOBI (`IssuerAdapter.meet`), as an issuer knows its issuee: heti seals a registry's salt to the issuee only when the issuer holds the issuee's key state.
- **Homes and clients.** The issuer's `homes` knob runs that many imbu processes, `issuer.h<i>`, each with its own home under `<run>/imbu/h<i>`, sharing the witnesses, the Registrar, the Observer, the holder and the verifier. Its `clients` knob authorises that many controllers per home, each signing with its own key over its own connection pool. Credentials are dealt to homes round-robin and then to each home's clients round-robin, and every issuer event carries `home` and `client` attributes (`this.i` 2lkqz9mq). The Observer, holder and verifier resolve one OOBI per home.
- **Restarts.** A crashed process restarts on what it had on disk: imbu reopens its home, the holder and verifier reopen their lockboxes, and the bench wrappers keep what they would otherwise forget in a spool beside the lockbox (the holder its delivered grants, which it admits again after a restart; the verifier the credentials it has verified). The Registrar and the Observer reopen their stores as they always do.
- **Store size.** During a soak the issuer reports every byte under every home, sampled every `[soak] sample_s`.
- **The external signer.** With `signer = "external"` each home's principal is minted and signed for by heti's `ThrottledSigner` over a `SoftwareSigner`, at `signer_latency_ms` per operation and at most `signer_rate` starts a second, attached on every start (`this.i` 2p26rn9h). The issuer process serves the signer's record of every operation, and the adapter counts the answers imbu signed, one signature each, for the signer census. On imbu `8512c1d` a credential costs two signatures beyond those answers: the `ixn` that anchors its registry's inception and its issuance, and the grant.

## The bulk issuer (`imbu.bulk_issuer`)

The SEDI proofed bulk ceremony (`this.i` 4f987y88). One request is one ceremony, and its events name the ceremony and carry `credentials`, the 2 (copies + 1) credentials it issued: the management Core and Residence pair and a pair per copy.

| Milestone | Method, clock | What counts |
|---|---|---|
| `accepted` | observed, harness | imbu's 202 answer to the ceremony. |
| `anchored`, `witnessed`, `published` | reported, realtime | imbu's stamps on the ceremony's operation, as for the plain issuer. |

- **The citizens process** (`keri_bench.stacks.citizens`) holds the proofing agent and `clients` SMAIDs, each a controller of every home. Per ceremony it signs an IAR and anchors the IAR's nonce in the citizen's KEL, and it signs each of the citizen's requests and polls. That work sits inside every interval, as a citizen's wallet's would.
- **The pool** of registries each home reserves from is provisioned before the window, `pool` registries per home, so registry inception is apart from issuance.
- **The Utah agent credential** every Core names as its far node is issued at the home's first start by a second facet, `utah-root`, in imbu's own lockbox, so imbu reads that registry locally rather than through a far Observer.
- **Copy holders** are fresh keys' AIDs. Nothing is delivered, and the bulk issuer does not revoke.

## Known limits

- **Registrar to Observer.** The Registrar's batches go to the callback address the Observer registers itself, so that one link bypasses the fault proxy.
- **Revocation is recognised through the holder.** Every registry is blinded, and a presentation's disclosures read the chain only as of that presentation, so a verifier cannot read a later revocation from the Observer alone (heti `this.i`, the `AdmitResult.blinders` node under `@0ky6uwqz`). The verifier therefore polls by asking the holder to present again, and `revocation_published`→`revocation_recognised` includes that round trip. It measures when a verifier would refuse the credential at its next presentation, which is what SEDI's verifiers can do today. An Observer-side blinder feed, the unlanded half of heti tick `7vhf`, would let a verifier check status without the holder, and this binding should move to it when it lands.
- **Signer census.** It reads `not measured` until heti's external-signer interface exists (plan item heti-signer).

## The calibration stack

`synthetic.*` adapters drive four small processes whose delays are injected and logged as truth (`this.i` 6he49n0k). The issuer reports on a stack clock deliberately skewed from wall time, so the offset path is exercised. State distribution's `published` is observed at the proxy, so that path is exercised too.
