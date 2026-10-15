"""The keri-bench command."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scenarios import scenario  # noqa: E402

from keri_bench import cli  # noqa: E402


def write(tmp_path, text):
    path = tmp_path / "scenario.toml"
    path.write_text(text)
    return path


def issue_only(**kw):
    return scenario(stages='["issue"]', names=("state_distribution", "issuer"), **kw)


def test_one_run_with_a_calibration_then_a_rebuilt_report(tmp_path, capsys):
    calibration = tmp_path / "calibration.json"
    calibration.write_text(json.dumps({
        "ok": True, "harness_commit": "c", "intervals": {"budget": {"a->b": {
            "p50_error_ns": 1, "p99_error_ns": 2}}}, "saturation": {"relative_error": 0.01}}))
    code = cli.main(["run", str(write(tmp_path, issue_only())), "--out", str(tmp_path / "out"),
                     "--rate", "10", "--calibration", str(calibration)])
    printed = json.loads(capsys.readouterr().out)
    assert code == (1 if printed["refused"] else 0)
    run_dir = tmp_path / "out" / "rate-10" / "rep-1"
    record = json.loads((run_dir / "provenance.json").read_text())
    assert record["calibration"]["budget"] == {"a->b": {"p50_error_ns": 1, "p99_error_ns": 2}}
    (run_dir / "report.json").unlink()
    assert cli.main(["report", str(run_dir)]) in (0, 1)
    assert (run_dir / "report.json").exists()


def test_a_whole_sweep_prints_its_saturation(tmp_path, capsys):
    code = cli.main(["run", str(write(tmp_path, issue_only(duration="0.5"))), "--out",
                     str(tmp_path / "out")])
    printed = json.loads(capsys.readouterr().out)
    assert "saturation" in printed
    assert code == (1 if printed["refused"] else 0)  # a loaded host may lag a run
    assert (tmp_path / "out" / "sweep.json").exists()


def test_a_refused_scenario_exits_2_with_its_code(tmp_path, capsys):
    assert cli.main(["run", str(write(tmp_path, "scenario_schema = 7")), "--out",
                     str(tmp_path)]) == 2
    assert "e.feature.unsupported.scenario.schema.f" in capsys.readouterr().err


def test_calibrate_reports_failure_with_exit_1(tmp_path, monkeypatch, capsys):
    async def fake(scenarios, out, *, harness_commit):
        assert scenarios == tmp_path and harness_commit
        return {"ok": False}

    monkeypatch.setattr(cli, "calibrate", fake)
    assert cli.main(["calibrate", "--scenarios", str(tmp_path), "--out", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False}


def test_the_contract_command_prints_each_adapters_problems(tmp_path, capsys):
    scenarios = Path(__file__).parent.parent / "scenarios"
    code = cli.main(["contract", str(scenarios / "contract-synthetic.toml"), "--out",
                     str(tmp_path)])
    printed = json.loads(capsys.readouterr().out)
    assert code == 0 and printed["ok"] and "synthetic.issuer" in printed["problems"]
