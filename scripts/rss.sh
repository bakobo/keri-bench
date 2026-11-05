#!/bin/bash
# Sample the resident memory of every process in a run's systemd user unit, and stop the unit if
# any one process passes a limit or the root disk runs low (this.i 7wvv745c: the host is shared).
#
#   scripts/rss.sh <unit> <log> [interval-s] [limit-kb] [min-free-kb]
#
# Every <interval-s> (default 300) it appends one line per process in the unit's cgroup:
#
#   <UTC time> <pid> <rss kB> <name> <port>
#
# where <name> is the process's Python module (`-m`), or its command's basename, and <port> is
# its `--port` argument, or `-`, so the three witnesses can be told apart. When any process's
# resident memory exceeds <limit-kb> (default 6,000,000, about 6 GB), it logs a STOP line and
# stops the unit, which ends the run with whatever it measured so far. It does the same when the
# root filesystem has less than <min-free-kb> available (default 8,000,000, about 8 GB), checked
# every 60 s whatever the interval, because other sessions share that disk. It exits when the
# unit is no longer active. Run it beside the run's unit, under its own unit:
#
#   systemd-run --user --unit=keri-bench-rss --nice=19 scripts/rss.sh keri-bench-stock rss.log
set -uo pipefail
# No `set -e`: this is a guard, and a failed log write or a failed probe must never end it while
# the run it guards goes on. Each stop stops the unit first and logs after, and a disk that cannot
# be read stops the run rather than leaving it unguarded (fail closed).

usage="usage: scripts/rss.sh <unit> <log> [interval-s] [limit-kb] [min-free-kb]"
unit=${1:?$usage}
log=${2:?$usage}
interval=${3:-300}
limit=${4:-6000000}
min_free=${5:-8000000}

stop() {
    # Stop the unit, then record why; the record is best effort, the stop is not.
    systemctl --user stop "$unit"
    echo "$(date -u +%FT%TZ) STOP $1; stopped $unit" >> "$log" 2>/dev/null
    exit 0
}

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
        echo "$now $pid $rss $what" >> "$log" 2>/dev/null
        [ "$rss" -gt "$limit" ] && over="$pid ($what) at $rss kB"
    done
    [ -n "$over" ] && stop "$over is past the $limit kB limit"
    waited=0
    while [ "$waited" -lt "$interval" ] && systemctl --user is-active --quiet "$unit"; do
        free=$(df --output=avail / 2>/dev/null | tail -1 | tr -d ' ')
        case "$free" in
            ''|*[!0-9]*) stop "the root disk's free space could not be read ('$free')" ;;
        esac
        [ "$free" -lt "$min_free" ] && stop "the root disk has $free kB free, under $min_free"
        step=$(( interval - waited < 60 ? interval - waited : 60 ))
        sleep "$step"
        waited=$(( waited + step ))
    done
done
