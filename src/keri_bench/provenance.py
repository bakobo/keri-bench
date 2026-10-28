"""``provenance.json``: what ran, where, configured how (this.i 8q4myzru; test plan, Outputs).

The report is derived from this file and the timeline only, so everything a reader needs to judge
or recompute a number is here: component commits and versions, each adapter's declaration and
schema versions, the resolved knobs, the host, the clock domains, and the scenario verbatim.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from .errors import BenchError

PROVENANCE_SCHEMA = 1
REQUIRED = frozenset({"provenance_schema", "harness_commit", "scenario", "scenario_sha256",
                      "adapters", "knobs", "components", "host", "clocks", "run"})

E_FIELD = "e.input.missing.provenance.field.f"
E_SCHEMA = "e.feature.unsupported.provenance.schema.f"

CPUINFO = Path("/proc/cpuinfo")
MEMINFO = Path("/proc/meminfo")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


def cpu_model(path: Path = CPUINFO) -> str:
    try:
        for line in path.read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def memory_bytes(path: Path = MEMINFO) -> int | None:
    try:
        for line in path.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def host_fingerprint() -> dict[str, Any]:
    return {"hostname": socket.gethostname(), "kernel": platform.release(),
            "machine": platform.machine(), "cpu_model": cpu_model(),
            "cpu_count": os.cpu_count(), "memory_bytes": memory_bytes(),
            "python": sys.version.split()[0], "boot_id": BOOT_ID.read_text().strip()}


def load_average() -> list[float]:
    """The host's 1, 5 and 15 minute load averages: on a shared host, how busy it was."""
    return [round(value, 2) for value in os.getloadavg()]


def harness_commit(repo: Path) -> str:
    """The harness's commit, with ``-dirty`` when uncommitted changes could affect a number."""
    try:
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain",
                                "--untracked-files=no"], check=True, capture_output=True,
                               text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return commit + ("-dirty" if dirty else "")


def build(*, harness_commit: str, scenario_text: str, scenario_sha256: str, adapters: dict,
          knobs: dict, components: dict, host: dict, clocks: dict, run: dict,
          **extra: Any) -> dict:
    record = {"provenance_schema": PROVENANCE_SCHEMA, "harness_commit": harness_commit,
              "scenario": scenario_text, "scenario_sha256": scenario_sha256,
              "adapters": adapters, "knobs": knobs, "components": components, "host": host,
              "clocks": clocks, "run": run, **extra}
    validate(record)
    return record


def validate(record: dict) -> None:
    missing = sorted(REQUIRED - set(record))
    if missing:
        raise BenchError(E_FIELD, f"The provenance lacks {missing}, so a report built on it "
                         f"could not be audited.")
    if record["provenance_schema"] != PROVENANCE_SCHEMA:
        raise BenchError(E_SCHEMA, f"Provenance schema {record['provenance_schema']!r} is not "
                         f"one this harness wrote.")


def write(path: Path, record: dict) -> None:
    validate(record)
    Path(path).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def read(path: Path) -> dict:
    record = json.loads(Path(path).read_text())
    validate(record)
    return record


def untested(adapters: dict, components: dict) -> list[dict]:
    """Every running component version an adapter was never contract-tested against."""
    found = []
    for role, declared in adapters.items():
        tested = declared.get("tested_against", {})
        for component, running in components.get(role, {}).items():
            if tested.get(component) != running:
                found.append({"role": role, "adapter": declared["adapter"],
                              "component": component, "running": running,
                              "tested": tested.get(component)})
    return found
