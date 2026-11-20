# A reusable quiet-host driver: take the bench-host flock and refuse on failure, refuse an unreadable load, and report a failed rate. results/argon2-2026-11/run.sh did none of these (hostile pass on #12, findings 1 and 2); it is a record of one run, so it stays as run, and scripts/targets.sh should grow the lock and the quiet gate instead
kind: debt
tags: review-deferred
created: 2026-11-20T20:42Z

