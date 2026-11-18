"""Re-render every committed run report from its report.json (this.i 6d0jwr9z).

A run's report.md is how its report.json reads to a person, so when the renderer changes, the
committed reports are rendered again from the JSON that was committed with them. They are never
re-derived from the timeline here: derive() changes over time, and re-deriving could move a number
a write-up cites, where rendering the committed JSON changes only how a number reads. Each
results directory's SHA256SUMS gets new hashes for its report.md lines and keeps every other line
byte for byte, so the timelines, provenance and report.json stay attested exactly as committed.

    uv run python scripts/rerender-reports
"""

import hashlib
import json
import sys
from pathlib import Path

from keri_bench.errors import BenchError
from keri_bench.report import render_markdown

MANIFEST = "SHA256SUMS"
E_RESULTS = "e.input.missing.rerender.results.f"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rerender(repo) -> dict:
    """Render each results/**/report.json to the report.md beside it, then bring every manifest's
    report.md lines up to date. Returns how many reports and manifest lines changed."""
    results = Path(repo) / "results"
    if not results.is_dir():
        raise BenchError(E_RESULTS, f"{results} is not a directory, so there are no committed "
                         f"reports to re-render. Give the repository's root.")
    sources = sorted(results.rglob("report.json"))
    if not sources:
        raise BenchError(E_RESULTS, f"{results} holds no report.json, so there are no committed "
                         f"reports to re-render. Give the repository's root.")
    reports = 0
    for source in sources:
        page = render_markdown(json.loads(source.read_text(encoding="utf-8")))
        target = source.with_name("report.md")
        if not target.exists() or target.read_text(encoding="utf-8") != page:
            target.write_text(page, encoding="utf-8")
            reports += 1
    lines = 0
    for manifest in sorted(results.rglob(MANIFEST)):
        out = []
        for line in manifest.read_text(encoding="utf-8").splitlines(keepends=True):
            digest, sep, name = line.rstrip("\n").partition("  ")
            if sep and name.endswith("/report.md"):
                fresh = _sha256(manifest.parent / name)
                if fresh != digest:
                    line = f"{fresh}  {name}\n"
                    lines += 1
            out.append(line)
        manifest.write_text("".join(out), encoding="utf-8")
    return {"reports": reports, "manifest_lines": lines}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    repo = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    try:
        changed = rerender(repo)
    except BenchError as exc:
        print(f"scripts/rerender-reports: {exc}", file=sys.stderr)
        return 1
    print(f"re-rendered {changed['reports']} report(s); updated {changed['manifest_lines']} "
          f"manifest line(s)")
    return 0
