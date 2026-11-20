# When imbu-stamps lands imbu's constructed and registry_updated stamps on utah/next: bind both milestones in the imbu issuer adapter (docs/adapters.md marks them unobserved), then rerun one saturation step at one issuer to split accepted->anchored; brief at ~/code/bakobo/imbu/.ignored/briefs/2026-11-19-issuance-stage-stamps.md
kind: todo
created: 2026-11-19T16:50Z
closed: 2026-11-20T05:24Z

- 2026-11-20T04:26Z 2026-11-20: the bench rerun on imbu 317ae49 (worktree .worktrees/stamps-run, scripts/targets.sh runs/stamps ... probe-capacity-1home) never got a valid calibration on kelun: two strict failures under other sessions' load (a criterion bench and its rustc builds in tessera/keri-oxidized), and an 8-hour wait for a quiet host that a too-broad pgrep pattern held closed. imbu-stamps' CPU profile (imbu .ignored/issuance-cpu-profile-2026-11.md) already attributes 0.49 of 0.575 s of issuer CPU per credential to argon2.
- 2026-11-20T05:24Z Closed 2026-11-20: the binding landed in #10 (1d59ce2); the saturation rerun was dropped on Daniel's choice (Q-TPGR, a) as redundant with imbu-stamps' CPU profile, which attributes 0.49 of 0.575 s of issuer CPU per credential to argon2.
