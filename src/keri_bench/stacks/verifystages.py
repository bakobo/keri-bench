"""The timed verifier's summary (this.i 3jeks5st):
``python -m keri_bench.stacks.verifystages <stages.jsonl> --out <dir>``.

It reads the lines the timed verifier wrote and summarises the warm attempts: accepted, run
alone, and from a holder already met, so each is one presentation's verification with nothing
else charged to it. Per stage it reports p50 and p99 through the one named percentile
definition, the mean and the calls per attempt; then the rollups the papers name (Fujie and
Suzuki, arXiv:2611.03493): issuer key state, status, holder binding, and what is left, beside
the IPEX admit receipt, which is not a check. The first meetings, opening the lockbox and the
first presentation from each holder, are reported apart, as one-off costs, keyed by the
attempt's credential and its holder's AID.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..errors import BenchError
from ..stats import Histogram
from .stages import PHASES, STAGES

E_EMPTY = "e.state.missing.verifystages.warm.f"
E_FORMAT = "e.input.format.verifystages.line.f"

ROLLUPS = {
    "issuer key state": ("key_state.issuer",),
    "status": ("tel.fetch", "tel.vet"),
    "holder binding": ("key_state.holder", "signatures"),
    "admit receipt": ("receipt",),
}
LEFT = "everything else"
OUTCOMES = ("accepted", "refused", "opened")
UNCODED = "(no code)"
"""How a refusal the verifier answered without a code is counted, such as a 200 that was not
valid: a key that sorts with the codes, which None does not."""


def _row(values: list[int], calls: list[int] | None = None, total: int | None = None) -> dict:
    histogram = Histogram()
    for value in values:
        histogram.record(value)
    row = {"n": len(values), "p50_ms": histogram.percentile(50) / 1e6,
           "p99_ms": histogram.percentile(99) / 1e6, "max_ms": histogram.max / 1e6,
           "mean_ms": sum(values) / len(values) / 1e6}
    if calls is not None:
        row["calls_per_attempt"] = sum(calls) / len(calls)
    if total is not None:
        row["share"] = sum(values) / total if total else 0.0
    return row


MAX_NS = 2**63 - 1
"""The histogram's ceiling, about 292 years; the writer never comes near it."""


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_NS


def _text(value, *, optional: bool) -> bool:
    if optional and value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate, which no report can write
        return False
    return True


def _well_formed(record) -> bool:
    """The whole record the timed verifier writes, and nothing the summary would read
    differently: every stage and call a non-negative integer, and an accepted attempt's time
    inside both of heti's calls."""
    if not (isinstance(record, dict) and record.get("outcome") in OUTCOMES
            and _text(record.get("credential"), optional=False)
            and _text(record.get("holder"), optional=True)
            and "code" in record and _text(record["code"], optional=True)
            and _count(record.get("total_ns")) and isinstance(record.get("overlapped"), bool)
            and isinstance(record.get("phases"), dict)):
        return False
    phases = record["phases"]
    if not (set(phases) <= set(PHASES) and all(_count(v) for v in phases.values())):
        return False
    if record["outcome"] == "accepted" and set(phases) != set(PHASES):
        return False
    if not all(isinstance(record.get(key), dict) and set(record[key]) == set(STAGES)
               and all(_count(v) for v in record[key].values()) for key in ("stages", "calls")):
        return False
    # heti's two calls run one after the other inside the attempt, and so do its stages unless
    # the attempt says it overlapped.
    return sum(phases.values()) <= record["total_ns"] and (
        record["overlapped"] or sum(record["stages"].values()) <= record["total_ns"])


def _parsed(lines) -> list[dict]:
    records = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not _well_formed(record):
                raise ValueError
        except ValueError:
            raise BenchError(E_FORMAT, f"line {number} of the stage times is not one of the "
                             f"timed verifier's records: {line[:120]!r}") from None
        records.append(record)
    return records


