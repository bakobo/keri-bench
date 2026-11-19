"""Re-rendering the committed run reports from their report.json (tools/rerender.py, this.i
6d0jwr9z), and the two things that must stay true of the committed results afterwards."""

import hashlib
import json
from pathlib import Path

from keri_bench.report import render_markdown
from tools import rerender

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "golden" / "v1" / "singles" / "expected-report.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def results_tree(tmp_path) -> Path:
    """Two runs, one manifest above both and one nested, as targets-2026-10 and its probes are."""
    report = GOLDEN.read_text()
    for run in ("results/a/s/rate-1/rep-1", "results/a/probes/p/rate-1/rep-1"):
        (tmp_path / run).mkdir(parents=True)
        (tmp_path / run / "report.json").write_text(report)
        (tmp_path / run / "report.md").write_text("old\n")
        (tmp_path / run / "timeline.jsonl").write_text("{}\n")
    top = tmp_path / "results" / "a"
    (top / "SHA256SUMS").write_text(
        f"{sha(b'{}' + bytes([10]))}  ./s/rate-1/rep-1/timeline.jsonl\n"
        f"{sha(b'old' + bytes([10]))}  ./s/rate-1/rep-1/report.md\n"
        f"{sha(b'old' + bytes([10]))}  ./probes/p/rate-1/rep-1/report.md\n")
    (top / "probes" / "SHA256SUMS").write_text(f"{sha(b'old' + bytes([10]))}  ./p/rate-1/rep-1/report.md\n")
    return tmp_path


def test_reports_are_rendered_from_their_json_and_only_their_manifest_lines_move(tmp_path):
    repo = results_tree(tmp_path)
    before = (repo / "results/a/SHA256SUMS").read_text().splitlines()
    assert rerender.rerender(repo) == {"reports": 2, "manifest_lines": 3}
    page = render_markdown(json.loads(GOLDEN.read_text()))
    assert (repo / "results/a/s/rate-1/rep-1/report.md").read_text() == page
    after = (repo / "results/a/SHA256SUMS").read_text().splitlines()
    assert after[0] == before[0]
    assert after[1] == f"{sha(page.encode())}  ./s/rate-1/rep-1/report.md"
    assert rerender.rerender(repo) == {"reports": 0, "manifest_lines": 0}


def test_main_reports_what_it_changed(tmp_path, capsys):
    assert rerender.main([str(results_tree(tmp_path))]) == 0
    assert "re-rendered 2 report(s); updated 3 manifest line(s)" in capsys.readouterr().out


def test_main_defaults_to_this_repository(monkeypatch):
    seen = {}
    monkeypatch.setattr(rerender, "rerender",
                        lambda repo: seen.update(repo=repo) or {"reports": 0, "manifest_lines": 0})
    monkeypatch.setattr("sys.argv", ["scripts/rerender-reports"])
    assert rerender.main() == 0 and seen["repo"] == REPO


def test_a_path_with_no_results_is_refused_not_reported_as_success(tmp_path, capsys):
    """The hostile pass on #7: a mistyped path printed 're-rendered 0 report(s)'."""
    assert rerender.main([str(tmp_path / "nowhere")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("scripts/rerender-reports: ") and rerender.E_RESULTS in err
    (tmp_path / "empty" / "results").mkdir(parents=True)
    assert rerender.main([str(tmp_path / "empty")]) == 1
    assert "holds no report.json" in capsys.readouterr().err


def test_every_committed_report_reads_as_the_renderer_renders_its_json():
    """A renderer change that leaves the committed reports stale fails here; run
    scripts/rerender-reports."""
    stale = [str(source.parent) for source in sorted((REPO / "results").rglob("report.json"))
             if source.with_name("report.md").read_text()
             != render_markdown(json.loads(source.read_text()))]
    assert not stale, stale[:5]


def test_every_committed_manifest_line_matches_its_file():
    wrong = []
    for manifest in sorted((REPO / "results").rglob(rerender.MANIFEST)):
        for line in manifest.read_text().splitlines():
            digest, _, name = line.partition("  ")
            if sha((manifest.parent / name).read_bytes()) != digest:
                wrong.append(f"{manifest.parent}/{name}")
    assert not wrong, wrong[:5]
