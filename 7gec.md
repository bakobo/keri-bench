# Record an unexpected child exit in a run: a witness died at 05:07 during witness-kel-growth-2's 40k fill (their tracer, not a defect), and the harness wrote nothing, so a run could report numbers from a stack missing a role. Make the stack supervisor log the exit (pid, role, code, time) into the run's timeline and provenance, and fail the run or mark it degraded. Source: imbu-prj, ~/code/bakobo/heti/.ignored/witness-kel-growth-2-findings.md (2), 2026-11-03.
kind: todo
created: 2026-11-03T13:37Z
closed: 2026-11-03T21:13Z