def summarise(lines) -> dict:
    records = _parsed(lines)
    attempts = [r for r in records if r["outcome"] in ("accepted", "refused")]
    accepted = [r for r in attempts if r["outcome"] == "accepted"]
    warm = [r for r in accepted if not r["overlapped"] and not r["calls"]["meet"]]
    if not warm:
        raise BenchError(E_EMPTY, f"Of {len(attempts)} verification attempts none was accepted, "
                         f"alone and from a holder already met, so there is no warm "
                         f"verification to summarise.")
    refused: dict[str, int] = {}
    for r in attempts:
        if r["outcome"] == "refused":
            code = r["code"] if r["code"] is not None else UNCODED
            refused[code] = refused.get(code, 0) + 1
    totals = [r["total_ns"] for r in warm]
    grand = sum(totals)
    rollups = {name: _row([sum(r["stages"][s] for s in members) for r in warm], total=grand)
               for name, members in ROLLUPS.items()}
    rollups[LEFT] = _row([r["total_ns"] - sum(r["stages"].values()) for r in warm], total=grand)
    return {
        "attempts": {"total": len(attempts), "accepted": len(accepted),
                     "refused": len(attempts) - len(accepted),
                     "overlapped": sum(r["overlapped"] for r in accepted),
                     "warm": len(warm), "refused_codes": refused},
        "warm": {
            "total": _row(totals),
            "phases": {p: _row([r["phases"].get(p, 0) for r in warm]) for p in PHASES},
            "stages": {s: _row([r["stages"][s] for r in warm], [r["calls"][s] for r in warm],
                               total=grand) for s in STAGES},
            "rollups": rollups,
        },
        "meetings": {r["credential"] + (f" ({r['holder']})" if r.get("holder") else ""):
                     r["stages"]["meet"] / 1e6 for r in records if r["calls"]["meet"]},
    }


def _ms(value: float) -> str:
    return f"{value:.2f}"


def markdown(summary: dict) -> str:
    a, w = summary["attempts"], summary["warm"]
    out = [f"{a['warm']} warm verifications of {a['total']} attempts: {a['accepted']} accepted, "
           f"{a['refused']} refused {a['refused_codes'] or ''}, {a['overlapped']} accepted "
           f"beside another and left out, and the first from each holder left out.", "",
           "| rollup | p50 ms | p99 ms | mean ms | share |", "|---|---|---|---|---|"]
    for name, row in w["rollups"].items():
        out.append(f"| {name} | {_ms(row['p50_ms'])} | {_ms(row['p99_ms'])} | "
                   f"{_ms(row['mean_ms'])} | {row['share']:.1%} |")
    t = w["total"]
    out += [f"| total | {_ms(t['p50_ms'])} | {_ms(t['p99_ms'])} | {_ms(t['mean_ms'])} | |", "",
            "| stage | calls | p50 ms | p99 ms | mean ms | share |", "|---|---|---|---|---|---|"]
    for name, row in w["stages"].items():
        out.append(f"| {name} | {row['calls_per_attempt']:g} | {_ms(row['p50_ms'])} | "
                   f"{_ms(row['p99_ms'])} | {_ms(row['mean_ms'])} | {row['share']:.1%} |")
    out += ["", "| heti call | p50 ms | p99 ms | mean ms |", "|---|---|---|---|"]
    for name, row in w["phases"].items():
        out.append(f"| {name} | {_ms(row['p50_ms'])} | {_ms(row['p99_ms'])} | "
                   f"{_ms(row['mean_ms'])} |")
    out += ["", "First meetings (OOBI resolution and KEL replay), ms: "
            + ", ".join(f"{k} {_ms(v)}" for k, v in summary["meetings"].items()), ""]
    return "\n".join(out)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = summarise(args.source.read_text().splitlines())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "stages.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (args.out / "stages.md").write_text(markdown(summary))


if __name__ == "__main__":
    main()
