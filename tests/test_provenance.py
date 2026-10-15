"""Provenance: everything a disputed number needs to be recomputed (this.i 8q4myzru; Part B #6)."""

import json
import subprocess

import pytest

from keri_bench import provenance
from keri_bench.errors import BenchError


def test_the_host_fingerprint_names_the_machine_and_its_capacity():
    host = provenance.host_fingerprint()
    for key in ("hostname", "kernel", "machine", "cpu_model", "cpu_count", "memory_bytes",
                "python", "boot_id"):
        assert key in host
    assert host["cpu_count"] >= 1


def test_the_cpu_model_and_memory_fall_back_when_proc_is_unreadable(tmp_path):
    assert provenance.cpu_model(tmp_path / "absent") == "unknown"
    assert provenance.memory_bytes(tmp_path / "absent") is None
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor : 0\nmodel name\t: Test CPU 9000\n")
    assert provenance.cpu_model(cpuinfo) == "Test CPU 9000"
    cpuinfo.write_text("processor : 0\n")
    assert provenance.cpu_model(cpuinfo) == "unknown"
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       1024 kB\n")
    assert provenance.memory_bytes(meminfo) == 1024 * 1024
    meminfo.write_text("Other: 1\n")
    assert provenance.memory_bytes(meminfo) is None


def test_the_harness_commit_comes_from_git_and_says_when_the_tree_is_dirty(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,
                                    capture_output=True)
    run("init", "-q")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    commit = provenance.harness_commit(repo)
    assert len(commit) == 40
    (repo / "new").write_text("x")
    run("add", "new")
    assert provenance.harness_commit(repo) == commit + "-dirty"
    assert provenance.harness_commit(tmp_path / "nowhere") == "unknown"


def complete():
    return provenance.build(
        harness_commit="abc", scenario_text="x = 1\n", scenario_sha256="f" * 64,
        adapters={"issuer": {"adapter": "synthetic.issuer", "knobs": {"version": 1}}},
        knobs={"issuer": {"service_ms": 5}}, components={"issuer": {"synthetic": "1"}},
        host={"hostname": "h"}, clocks={"harness": "monotonic@b"}, run={"rate": 10.0})


def test_a_built_provenance_has_every_required_field_and_round_trips(tmp_path):
    record = complete()
    assert set(provenance.REQUIRED) <= set(record)
    assert record["provenance_schema"] == provenance.PROVENANCE_SCHEMA
    path = tmp_path / "provenance.json"
    provenance.write(path, record)
    assert provenance.read(path) == json.loads(path.read_text())


@pytest.mark.parametrize("field", sorted(provenance.REQUIRED))
def test_a_provenance_missing_any_required_field_is_refused(tmp_path, field):
    record = complete()
    del record[field]
    with pytest.raises(BenchError) as refused:
        provenance.validate(record)
    assert refused.value.code == "e.input.missing.provenance.field.f"
    assert field in refused.value.message


def test_an_unknown_provenance_schema_is_refused():
    record = complete()
    record["provenance_schema"] = 99
    with pytest.raises(BenchError) as refused:
        provenance.validate(record)
    assert refused.value.code == "e.feature.unsupported.provenance.schema.f"


def test_reading_validates(tmp_path):
    path = tmp_path / "provenance.json"
    path.write_text("{}")
    with pytest.raises(BenchError):
        provenance.read(path)


def test_untested_versions_are_named_per_adapter():
    adapters = {"issuer": {"adapter": "imbu.issuer", "tested_against": {"imbu": "abc"}},
                "holder": {"adapter": "heti.holder", "tested_against": {"heti": "c9f"}}}
    components = {"issuer": {"imbu": "def"}, "holder": {"heti": "c9f", "keripy": "1.2"}}
    assert provenance.untested(adapters, components) == [
        {"role": "issuer", "adapter": "imbu.issuer", "component": "imbu", "running": "def",
         "tested": "abc"},
        {"role": "holder", "adapter": "heti.holder", "component": "keripy", "running": "1.2",
         "tested": None},
    ]
