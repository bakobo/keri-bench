#!/bin/bash
# Sample the resident memory of every process in a run's systemd user unit, and stop the unit if
# any one process passes a limit (this.i 7wvv745c: the host is shared).
#
#   scripts/rss.sh <unit> <log> [interval-s] [limit-kb]
#
# Every <interval-s> (default 300) it appends one line per process in the unit's cgroup:
#
#   <UTC time> <pid> <rss kB> <name> <port>
#
# where <name> is the process's Python module (`-m`), or its command's basename, and <port> is
# its `--port` argument, or `-`, so the three witnesses can be told apart. When any process's
# resident memory exceeds <limit-kb> (default 6,000,000, about 6 GB), it logs a STOP line and
# stops the unit, which ends the run with whatever it measured so far. It exits when the unit is
# no longer active. Run it beside the run's unit, under its own unit:
#
#   systemd-run --user --unit=keri-bench-rss --nice=19 scripts/rss.sh keri-bench-stock rss.log
set -euo pipefail

unit=${1:?usage: scripts/rss.sh <unit> <log> [interval-s] [limit-kb]}
log=${2:?usage: scripts/rss.sh <unit> <log> [interval-s] [limit-kb]}
interval=${3:-300}
limit=${4:-6000000}

describe() {
    # The module after -m, else the command's basename; and the value after --port, else -.
    local pid=$1 name="" port="-" prev="" arg
    while IFS= read -r -d '' arg; do
        [ -z "$name" ] && name=$(basename "$arg")
        [ "$prev" = "-m" ] && name=$arg
        [ "$prev" = "--port" ] && port=$arg
        prev=$arg
    done < "/proc/$pid/cmdline"
    echo "${name:-?} $port"
}

while systemctl --user is-active --quiet "$unit"; do
    group=$(systemctl --user show -p ControlGroup --value "$unit")
    now=$(date -u +%FT%TZ)
    over=""
    for pid in $(cat "/sys/fs/cgroup$group/cgroup.procs" 2>/dev/null; \
                 find "/sys/fs/cgroup$group" -mindepth 2 -name cgroup.procs -exec cat {} + \
                 2>/dev/null); do
        rss=$(awk '/^VmRSS:/ {print $2}' "/proc/$pid/status" 2>/dev/null) || continue
        [ -n "$rss" ] || continue
        what=$(describe "$pid" 2>/dev/null) || continue
        echo "$now $pid $rss $what" >> "$log"
        [ "$rss" -gt "$limit" ] && over="$pid ($what) at $rss kB"
    done
    if [ -n "$over" ]; then
        echo "$now STOP $over is past the $limit kB limit; stopping $unit" >> "$log"
        systemctl --user stop "$unit"
        exit 0
    fi
    sleep "$interval"
done
