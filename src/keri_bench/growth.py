"""How a stack's cost grows with its stock (this.i 4j5395l8).

A soak's report holds, per bucket of credentials, the stock the store held, the bytes it
occupied and the bucket's p50 and p99 (this.i 7f9jfc99). This pools the buckets of one or more
soaks, fits store bytes, p50 and p99 against stock with a line and a parabola by least squares,
and says what each curve gives at stocks past the data and where a latency curve crosses a
bound. Both degrees are reported, because which one a stack follows is the finding (the first
baseline's store grew quadratically); R^2 says how well each fits, and nothing here chooses.
An extrapolation is only as good as the assumption that the curve keeps its shape, which the
doc that cites it must say.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .errors import BenchError

E_FIT = "e.input.range.growth.fit.f"
E_REPORT = "e.input.format.growth.report.f"
DEGREES = {1: "linear", 2: "quadratic"}
CURVES = {"store_bytes": ("store_bytes", 1), "p50_s": ("p50_ns", 1e-9),
          "p99_s": ("p99_ns", 1e-9)}


@dataclass(frozen=True)
class Fit:
    coefficients: tuple[float, ...]
    """Constant first: y = c0 + c1 x (+ c2 x^2)."""
    r2: float | None
    """None when y never varies, so there is no variance to explain."""
    n: int

    def at(self, x: float) -> float:
        return sum(c * x ** k for k, c in enumerate(self.coefficients))


def polyfit(xs: Sequence[float], ys: Sequence[float], degree: int) -> Fit:
    """Least squares by the normal equations, solved exactly in fractions. A fit needs more
    points than coefficients, so it has something to be judged against, and as many distinct x
    as coefficients."""
    if degree not in DEGREES:
        raise BenchError(E_FIT, f"Only lines and parabolas are fitted, not degree {degree}.")
    if len(xs) != len(ys) or len(xs) < degree + 2 or len(set(xs)) < degree + 1:
        raise BenchError(E_FIT, f"a degree-{degree} fit needs at least {degree + 2} points "
                         f"with {degree + 1} distinct stocks; it has {min(len(xs), len(ys))}.")
    fx = [Fraction(x) for x in xs]
    fy = [Fraction(y) for y in ys]
    size = degree + 1
    rows = [[sum(x ** (i + j) for x in fx) for j in range(size)]
            + [sum(y * x ** i for x, y in zip(fx, fy))] for i in range(size)]
    for col in range(size):
        pivot = next(r for r in range(col, size) if rows[r][col] != 0)
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(size):
            if r != col and rows[r][col] != 0:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    coefficients = tuple(float(rows[i][size] / rows[i][i]) for i in range(size))
    mean = sum(fy) / len(fy)
    total = sum((y - mean) ** 2 for y in fy)
    fit = Fit(coefficients, None, len(xs))
    if total == 0:
        return fit
    residual = sum((Fraction(fit.at(float(x))) - y) ** 2 for x, y in zip(fx, fy))
    return Fit(coefficients, float(1 - residual / total), len(xs))


def crossing(fit: Fit, bound: float) -> float | None:
    """The least stock, not below zero, at which the curve reaches ``bound``; None if it never
    does."""
    c0, c1, *rest = fit.coefficients
    c2 = rest[0] if rest else 0.0
    if c0 >= bound:
        return 0.0
    if c2 == 0:
        return (bound - c0) / c1 if c1 > 0 else None
    disc = c1 * c1 - 4 * c2 * (c0 - bound)
    if disc < 0:
        return None
    roots = sorted(((-c1 - math.sqrt(disc)) / (2 * c2), (-c1 + math.sqrt(disc)) / (2 * c2)))
    return next(r for r in roots if r >= 0)


def _points(reports: Iterable[Mapping], key: str, scale: float) -> tuple[list, list]:
    xs, ys = [], []
    for report in reports:
        for bucket in report["drift"]["buckets"]:
            if bucket.get(key) is not None:
                xs.append(bucket["store_credentials"])
                ys.append(bucket[key] * scale)
    return xs, ys


def growth(reports: Sequence[Mapping], *, at: Sequence[float],
           bound_s: float | None) -> dict[str, Any]:
    """Every curve at both degrees, what each gives at ``at``, and for the latency curves
    where each reaches ``bound_s``."""
    for report in reports:
        if not isinstance(report.get("drift"), Mapping):
            raise BenchError(E_REPORT, "A report without a soak's drift buckets has no stock "
                             "to fit against.")
    stocks = [b["store_credentials"] for r in reports for b in r["drift"]["buckets"]]
    out: dict[str, Any] = {"buckets": len(stocks), "stock": [min(stocks), max(stocks)],
                           "bound_s": bound_s}
    for name, (key, scale) in CURVES.items():
        xs, ys = _points(reports, key, scale)
        out[name] = {}
        for degree, label in DEGREES.items():
            try:
                fit = polyfit(xs, ys, degree)
            except BenchError as refused:
                out[name][label] = {"not measured": str(refused)}
                continue
            entry = {"coefficients": list(fit.coefficients), "r2": fit.r2, "n": fit.n,
                     "at": {f"{x:g}": fit.at(x) for x in at}}
            if bound_s is not None and name != "store_bytes":
                entry["crosses_bound_at"] = crossing(fit, bound_s)
            out[name][label] = entry
    return out
