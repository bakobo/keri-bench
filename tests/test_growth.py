"""Growth with stock: least-squares curves over soak buckets, and where they cross a bound
(this.i 4j5395l8). Expected values are worked by hand here, not produced by the code."""

import json

import pytest

from keri_bench import cli, growth
from keri_bench.errors import BenchError


def test_a_line_through_exact_points_is_recovered_with_r2_of_one():
    fit = growth.polyfit([0, 1, 2, 3], [1, 3, 5, 7], 1)
    assert fit.coefficients == pytest.approx([1.0, 2.0])
    assert fit.r2 == pytest.approx(1.0) and fit.n == 4
    assert fit.at(10) == pytest.approx(21.0)


def test_a_parabola_through_exact_points_is_recovered():
    # y = 3 + 0.5 x + 2 x^2
    xs = [0, 10, 20, 30, 40]
    fit = growth.polyfit(xs, [3 + 0.5 * x + 2 * x * x for x in xs], 2)
    assert fit.coefficients == pytest.approx([3.0, 0.5, 2.0])
    assert fit.r2 == pytest.approx(1.0)


def test_r2_measures_the_share_of_variance_a_line_explains():
    # Points 0,1 / 1,3 / 2,2: the line is y = 1.5 + 0.5 x, residuals -0.5, 1, -0.5,
    # SSres 1.5 against SStot 2, so R^2 is 0.25.
    fit = growth.polyfit([0, 1, 2], [1, 3, 2], 1)
    assert fit.coefficients == pytest.approx([1.5, 0.5])
    assert fit.r2 == pytest.approx(0.25)


def test_a_flat_series_has_no_r2():
    assert growth.polyfit([0, 1, 2], [5, 5, 5], 1).r2 is None


@pytest.mark.parametrize("xs, ys, degree", [
    ([0, 1], [1, 2], 1),            # two points fit any line exactly: nothing to judge
    ([0, 1, 2], [1, 2, 3], 2),      # three points fit any parabola exactly
    ([1, 1, 1, 1], [1, 2, 3, 4], 1),  # one x: no slope
    ([0, 1, 2], [1, 2], 1),         # unequal lengths
    ([0, 1, 2, 3], [1, 2, 3, 4], 3),  # only lines and parabolas
])
def test_a_fit_with_nothing_to_judge_or_no_answer_is_refused(xs, ys, degree):
    with pytest.raises(BenchError) as refused:
        growth.polyfit(xs, ys, degree)
    assert refused.value.code == "e.input.range.growth.fit.f"


def test_a_rising_line_crosses_a_bound_once():
    fit = growth.Fit((1.0, 2.0), 1.0, 4)
    assert growth.crossing(fit, 21.0) == pytest.approx(10.0)
    # Already past the bound at no stock: it crossed at zero.
    assert growth.crossing(fit, 0.5) == 0.0
    # A flat or falling line never reaches a bound above it.
    assert growth.crossing(growth.Fit((1.0, 0.0), None, 4), 5.0) is None
    assert growth.crossing(growth.Fit((5.0, -1.0), 1.0, 4), 9.0) is None


def test_a_parabola_crosses_at_its_first_root_past_zero():
    # 2 x^2 + 0.5 x + 3 = 208 at x = 10.
    assert growth.crossing(growth.Fit((3.0, 0.5, 2.0), 1.0, 5), 208.0) == pytest.approx(10.0)
    # One that opens downward and peaks below the bound never reaches it.
    assert growth.crossing(growth.Fit((0.0, 1.0, -1.0), 1.0, 5), 5.0) is None
    # One that dips below zero first: x^2 - 4x + 3 = 8 at x = 5, not at -1.
    assert growth.crossing(growth.Fit((3.0, -4.0, 1.0), 1.0, 5), 8.0) == pytest.approx(5.0)
    # A parabola whose x^2 term is zero is a line.
    assert growth.crossing(growth.Fit((1.0, 2.0, 0.0), 1.0, 5), 21.0) == pytest.approx(10.0)


def bucket(stock, size, p50_s, p99_s):
    return {"store_credentials": stock, "store_bytes": size,
            "p50_ns": None if p50_s is None else int(p50_s * 1e9),
            "p99_ns": None if p99_s is None else int(p99_s * 1e9)}


def test_growth_pools_every_report_s_buckets_against_stock():
    first = {"drift": {"buckets": [bucket(25, 1000, 1.0, 2.0), bucket(75, 3000, 2.0, 3.0)]}}
    second = {"drift": {"buckets": [bucket(125, 5000, 3.0, 4.0), bucket(175, None, 4.0, 5.0),
                                    bucket(225, 9000, None, None)]}}
    out = growth.growth([first, second], at=[1000], bound_s=10.0)
    assert out["buckets"] == 5 and out["stock"] == [25, 225]
    # Bytes: 40 a credential exactly, from the four buckets that were sampled.
    linear = out["store_bytes"]["linear"]
    assert linear["coefficients"] == pytest.approx([0.0, 40.0], abs=1e-6)
    assert linear["n"] == 4 and linear["at"] == {"1000": pytest.approx(40_000.0)}
    # p50 rises a second per 50 credentials: 10 s at 475.
    p50 = out["p50_s"]["linear"]
    assert p50["n"] == 4 and p50["crosses_bound_at"] == pytest.approx(475.0)
    assert out["p99_s"]["linear"]["crosses_bound_at"] == pytest.approx(425.0)
    assert out["bound_s"] == 10.0
    assert set(out["store_bytes"]) == {"linear", "quadratic"}


def test_growth_says_which_curves_had_too_little_to_fit():
    out = growth.growth([{"drift": {"buckets": [bucket(25, None, 1.0, 2.0),
                                                bucket(75, None, 2.0, 2.5)]}}],
                        at=[], bound_s=None)
    assert out["store_bytes"]["linear"] == {"not measured": "e.input.range.growth.fit.f: "
                                            "a degree-1 fit needs at least 3 points with 2 "
                                            "distinct stocks; it has 0."}
    assert "crosses_bound_at" not in out["p50_s"]["quadratic"]


def test_a_report_without_soak_buckets_is_refused():
    with pytest.raises(BenchError) as refused:
        growth.growth([{"drift": "not measured"}], at=[], bound_s=None)
    assert refused.value.code == "e.input.format.growth.report.f"


def test_the_command_reads_reports_and_prints_the_fits(tmp_path, capsys):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"drift": {"buckets": [
        bucket(25, 1000, 1.0, 2.0), bucket(75, 3000, 2.0, 3.0), bucket(125, 5000, 3.0, 4.0)]}}))
    assert cli.main(["growth", str(path), "--at", "1000", "--bound-s", "10", "--out",
                     str(tmp_path / "growth.json")]) == 0
    out = json.loads((tmp_path / "growth.json").read_text())
    assert out["reports"] == [str(path)]
    assert out["store_bytes"]["linear"]["at"]["1000"] == pytest.approx(40_000.0)
