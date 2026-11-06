#!/bin/bash
# Time a KERI verification by stage, and the derivative formats beside it, on this host
# (this.i 3jeks5st; docs/verify-2026-11.md), and collect both.
#
#   scripts/verify.sh <work-dir> <results-dir> [<arf-interop checkout> [<mdoc-verify install>]]
#
# It runs scenarios/verify/verify-stages-1home.toml once at its rate, summarises the timed
# verifier's stages.jsonl into stages.json and stages.md, and, given an arf-interop checkout (and
# mdoc-interop's build/install/mdoc-verify), runs scripts/derivatives/bench.py in that checkout's
# environment (scripts/derivatives/README.md). It copies into <results-dir> what the numbers
# rest on: the run's timeline.jsonl, provenance.json, report.json and report.md, stages.jsonl,
# stages.json, stages.md, derivatives.json, the load average every 60 s, the busiest processes at
# the start and the end, and a SHA256SUMS over them.
#
# Unlike the baseline it is NOT run at nice 19: the numbers are per-call latencies, which a
# deprioritised process inflates by however much else the host is doing. Run it when nothing
# else heavy is running, and read host.log to see that nothing was. Under a named user unit:
#
#   systemd-run --user --unit=keri-bench-verify -p LimitNOFILE=524288 \
#       -E PATH="$PATH" -E HOME="$HOME" --working-directory="$PWD" \
#       scripts/verify.sh runs/verify results/verify-2026-11 ../arf-interop/.worktrees/bench \
#       ../mdoc-interop/build/install/mdoc-verify
#
# The arf-interop path is a checkout of the branch to time, as scripts/derivatives/README.md
# sets one up; uv syncs that checkout's environment, so give it one you do not mind synced. The
# KERI results are collected before the derivatives run, and a failure there leaves them in
# place and says so.
set -euo pipefail

work=${1:?usage: scripts/verify.sh <work-dir> <results-dir> [<arf-interop> [<mdoc-verify>]]}
results=${2:?usage: scripts/verify.sh <work-dir> <results-dir> [<arf-interop> [<mdoc-verify>]]}
arf=${3:-}
mdoc=${4:-}
repo=$(cd "$(dirname "$0")/.." && pwd)
scenario=$repo/scenarios/verify/verify-stages-1home.toml
mkdir -p "$work" "$results"

busiest() {
    echo "$(date -u +%FT%TZ) $1 load $(cut -d' ' -f1-3 /proc/loadavg)"
    ps -eo pid,ni,pcpu,etime,comm --sort=-pcpu | head -8 || true
}
busiest start > "$work/host.log"
(while true; do echo "$(date -u +%FT%TZ) $(cut -d' ' -f1-3 /proc/loadavg)"; sleep 60; done) \
    >> "$work/loadavg.log" &
logger=$!
trap 'kill $logger 2>/dev/null || true' EXIT

if [ ! -f "$work/calibration/calibration.json" ]; then
    uv run --project "$repo" keri-bench calibrate --out "$work/calibration"
fi
rate=$(uv run --project "$repo" python -c "import sys, tomllib; \
print(tomllib.load(open(sys.argv[1], 'rb'))['load']['rates'][0])" "$scenario")
uv run --project "$repo" keri-bench run "$scenario" --out "$work/verify-stages-1home" \
    --rate "$rate" --calibration "$work/calibration/calibration.json"
run=$(find "$work/verify-stages-1home" -name timeline.jsonl -printf '%h\n' | head -1)
uv run --project "$repo" python -m keri_bench.stacks.verifystages \
    "$run/verifier-spool/stages.jsonl" --out "$run"
cp "$run/verifier-spool/stages.jsonl" "$run/stages.jsonl"

mkdir -p "$results/calibration"
cp "$work/calibration/calibration.json" "$results/calibration/"
cp "$work/loadavg.log" "$work/host.log" "$results/"
(cd "$work" && find . -path ./calibration -prune -o \( -name timeline.jsonl -o \
    -name provenance.json -o -name report.json -o -name report.md -o -name stages.jsonl \
    -o -name stages.json -o -name stages.md \) -not -path '*/verifier-spool/*' -print) \
    | while read -r file; do
    mkdir -p "$results/$(dirname "$file")"
    cp "$work/$file" "$results/$file"
done

status=0
if [ -n "$arf" ]; then
    if uv run --project "$arf" python -I "$repo/scripts/derivatives/bench.py" \
            --out "$work/derivatives.json" ${mdoc:+--mdoc-install "$mdoc"}; then
        cp "$work/derivatives.json" "$results/"
    else
        echo "the derivative harness failed; the KERI results are collected without it" >&2
        status=1
    fi
fi
busiest end >> "$work/host.log"
cp "$work/host.log" "$results/"
(cd "$results" && find . -type f ! -name SHA256SUMS -print0 | sort -z \
    | xargs -0 sha256sum > SHA256SUMS)
exit $status
