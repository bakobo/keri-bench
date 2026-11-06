status: closed. Fix-diff review by gemini (panel -m gemini, finish_reason stop) before pushing the fix to #2's hostile pass.
1, 2 and 3 fixed in 40e3925. 4 declined: every span is a `with` block, which exits in the task and in the order it entered, so an out-of-order or cross-task exit cannot happen through this API.

### Hostile Review: `src/keri_bench/stacks/stages.py`

#### 1. Unterminated / Outliving Child Span Mutates `attempt` After `ended` and Causes Stage Sum > Total Without `overlapped`
* **Location:** `src/keri_bench/stacks/stages.py`, lines 101–106 (`Attempt.run` `finally`) and lines 133, 141–154 (`span`)
* **Failure Mode:** 
  Consider the following interleaving:
  1. Parent task enters `Attempt.run()`. No stage span is open in the parent (`attempt._live == 0`).
  2. Parent spawns a child task.
  3. Child task enters `span(S)` **before** `Attempt.run()` exits. At entry, `attempt.ended` is `False`, `attempt._live` becomes `1` (not `> 1`, so `attempt.overlapped` remains `False`). Child suspends at `await` inside `span(S)`.
  4. Parent exits `Attempt.run()` (e.g., on timeout, cancellation, or fire-and-forget task). `self.total` is recorded (e.g., `10ms`), `self.ended = True`, and `_live` is **not** checked in `Attempt.run`'s `finally` block.
  5. Child finishes `span(S)` `50ms` later. The `finally` block in `span` (lines 149–154) does **not** check `attempt.ended`: it unconditionally executes `attempt.stages[stage] += _now() - began` (`50ms`) and `attempt.calls[stage] += 1`.
  
  **Result:**
  * Violation of rule *"spans after the attempt ended are ignored"* (in-flight spans at the moment `Attempt.run` ends still mutate `attempt.stages` and `attempt.calls` after `attempt.ended = True`).
  * Violation of invariant *"stages never sum to more than the attempt's total without `overlapped`"*: `attempt.stages[S]` is `50ms`, `attempt.total` is `10ms`, and `attempt.overlapped` is `False`.
* **Severity:** **High** (Silent data corruption / invariant violation)

---

#### 2. Nested `Attempt.run()` inside an Open `span` Corrupts Outer Span's `_live` and Double-Counts Inner Stages
* **Location:** `src/keri_bench/stacks/stages.py`, lines 131–145 (`span`)
* **Failure Mode:**
  `_current` (the `Attempt`) and `_open` (the `_Span`) are tracked in independent `ContextVar`s, and `_Span` does not record which `Attempt` it belongs to.
  1. Task enters `attempt_1.run()`, then enters `span(A)`. `_open` is `Span_1(task=T)`, `attempt_1._live == 1`.
  2. Inside `span(A)`, the same task enters a nested `attempt_2.run()`, then enters `span(B)`.
  3. In `span(B)`: `attempt` is `attempt_2`, `outer` is `Span_1`. Because `outer.task is _task()` and `not outer.closed`, `span(B)` hits the early-exit branch on line 134 (`outer is not None and not outer.closed and outer.task is _task()`)!
  
  **Result:** `attempt_2`'s stage `B` is **completely swallowed** because the task happens to have an open span belonging to `attempt_1`. (Conversely, if `_Span` isn't scoped to `attempt`, any cross-attempt nesting within a task silently drops all stages for the inner attempt).
* **Severity:** **Medium** (Swallows stages if `Attempt.run()` is ever nested or re-entered in the same task)

---

