#!/bin/bash
# Run scenarios against Utah's stated targets (docs/targets-2026-10.md) and collect them.
#
#   scripts/targets.sh <work-dir> <results-dir> <scenario> [scenario ...]
#
# As scripts/baseline.sh, with three differences. Scenarios are named one by one, because the
# targets run is several sittings on a shared host rather than one matrix. A soak, a peak-hour or
# office-day run, or a probe is run once at each of its rates (a soak's spread comes from its
# buckets, a probe's from nothing, which is why it is a probe); anything else is swept with up
# to six attempts per step until three are accepted (this.i 2hn3mn3j). And the host's load
# average is logged every 60 s beside the runs, so a number can be read against what else the
# host was doing during it; each run's provenance carries it at its start, window and end too.
#
# Run it under a named user unit, with the open-file limit the stack needs, at nice 19:
#
#   systemd-run --user --unit=keri-bench-targets -p LimitNOFILE=524288 --nice=19 \
#       -E PATH="$PATH" -E HOME="$HOME" --working-directory="$PWD" \
#       scripts/targets.sh runs/targets results/targets-2026-10 scenarios/targets/<name>.toml
set -euo pipefail

work=${1:?usage: scripts/targets.sh <work-dir> <results-dir> <scenario> [scenario ...]}
results=${2:?usage: scripts/targets.sh <work-dir> <results-dir> <scenario> [scenario ...]}
shift 2
[ $# -gt 0 ] || { echo "name at least one scenario" >&2; exit 2; }
repo=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$work" "$results"

(while true; do echo "$(date -u +%FT%TZ) $(cut -d' ' -f1-3 /proc/loadavg)"; sleep 60; done) \
    >> "$work/loadavg.log" &
logger=$!
trap 'kill $logger 2>/dev/null || true' EXIT

if [ ! -f "$work/calibration/calibration.json" ]; then
    uv run --project "$repo" keri-bench calibrate --out "$work/calibration"
fi

for scenario in "$@"; do
    name=$(basename "$scenario" .toml)
    echo "$(date -u +%FT%TZ) start $name"
    case "$name" in
        probe-*|peak-hour-*|office-day-*|stock-*)
            for rate in $(uv run --project "$repo" python -c "import sys, tomllib; \
print(*tomllib.load(open(sys.argv[1], 'rb'))['load']['rates'])" "$scenario"); do
                uv run --project "$repo" keri-bench run "$scenario" --out "$work/$name" \
                    --rate "$rate" --calibration "$work/calibration/calibration.json" || true
            done ;;
        *)
            uv run --project "$repo" keri-bench run "$scenario" --out "$work/$name" \
                --attempts 6 --calibration "$work/calibration/calibration.json" || true ;;
    esac
    echo "$(date -u +%FT%TZ) end $name"
done

mkdir -p "$results/calibration"
cp "$work/calibration/calibration.json" "$results/calibration/"
cp "$work/loadavg.log" "$results/"
(cd "$work" && find . -path ./calibration -prune -o \( -name timeline.jsonl -o \
    -name provenance.json -o -name report.json -o -name report.md -o -name sweep.json \) \
    -print) | while read -r file; do
    mkdir -p "$results/$(dirname "$file")"
    cp "$work/$file" "$results/$file"
done
(cd "$results" && find . -type f ! -name SHA256SUMS -print0 | sort -z \
    | xargs -0 sha256sum > SHA256SUMS)
