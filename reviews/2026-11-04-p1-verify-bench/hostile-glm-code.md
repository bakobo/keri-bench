status: closed. Hostile pass by GLM (panel -m glm, finish_reason stop) on the diff origin/vc-harden...29d991c+d187d3e. Dispositions are below the review, and the fixes are 250e2b2.

## Findings

**1. `src/keri_bench/stacks/stages.py:100-111` (`span`) — sibling-task spans are silently dropped, not "outermost wins".**
`attempt._depth` lives on the shared `Attempt` object, not per task. If heti ever runs two wrapped stage functions *concurrently* under one attempt (via `gather` — and `_observe` already gathers witnesses), the second task sees `_depth` nonzero, records **no time and no call** for its stage, and its wall-clock is absorbed into whatever stage the first task opened. `calls_per_attempt` and the per-stage p50s in the summary are then wrong, and the docstring's "per stage the time and the number of calls" is not upheld structurally — it holds only because the pinned heti happens to call these sequentially. The "stages never sum to more than total" invariant does hold (charged spans are serialized), so this is misattribution, not double counting. **Severity: Medium.**

**2. `stages.py:64-71` (`Attempt.active`) + `docs/adapters.md:39` — "whether another verification ran at the same time" is the only confound the flag can see.**
`_running` tracks attempts only. The verifier process also runs heti/keripy background work on the same loop (witness receipting, KEL replay, mailboxes for its own AID); whenever that work gets the loop while a stage span is open on an await, its CPU time is charged to that stage and to `total`. `overlapped` cannot detect it, the summary cannot exclude it, and adapters.md presents the flag as the whole concurrency story. The "warm, run alone" rows are warm only with respect to other `/verify` attempts. **Severity: Medium (measurement bias, doc overstates).**

