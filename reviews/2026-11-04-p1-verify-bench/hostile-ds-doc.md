status: closed. Hostile pass by DeepSeek (panel -m ds, finish_reason stop) on docs/verify-2026-11.md against its data, and the commits after 250e2b2. Fixed in 3e334f0: 1, 2, 3, 4, 6, 8. Declined: 5 (the two sessions' pauses are in their session-coord messages to this one, not in the results), 7 (results/baseline-2026-10/microbench.json is in the repository; the reviewer was not given it), 9 (the refused calibration stayed in the work directory, which is never committed; the doc says it is not reported).

I find several concrete errors, unsupported claims, and one likely reproducibility defect.

**1. Reproducibility defect in the scenario diff**
> `scenarios/verify/verify-stages-1home.toml` … `repetitions = 1` → `repetitions = 3`

> “`scenarios/verify/verify-stages-1home.toml` … 120 credentials verified. 259 attempts were made”

Why wrong: at `0.5` credentials/s for `240` s, one repetition produces `120` credentials. With `repetitions = 3`, the run should produce about `360` presentations, not `120`. The supplied stages summary reports only `120` accepted and `259` attempts, consistent with one repetition. The doc does not explain that only one repetition was used or that the script overrides repetitions.  
Severity: **Critical** — the reported run does not match the committed scenario.

**2. Arithmetic error in attempt/warm-verification count**
> “259 attempts were made: 139 were refused … those are left out, as is the first presentation … That leaves 120 warm verifications”

Why wrong: `259 - 139 = 120` accepted. If the first presentation is also left out, the remaining warm count is `119`, not `120`. If the first presentation is among the `139` refused, then the text saying it is additionally left out is redundant/contradictory.  
Severity: **High** — the summary’s own arithmetic changes the sample size.

**3. “2,000 timed verifications per row” is false for the mdoc CLI row**
> “`results/verify-2026-11/derivatives.json`, 2,000 timed verifications per row after 200 untimed”

Why wrong: in `derivatives.json`, the row `"mdoc (process per verification)"` has `"n": 30`, not `2000`. Its `p99_ms` is `887.33684`, which equals `max_ms`, so that “p99” is really the maximum of 30 runs and should not be presented with the same confidence as the 2,000-run rows.  
Severity: **Medium** — the statistical basis for one table row is materially weaker than claimed.

**4. Load-average claim contradicted/unsupported by supplied host log**
> “The load average, including the stack’s own processes, fell from 4.3 to 3.6 over the window (`loadavg.log`).”

Why wrong/unsupported: `host.log` begins with `4.26 5.59 4.66` and ends with `3.97 4.21 4.31`. The “fell … to 3.6” value is not present in the supplied log, and `loadavg.log` is not included in the provided data.  
Severity: **Medium**

**5. “Two other sessions paused their benchmarks and test suites for it” is unsupported**
> “Two other sessions paused their benchmarks and test suites for it.”

Why unsupported: `host.log` shows processes running, but nothing demonstrates that the other sessions paused their benchmarks/test suites. This is an environmental claim that cannot be checked from the supplied artifacts.  
Severity: **Low**

**6. Absolute comparison to the papers’ Node figures is unsupported and likely wrong**
> “The absolute numbers are four to nine times the papers’ Node figures (0.27 to 0.53 ms)”

Why wrong/unsupported: the introduction gives only the spread as “about 0.27 ms per exchange”; it does not establish an absolute range of `0.27` to `0.53` ms. The ratios in the current data do not consistently fall in “four to nine times”: e.g. `1.20 / 0.27 = 4.4`, but `3.74 / 0.53 = 7.1`, and `3.74 / 0.27 = 13.9`. The comparison also mixes measuring scopes.  
Severity: **Medium**

**7. Referenced microbenchmark not available**
> “against 0.09 ms for one bare `Verfer.verify` in keri-bench’s October microbenchmark on this host (`results/baseline-2026-10/microbench.json`)”

Why unsupported: that file is not part of the supplied data, so the `0.09` ms baseline cannot be verified.  
Severity: **Low**

**8. “2.4 to 3.1 times SD-JWT VC” overstates the lower end**
> “Data Integrity is the slowest, at 2.4 to 3.1 times SD-JWT VC here”

Why wrong: using the supplied p50 values, the minimum ratio is about `3.637925 / 1.591829 = 2.29`, or `3.637925 / 1.20444 = 3.02`. The correct range is about `2.3` to `3.1`, not `2.4` to `3.1`.  
Severity: **Low**

**9. Calibration failure claim is unsupported**
> “The first attempt’s calibration was refused by 0.1 ms while another session’s parallel tests ran, and it is not reported.”

Why unsupported: the supplied `host.log`, scenario, and summaries do not contain calibration details.  
Severity: **Low**

**Verdict:** **Reject major revision.** The central KERI-stage breakdown may still be valid, but the report as supplied has a critical reproducibility mismatch, an arithmetic error in the sample count, and several statistical/environmental claims that are either contradicted by the artifacts or unsupported by them.
