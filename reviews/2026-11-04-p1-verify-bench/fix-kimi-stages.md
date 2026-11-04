status: closed. Fix-diff review by kimi (panel -m kimi, finish_reason stop) before pushing the fix to #2's hostile pass.
Both findings fixed in 40e3925.

1. **High — `src/keri_bench/stacks/stages.py:119-129`, `span()` — an inherited open span still swallows a concurrent child stage.**  
   The `outer.closed` check happens only once, when the child enters `span()`. Consider this interleaving:
   - A parent opens a charged span and creates a child task.
   - The child inherits that span and enters `span("tel.fetch")` while it is still open, so line 120 takes the bare `yield`: no charging, no call count, and no `_live` increment.
   - The child blocks; the parent closes its span; the child then resumes and finishes inside the attempt.

   The result is `calls["tel.fetch"] == 0`, no `tel.fetch` time, and `overlapped == False`, even though the stage contexts overlapped and part of the child’s work occurred after the parent’s charged interval. The new test avoids this by not letting the child enter its stage until after the parent closes, so it misses the failing case. An inherited span should not suppress nesting in a different task; the child needs its own charged span, with `_live` marking any actual overlap.

2. **Medium — `src/keri_bench/stacks/stages.py:118-135` — a child outliving `Attempt.active()` can mutate a completed attempt without being marked overlapped.**  
   A child task retains its copied `_current` value after the parent exits `attempt.active()`. If it also inherits the now-closed parent span, the new logic deliberately lets it open another charged span. At that point `attempt.total` is already fixed and `_live` is zero, so the child adds stage time and calls with `overlapped` remaining false. If serialization happens afterward, stages can sum beyond `total_ns` while the summary still accepts the attempt; if serialization happens first, the attempt is mutated after its record was emitted. The validator does not detect either lifecycle error. Charging needs an active-attempt/generation check, not merely an inherited `Attempt` reference.

**Verdict: request changes.** The independent-sibling case is handled, but the parent/child semantics remain incorrect, and the new tests specifically steer around both failing lifetimes.
