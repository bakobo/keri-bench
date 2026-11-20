#!/bin/bash
# The argon2 rerun (this.i 06klkn9m): probe-capacity-1home at the new pin (imbu 7b544ac, heti
# 093acb3) and at the old one (imbu 317ae49, heti 12e6f41), back to back on one quiet host,
# each at the scenario's rates plus 3.5 and 4.0 in case the ceiling moved past 2.8.
# Holds the shared bench-host lock throughout, and starts only once the 1-minute load is < 2.
# Run under systemd-run as scripts/targets.sh's header says.
set -uo pipefail
base=/home/daniel/code/bakobo/keri-bench/.worktrees
runs=/home/daniel/code/bakobo/keri-bench/.ignored/runs-argon2
mkdir -p "$runs"
exec 9>"$HOME/.local/state/bench-host.lock"
echo "$(date -u +%FT%TZ) waiting for the bench-host lock"
flock 9
echo "$(date -u +%FT%TZ) lock held; waiting for 1-minute load < 2"
while :; do
    l=$(cut -d' ' -f1 /proc/loadavg)
    awk -v l="$l" 'BEGIN{exit !(l < 2.0)}' && break
    echo "$(date -u +%FT%TZ) load $l"
    sleep 60
done
echo "$(date -u +%FT%TZ) quiet at load $(cut -d' ' -f1 /proc/loadavg)"
for side in argon2:after before:before; do
    tree=$base/rerun-${side%%:*}
    name=${side##*:}
    cd "$tree" || exit 3
    work=$runs/$name
    # A failed calibration means the host is too busy (this.i 0hykugwr): stop, never retry.
    scripts/targets.sh "$work" "$runs/results-$name" scenarios/targets/probe-capacity-1home.toml \
        || { echo "$(date -u +%FT%TZ) targets.sh failed for $name; stopping"; exit 4; }
    for rate in 3.5 4.0; do
        uv run --project "$tree" keri-bench run scenarios/targets/probe-capacity-1home.toml \
            --out "$work/probe-capacity-1home" --rate "$rate" \
            --calibration "$work/calibration/calibration.json" || true
    done
    echo "$(date -u +%FT%TZ) done $name"
done
echo "$(date -u +%FT%TZ) all done"
