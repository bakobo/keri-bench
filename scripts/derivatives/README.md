# The derivative formats' verification cost

`bench.py` times verifying an SD-JWT VC, a W3C VCDM 2.0 credential secured with eddsa-rdfc-2022, and an ISO 18013-5 mdoc on this host, so they can be set beside the timed KERI verifier's stages (`this.i` 3jeks5st and its derivative child; the reading is in `docs/verify-2026-11.md`). It is not part of keri-bench's package and has no tests of its own: it runs in arf-interop's environment, because arf-interop and keri-bench pin different hetis and cannot share one, and keri-bench's own stack-agnostic dependencies should not grow a Bakobo derivative minter for one comparison row.

You need a checkout of [bakobo/arf-interop](https://github.com/bakobo/arf-interop) (the results in this repository used its `vc-harden` branch), and for the mdoc rows a JDK 25 and an installed build of [bakobo/mdoc-interop](https://github.com/bakobo/mdoc-interop):

```
git -C ../arf-interop fetch && git -C ../arf-interop worktree add .worktrees/bench origin/vc-harden
(cd ../arf-interop/.worktrees/bench && uv sync)
(cd ../mdoc-interop && ./gradlew installDist)
uv run --project ../arf-interop/.worktrees/bench python -I scripts/derivatives/bench.py \
    --out derivatives.json --mdoc-install ../mdoc-interop/build/install/mdoc-verify
```

`scripts/verify.sh` runs it the same way after the KERI scenario, when given those two paths. Without `--mdoc-install` there are no mdoc rows.

What each row times, all of it after a warmup, one verification at a time, with the network out of the path (trust anchors and keys held, no status list or DID document fetched), which is the papers' scope (Fujie and Suzuki, arXiv:2611.03493):

- `sd-jwt-vc+kb`: arf-interop's `verify_eaa` with `require_binding`, an audience and a nonce, on a credential of 20 selectively disclosable attributes presented with 5, under a two-certificate P-256 chain. That is the chain, the issuer's signature, the time window, five disclosure digests and the holder's key-binding JWT.
- `sd-jwt-vc`: the same presentation without a key-binding JWT.
- `w3c-vc-rdfc-20` and `w3c-vc-rdfc-5`: parse, look up the verification method in a held allowlist before any canonicalization, canonicalize the proof options and the document through arf-interop's own pinned-context loader (`w3c._canonical`, which refuses to fetch), hash both, and check the Ed25519 signature. arf-interop mints these and does not verify them, so this is what a Data Integrity verifier would have to do. eddsa-rdfc-2022 cannot derive a subset, so the 5-attribute row is a re-signed subset, as in the papers. Each row also carries one canonicalization on its own.
- `mdoc (process per verification)`: mdoc-interop's `mdoc-verify` CLI on a DeviceResponse disclosing 5 of 20 elements under an IACA and a document signer minted by the script, with no device authentication. One process per verification is how the CLI is meant to be used (mdoc-interop `this.i` @ue3o4e), so this includes the JVM's start.
- `mdoc (warm JVM)`: the same arguments through the CLI's own entry point, `Cli.run`, called repeatedly in one JVM by `MdocBench.java`. It still reads the two files and renders the evidence record each time, which the CLI does.

The script refuses to time an mdoc that `mdoc-verify` does not verify, and records which of its checks ran in `mdoc_checks`.