**3. `src/keri_bench/stacks/adapters.py:338-343` (`HetiTimedVerifier.connect`) — the subclass re-lists the spawn arguments wholesale.**
The visible spawn forwards only `--observer-url`, `--observer-aid`, `--freshness`, `--stages`. The scenario (`scenarios/verify/verify-stages-1home.toml:60-64`) sets verifier knobs `toad = 2`, `retry_ms`, `deadline_s`; none reach this list. `retry_ms`/`deadline_s` may be adapter-side, but if the base `HetiVerifier.connect` forwards anything else to the process (e.g. `--toad` for `party.open_party`'s witness policy), the timed verifier runs with different witness acceptance than `heti.verifier` while adapters.md claims "the same verifier … the same knobs and milestones." Even if the base today matches, the copy-pasted list is a divergence trap: the next base-class flag silently never reaches `--stages` runs. This must be diffed against the base `connect` before merge. **Severity: Medium (pending that check).**

**4. `scripts/verify.sh:12-13 vs 57-62` — `report.json`/`report.md` are promised and collected but never produced.**
Nothing in the script invokes `keri-bench report` (the README documents it as a separate subcommand). If `keri-bench run` does not also emit them, the `find` silently skips them, the header's "what the numbers rest on" list is false, and the results directory ships without the reconciliation — with no error, since `find` matching nothing is not a failure. Either run `keri-bench report "$run"` or delete the claim. **Severity: Medium-low.**

**5. `scripts/verify.sh:46-50` — a derivatives failure throws away the already-finished KERI results.**
Under `set -e`, if the arf bench crashes (missing JDK, `cbor2` absent — see #9), the script exits before the copy block at 55-66 and before SHA256SUMS: six minutes of scenario output exists only in `$work`, and `$results` is empty or stale. A collection script should collect what succeeded; the derivatives half should be non-fatal (or run last, after the KERI copy). Same class: a `verifystages` `E_EMPTY` refusal aborts before anything is copied. **Severity: Medium-low.**

**6. `src/keri_bench/stacks/verifystages.py:48-61` (`_parsed`) — the format contract is not upheld.**
Validation only checks the line parses and has `stages` and `total_ns`. A JSON object missing `outcome`, `calls`, `phases`, `overlapped`, or individual stage keys passes `_parsed` and then dies with a raw `KeyError`/`TypeError` deep inside `summarise`, not `E_FORMAT` "refused by line number" as the module's own docstring and test promise. `test_a_line_that_is_not_a_record_is_refused_by_line_number` only covers the non-JSON case. **Severity: Low.**

**7. `scripts/verify.sh:18-20` vs `scripts/derivatives/README.md:12-14` — the script's own usage example contradicts its sibling README.**
The header tells the user to pass `../arf-interop` (the base checkout), while derivatives/README.md requires the `origin/vc-harden` worktree — following the script header times the wrong branch, and since `uv run --project` syncs, it mutates whichever checkout it is given. **Severity: Low-medium.**

**8. `scripts/derivatives/bench.py:283-295` (`mdoc`) — the process-per-verification row is statistically not comparable to the others.**
It gets exactly one warmup process (the validation run) and n=30 samples against n=2000 elsewhere; its `p99_ms` is effectively near-max noise. The JSON records `n`, but the README presents the rows side by side without saying the mdoc process p99 is meaningless at that n. **Severity: Low.**

**9. `scripts/derivatives/bench.py:330-336` (`main`) — the version probe assumes dependencies arf-interop may not have.**
`version(name)` for `pyld`, `joserfc`, `cryptography`, `cbor2` raises `PackageNotFoundError` if any is absent from arf-interop's env, and `import cbor2` in `mdoc_fixture` needs it too; the derivatives README's bare `uv sync` gives no guarantee, and the crash happens before any timing, killing the whole script (and via #5, the collection). **Severity: Low.**

**10. `src/keri_bench/stacks/verifier.py:119-127` (`timed_verify`) — "accepted" is inferred from HTTP status, not the answer.**
`outcome="accepted" if status == 200` trusts that `verify` never answers 200 with `valid: False`; the reconciliation counts `verified`, the stage log counts `status==200`. If the two notions ever diverge (a 200/`valid:False` verdict path), `verifystages`' "accepted" and the report's "verified" silently disagree. Use `answer.get("valid")`. **Severity: Low.**

**11. `stages.py:87-92` (`Attempt.record`) + `verifystages.py:92-94` — the record carries no holder AID.**
`verifystages`' docstring promises "the first presentation from each holder, reported apart," but the record keys nothing by holder and `meetings` is keyed by *credential name*. With one holder per run that is guessable; with two it is indistinguishable and unverifiable. One field would fix it. **Severity: Low.**

**12. `scripts/derivatives/bench.py:31-26 docstring vs 160-178` — "the verification a Data Integrity verifier would have to do" overstates the W3C row.**
The code verifies the Ed25519 signature over the two canonicalization hashes (spec-shaped for eddsa-rdfc-2022, and the README's step list is accurate), but it performs no cryptosuite dispatch, no `proofPurpose` check, and no `created`/`validFrom`/`validUntil` check — work every real DI verifier does. The row therefore under-times a real verifier, slightly, while the docstring claims completeness. Say "the cryptographic core of". **Severity: Low.**

**13. `scripts/verify.sh:26-29` (`busiest`) — `ps | head` under `set -euo pipefail`.**
If `ps` takes SIGPIPE (large process table), the function returns 141 and `set -e` kills the entire run — the very monitoring that is supposed to prove "nothing else heavy was running." Use `ps ... | head -8 || true` semantics inside the helper. **Severity: Low.**

**14. `README.md:39` and `docs/adapters.md:38` link `docs/verify-2026-11.md`, which this diff does not add.**
If merged as-is, the flagship reading document is a dangling link. **Severity: Low.**

**15. Comparability of the two artifacts being "set beside" each other.**
`bench.py:66-70` uses nearest-rank percentiles; `verifystages._row` uses `Histogram.percentile` ("the definition keri-bench's own percentiles approximate") — two different p99 definitions land in one comparison table. More importantly, the KERI stages include localhost witness-gather and Observer network round-trips inside `key_state`/`tel.fetch`, while the derivative rows exclude all network by construction. Both facts are documented in code comments but belong in the comparison doc's caveats, or the KERI rows will be read as pure CPU cost. **Severity: Low (documentation).**

**16. `stages.py:139-142` (`_observed`) and `stages.py:160-172` (`TimedSource`) — two latent repin hazards the install() guard does not cover.**
(a) Stage labelling is `aid == grant.i`: an unreadable grant (`holder=None`) dumps the holder's observation into `key_state.issuer`, and a self-issued credential (holder == issuer) collapses both key-state stages into `key_state.holder`, silently zeroing the issuer rollup. (b) `TimedSource` proxies by `__getattr__`; if a future heti type-checks the source or calls a dunder on it, the proxy breaks with no `e.feature.unsupported` refusal — unlike every other repin hazard here, which `install()` names loudly. **Severity: Low.**

## What checked out

For the record, the things the prompt invited me to attack that I could **not** break: the W3C row *does* verify the signature (sign/verify are symmetric, `proofValue` excluded from the options hash, multibase decode handles leading-zero bytes); the mdoc fixture is self-consistent and the script genuinely refuses to time anything `mdoc-verify` does not exit `EXIT_VERIFIED` on (all timed runs are checked, warm-JVM included); the outermost-span `_depth` scheme does structurally guarantee the stages-sum-≤-total invariant the test asserts; `install()` refuses before wrapping anything if any heti name is missing; and `Attempt`/`ContextVar` per-task attribution is correct under `gather` across *different* attempts (the `test_concurrent_tasks` case).

**Verdict: request changes — the timing core is sound, but the collection script loses all results on a half-failure and never produces the reports it promises, the adapter subclass must be proven flag-identical to the base spawn, and several documented claims (per-holder meetings, "run alone", "what a DI verifier would have to do") outrun what the code records.**

## Dispositions

GLM hostile pass, dispositions:
1 fixed (per-task span flag). 2 doc caveat in docs/verify-2026-11.md. 3 fixed (extra args). 4 declined: keri-bench run writes report.json and report.md in every run directory. 5 fixed. 6 fixed. 7 fixed. 8 doc note + 3 untimed runs. 9 declined: pyld, joserfc, cryptography and cbor2 are all arf-interop runtime dependencies. 10 fixed. 11 fixed. 12 fixed (cheap checks first, wording). 13 fixed. 14 doc added in this PR. 15 doc caveats. 16a doc note (one holder, not self-issued, in this scenario); 16b declined: heti reads the source only through read() at 3a8b1ccb, and the real-stack test proves the proxy works.
