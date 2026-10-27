"""``keri-bench``: run a scenario, sweep it, calibrate the harness, or rebuild a report."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import growth, provenance, report
from .calibration import calibrate
from .contract import contract
from .errors import BenchError
from .run import run_once, sweep
from .scenario import load_scenario

REPO = Path(__file__).resolve().parents[2]


def _commit() -> str:
    return provenance.harness_commit(REPO)


def _run(args) -> dict:
    scenario = load_scenario(args.scenario)
    if args.rate is None:
        return asyncio.run(sweep(scenario, args.out, harness_commit=_commit(),
                                 calibration=args.calibration, attempts=args.attempts))
    return asyncio.run(run_once(scenario, args.out, rate=args.rate, repetition=args.repetition,
                                harness_commit=_commit(), calibration=args.calibration))


def _calibrate(args) -> dict:
    return asyncio.run(calibrate(args.scenarios, args.out, harness_commit=_commit()))


def _contract(args) -> dict:
    return asyncio.run(contract(load_scenario(args.scenario), args.out,
                                harness_commit=_commit()))


def _report(args) -> dict:
    return report.write_report(args.run_dir, generator_commit=_commit())


def _growth(args) -> dict:
    reports = [json.loads(Path(path).read_text()) for path in args.reports]
    out = growth.growth(reports, at=args.at, bound_s=args.bound_s)
    out["reports"] = [str(path) for path in args.reports]
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="keri-bench", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run a scenario: every load step and repetition, or "
                              "one run with --rate.")
    run.add_argument("scenario", type=Path)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--rate", type=float, help="One run at this offered rate only.")
    run.add_argument("--repetition", type=int, default=1)
    run.add_argument("--attempts", type=int, help="Runs per load step at most, until the "
                     "scenario's repetitions are accepted; by default exactly the repetitions.")
    run.add_argument("--calibration", type=Path, help="A calibration.json whose error budget "
                     "goes into each run's provenance.")
    run.set_defaults(handler=_run)
    cal = commands.add_parser("calibrate", help="Prove the harness measures correctly against "
                              "the synthetic stack.")
    cal.add_argument("--scenarios", type=Path, default=REPO / "scenarios")
    cal.add_argument("--out", type=Path, required=True)
    cal.set_defaults(handler=_calibrate)
    check = commands.add_parser("contract", help="Run the adapter contract suite with the "
                                "adapters a scenario names (Part B #10).")
    check.add_argument("scenario", type=Path)
    check.add_argument("--out", type=Path, required=True)
    check.set_defaults(handler=_contract)
    rebuild = commands.add_parser("report", help="Rebuild a run's report from its timeline and "
                                  "provenance.")
    rebuild.add_argument("run_dir", type=Path)
    rebuild.set_defaults(handler=_report)
    grow = commands.add_parser("growth", help="Fit store bytes and latency against stock over "
                               "soak reports' buckets (this.i 4j5395l8).")
    grow.add_argument("reports", type=Path, nargs="+")
    grow.add_argument("--at", type=float, action="append", default=[],
                      help="A stock to read every curve at; repeatable.")
    grow.add_argument("--bound-s", type=float, help="A latency to find each latency curve's "
                      "crossing of.")
    grow.add_argument("--out", type=Path, required=True)
    grow.set_defaults(handler=_growth)
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except BenchError as refused:
        print(str(refused), file=sys.stderr)
        return 2
    print(json.dumps({k: result[k] for k in ("ok", "refused", "saturation", "problems")
                      if k in result}))
    return 1 if result.get("ok") is False or result.get("refused") else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