#### 3. Callback / `contextvars.copy_context()` Reuse Within Same Task or Thread Pool (`_task() is None`) Swallows Concurrent Spans
* **Location:** `src/keri_bench/stacks/stages.py`, lines 58–62 (`_task`) and line 134 (`span`)
* **Failure Mode:**
  * **Case A (`asyncio.to_thread` / `run_in_executor`):** Python 3.9+ `asyncio.to_thread` copies the current `Context` to a worker thread. In the worker thread, there is no running event loop, so `_task()` returns `None`. If the caller was not in an async loop (or if two thread-pool workers are spawned from a synchronous context where `outer.task is None`), both worker threads have `_task() is None`. Since `None is None` evaluates to `True`, `outer.task is _task()` is `True` across completely different threads! The child thread's stage is **swallowed** instead of being charged and marked `overlapped`.
  * **Case B (Greenlets / Coroutine trampoline within the same `asyncio.Task`):** Any concurrency mechanism multiplexing inside a single `asyncio.Task` (or callbacks scheduled via `loop.call_soon` that happen to execute while the task is irrelevant, where `_task()` is `None`) shares `_task() == None`, causing concurrent spans in callback context to see `outer.task is _task()` (`None is None`) and swallow the inner span.
* **Severity:** **Medium** (Silent stage swallowing when `None is None` matches across distinct threads/contexts)

---

#### 4. Out-of-Order Span Exit across Parent/Child Tasks Resets `_open` Token Value or Fails if Unstructured
* **Location:** `src/keri_bench/stacks/stages.py`, line 154 (`_open.reset(token)`)
* **Failure Mode:**
  If an async generator or manual `__enter__`/`__exit__` exits spans out of LIFO order within the same task (or if a span context manager is entered in one task and exited in another), `_open.reset(token)` either raises `ValueError: <Token ...> was created in a different Context` (crash) or resets `_open` to a stale outer span while an inner span is still running, allowing a third nested span to see `outer.closed == True` and **double-count within the same task** without incrementing `_live > 1` if the first span already decremented `_live`.
  Specifically:
  1. Same task enters `Span1` (`_open = Span1`, `_live = 1`), then manually enters `Span2` (yields early, doesn't set `_open`).
  2. Now suppose `Span1` is entered in Parent (`_open = Span1`), Parent spawns Child while `Span1` is open. Child inherits `_open = Span1`.
  3. Parent finishes `Span1` (`Span1.closed = True`, `_live = 0`).
  4. Child (which still has `_open = Span1` in its inherited context!) now enters `SpanA`, and inside `SpanA` spawns Grandchild, then exits `SpanA` (`_open.reset(token)` resets Child's `_open` back to `Span1`, which has `Span1.closed == True`). This part is handled by `not outer.closed`, **unless** `Span1` was somehow still open when `SpanA` exited, which works. However, exiting a span in a different task than it was entered crashes with `ValueError` on `_open.reset(token)`.
* **Severity:** **Low** (Standard `contextlib.contextmanager` usage is LIFO per task, though `Attempt.run` should guard against live spans at exit)

---

### Summary of Required Fixes

1. **In `Attempt.run()` `finally` (line 103):**
   If `self._live > 0` when `Attempt.run` exits, a child task still has an open span outliving the attempt. Either mark `self.overlapped = True` (since a span overlapped the end boundary of the attempt) **and** in `span()`'s `finally` (line 151), do not add to `attempt.stages[stage]` / `attempt.calls[stage]` if `attempt.ended` is `True`.
2. **In `_Span.__init__` (line 53) and `span` (line 134):**
   Record `self.attempt = attempt` on `_Span` and check `outer.attempt is attempt` so nested `Attempt`s don't swallow spans.
3. **In `_task()` (line 58):**
   Fallback to `(threading.get_ident(), None)` or `threading.current_thread()` when `asyncio.current_task()` raises `RuntimeError` so `None is None` doesn't conflate distinct threads.

### Verdict
**FAIL (Request Changes)** — Interleaving #1 allows a child task span started before `Attempt.run()` exits and finishing after `Attempt.run()` exits to mutate `attempt.stages` after `attempt.ended = True` and push `sum(attempt.stages.values()) > attempt.total` with `attempt.overlapped == False`.
