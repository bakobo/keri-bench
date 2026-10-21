"""The primitive microbenchmark (this.i 9n80j7ph): keripy's own Ed25519 and Blake3-256."""

import json
import subprocess
import sys

import pytest

from keri_bench.stacks import microbench


def test_each_primitive_is_timed_at_each_size_and_its_output_checked():
    out = microbench.bench(sizes=(32, 1024), iterations=50)
    assert set(out["operations"]) == {"ed25519_sign", "ed25519_verify", "blake3_256"}
    for op, by_size in out["operations"].items():
        assert set(by_size) == {"32", "1024"}
        for row in by_size.values():
            assert row["n"] == 50
            # p99 is quoted as its slot's upper bound, so it may sit just above the raw max.
            assert 0 < row["p50_ns"] <= row["p99_ns"] and row["max_ns"] > 0
            assert row["per_s"] > 0
    assert out["iterations"] == 50 and out["sizes"] == [32, 1024]
    assert out["keri"] and out["host"]["cpu_count"] >= 1
    assert isinstance(out["nice"], int)


def test_a_signature_that_does_not_verify_stops_the_benchmark(monkeypatch):
    monkeypatch.setattr(microbench, "_verify_ok", lambda verfer, sig, data: False)
    with pytest.raises(microbench.BenchError) as wrong:
        microbench.bench(sizes=(8,), iterations=2)
    assert wrong.value.code == "e.self.microbench.verify.f"


@pytest.mark.parametrize("sizes, iterations", [((), 10), ((8,), 0), ((0,), 5)])
def test_an_empty_or_degenerate_benchmark_is_refused(sizes, iterations):
    with pytest.raises(microbench.BenchError) as refused:
        microbench.bench(sizes=sizes, iterations=iterations)
    assert refused.value.code == "e.input.range.microbench.f"


def test_the_module_writes_its_result_to_a_file(tmp_path):
    out = tmp_path / "micro.json"
    subprocess.run([sys.executable, "-m", "keri_bench.stacks.microbench", "--out", str(out),
                    "--iterations", "5", "--size", "16"], check=True)
    result = json.loads(out.read_text())
    assert result["sizes"] == [16] and result["iterations"] == 5
