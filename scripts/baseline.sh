#!/bin/bash
# Run the October 2026 baseline of today's imbu (docs/baseline-2026-10.md) and collect it.
#
#   scripts/baseline.sh <work-dir> <results-dir> [scenario ...]
#
# With no scenarios it runs every file in scenarios/baseline/ in the order the doc reports them.
# Each scenario is swept (every load step and repetition, or one run for a soak, which the
# scenario's name says) into <work-dir>, and then only what a number rests on is copied into
# <results-dir>: each run's timeline.jsonl, provenance.json, report.json and report.md, every
# sweep.json, and a SHA256SUMS over them. The run directories also hold lockboxes, witness
# keystores and logs, which stay in <work-dir> and are never committed.
#
# Run it under a named user unit, so a closed terminal or the low-memory reaper cannot kill it,
# with the open-file limit the stack needs and at the lowest priority on a shared box:
#
#   systemd-run --user --unit=keri-bench-baseline -p LimitNOFILE=524288 --nice=19 \
#       -E PATH="$PATH" -E HOME="$HOME" --working-directory="$PWD" \
#       scripts/baseline.sh runs/baseline results/baseline-2026-10
set -euo pipefail

work=${1:?usage: scripts/baseline.sh <work-dir> <results-dir> [scenario ...]}
results=${2:?usage: scripts/baseline.sh <work-dir> <results-dir> [scenario ...]}
shift 2
repo=$(cd "$(dirname "$0")/.." && pwd)
if [ $# -eq 0 ]; then
    set -- "$repo"/scenarios/baseline/*.toml
fi
mkdir -p "$work" "$results"

uv run --project "$repo" python -m keri_bench.stacks.microbench --out "$work/microbench.json"
uv run --project "$repo" keri-bench calibrate --out "$work/calibration"

for scenario in "$@"; do
    name=$(basename "$scenario" .toml)
    echo "$(date -u +%FT%TZ) start $name"
    case "$name" in
        *soak*)
            # A soak is one long run at its one rate; its drift comes from its buckets.
            rate=$(uv run --project "$repo" python -c "import sys, tomllib; \
print(tomllib.load(open(sys.argv[1], 'rb'))['load']['rates'][0])" "$scenario")
            uv run --project "$repo" keri-bench run "$scenario" --out "$work/$name" \
                --rate "$rate" --calibration "$work/calibration/calibration.json" || true ;;
        *)
            # Up to six runs per step until three are accepted: a run the driver check refuses
            # on a shared host says nothing about the stack (this.i 2hn3mn3j).
            uv run --project "$repo" keri-bench run "$scenario" --out "$work/$name" \
                --attempts 6 --calibration "$work/calibration/calibration.json" || true ;;
    esac
    echo "$(date -u +%FT%TZ) end $name"
done

cp "$work/microbench.json" "$results/"
mkdir -p "$results/calibration"
cp "$work/calibration/calibration.json" "$results/calibration/"
(cd "$work" && find . -path ./calibration -prune -o \( -name timeline.jsonl -o \
    -name provenance.json -o -name report.json -o -name report.md -o -name sweep.json \) \
    -print) | while read -r file; do
    mkdir -p "$results/$(dirname "$file")"
    cp "$work/$file" "$results/$file"
done
(cd "$results" && find . -type f ! -name SHA256SUMS -print0 | sort -z \
    | xargs -0 sha256sum > SHA256SUMS)
