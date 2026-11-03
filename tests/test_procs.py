"""Process control for roles (test plan, Part B #8, and Faults: crash)."""

import asyncio
import json
import os
import signal
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from keri_bench.errors import BenchError
from keri_bench.procs import (E_DUPLICATE, E_STARTUP, E_UNKNOWN, ProcessController,
                              StartupError, spawn)

FAKE = (sys.executable, str(Path(__file__).with_name("fake_role.py")))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie is dead; it only awaits reaping.
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return False


async def gone(pid: int, within: float = 3.0) -> bool:
    for _ in range(int(within / 0.05)):
        if not alive(pid):
            return True
        await asyncio.sleep(0.05)
    return not alive(pid)


def logged_pid(log: Path) -> int:
    lines = [line for line in log.read_text().splitlines() if line.startswith("pid ")]
    return int(lines[-1].split()[1])


class Recorder:
    def __init__(self):
        self.events = []
        self.now = 0

    def clock(self) -> int:
        self.now += 10
        return self.now

    def __call__(self, name, t, attrs):
        self.events.append((name, t, attrs))


@pytest.fixture
async def controller(tmp_path):
    recorder = Recorder()
    ctl = ProcessController(tmp_path, clock=recorder.clock, on_event=recorder)
    ctl.recorder = recorder
    yield ctl
    await ctl.stop_all()


async def test_a_started_role_announces_and_is_ready(controller, tmp_path):
    port = free_port()
    proc = await controller.start("witness", FAKE, ("ok", "--port", str(port)), timeout=5)
    assert proc.announced == {"url": f"http://127.0.0.1:{port}/", "pid": proc.pid}
    assert controller.process("witness") is proc
    assert alive(proc.pid)
    [(name, t, attrs)] = controller.recorder.events
    assert name == "process_ready"
    assert attrs == {"process": "witness", "restart": False, "announced": proc.announced,
                     "ready_ns": t - 10}
    assert (tmp_path / "witness.log").read_text().startswith("--- witness start 1 ---\n")


async def test_a_role_that_never_announces_is_refused_and_stopped(controller, tmp_path):
    with pytest.raises(StartupError) as caught:
        await controller.start("mute", FAKE, ("silent",), timeout=0.5)
    assert caught.value.code == E_STARTUP
    assert isinstance(caught.value, BenchError)
    assert "did not announce itself" in caught.value.message
    assert await gone(logged_pid(tmp_path / "mute.log"))
    assert controller.recorder.events == []


async def test_a_role_that_exits_before_announcing_is_refused(controller, tmp_path):
    with pytest.raises(StartupError, match="did not announce itself"):
        await controller.start("quitter", FAKE, ("exit",), timeout=5)


@pytest.mark.parametrize("mode", ["garbage", "list"])
async def test_an_announcement_that_is_not_a_json_object_is_refused(controller, tmp_path, mode):
    with pytest.raises(StartupError, match="not a JSON object") as caught:
        await controller.start("odd", FAKE, (mode,), timeout=5)
    assert caught.value.code == E_STARTUP
    assert await gone(logged_pid(tmp_path / "odd.log"))


async def test_a_validator_can_refuse_an_announcement(controller, tmp_path):
    def needs_url(announced):
        if not announced.get("url"):
            raise ValueError("it names no url")

    with pytest.raises(StartupError, match="malformed announcement \\(it names no url\\)"):
        await controller.start("bare", FAKE, ("ok",), validate=needs_url, timeout=5)
    assert await gone(logged_pid(tmp_path / "bare.log"))


async def test_a_ready_probe_waits_for_a_listener_that_binds_late(controller):
    port = free_port()
    polls = []

    async def listening(announced):
        assert announced["url"] == f"http://127.0.0.1:{port}/"
        while True:
            polls.append(1)
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                await asyncio.sleep(0.05)
                continue
            writer.close()
            return

    await controller.start("late", FAKE, ("ok", "--port", str(port), "--bind-delay", "0.4"),
                           ready=listening, timeout=5)
    assert len(polls) > 1
    assert controller.recorder.events[-1][0] == "process_ready"


async def test_a_ready_probe_that_never_succeeds_times_out(controller, tmp_path):
    async def never(announced):
        await asyncio.sleep(3600)

    with pytest.raises(StartupError, match="was not ready within") as caught:
        await controller.start("stuck", FAKE, ("ok",), ready=never, timeout=0.5)
    assert caught.value.code == E_STARTUP
    assert await gone(logged_pid(tmp_path / "stuck.log"))
    assert controller.recorder.events == []


async def test_a_ready_probe_that_raises_is_a_startup_failure(controller, tmp_path):
    async def broken(announced):
        raise ConnectionRefusedError("nobody home")

    with pytest.raises(StartupError, match="readiness probe failed \\(nobody home\\)"):
        await controller.start("broken", FAKE, ("ok",), ready=broken, timeout=5)
    assert await gone(logged_pid(tmp_path / "broken.log"))


async def test_a_killed_role_is_reaped_and_recorded(controller):
    proc = await controller.start("holder", FAKE, ("ok",), timeout=5)
    await controller.kill("holder")
    assert not alive(proc.pid)
    name, t, attrs = controller.recorder.events[-1]
    assert (name, attrs) == ("process_killed", {"process": "holder"})
    assert t > controller.recorder.events[0][1]


async def test_killing_a_role_that_is_already_dead_still_records_it(controller):
    proc = await controller.start("holder", FAKE, ("ok",), timeout=5)
    await controller.kill("holder")
    await controller.kill("holder")
    assert [e[0] for e in controller.recorder.events] == [
        "process_ready", "process_killed", "process_killed"]
    assert not alive(proc.pid)


async def test_a_restarted_role_comes_back_on_the_same_address(controller, tmp_path):
    port = free_port()
    first = await controller.start("verifier", FAKE, ("ok", "--port", str(port)),
                                   env={"ROLE_FLAVOUR": "x"}, timeout=5)
    await controller.kill("verifier")
    second = await controller.restart("verifier")
    assert second.pid != first.pid
    assert second.announced["url"] == first.announced["url"]
    assert controller.process("verifier") is second
    name, _, attrs = controller.recorder.events[-1]
    assert name == "process_ready"
    assert attrs["restart"] is True and attrs["announced"] == second.announced
    log = (tmp_path / "verifier.log").read_text()
    assert "--- verifier start 1 ---" in log and "--- verifier start 2 ---" in log
    assert log.count("pid ") == 2


async def test_restarting_a_live_role_kills_it_first(controller):
    first = await controller.start("issuer", FAKE, ("ok",), timeout=5)
    second = await controller.restart("issuer")
    assert not alive(first.pid) and alive(second.pid)
    assert [e[0] for e in controller.recorder.events] == [
        "process_ready", "process_killed", "process_ready"]


async def test_stop_all_stops_everything_even_a_role_that_ignores_sigterm(tmp_path):
    ctl = ProcessController(tmp_path)
    polite = await ctl.start("polite", FAKE, ("ok",), timeout=5)
    stubborn = await ctl.start("stubborn", FAKE, ("ok", "--ignore-term"), timeout=5)
    await ctl.stop_all(timeout=0.5)
    assert not alive(polite.pid) and not alive(stubborn.pid)
    assert polite.proc.returncode == -signal.SIGTERM
    assert stubborn.proc.returncode == -signal.SIGKILL
    await ctl.stop_all()


async def test_an_unknown_name_is_refused(controller):
    for action in (controller.kill, controller.restart, controller.process):
        with pytest.raises(BenchError) as caught:
            result = action("ghost")
            if asyncio.iscoroutine(result):
                await result
        assert caught.value.code == E_UNKNOWN
        assert "no process named 'ghost'" in caught.value.message


async def test_starting_a_name_that_is_running_is_refused(controller):
    await controller.start("pool", FAKE, ("ok",), timeout=5)
    with pytest.raises(BenchError) as caught:
        await controller.start("pool", FAKE, ("ok",), timeout=5)
    assert caught.value.code == E_DUPLICATE


async def test_a_name_whose_process_died_can_be_started_afresh(controller):
    await controller.start("pool", FAKE, ("ok",), timeout=5)
    await controller.kill("pool")
    again = await controller.start("pool", FAKE, ("ok",), timeout=5)
    assert controller.recorder.events[-1][2]["restart"] is False
    assert alive(again.pid)


# --- an exit the harness did not cause (this.i 9zgpyjxt) ----------------------------------------

async def exited(controller, within: float = 5.0):
    for _ in range(int(within / 0.02)):
        found = [e for e in controller.recorder.events if e[0] == "process_exited"]
        if found:
            return found
        await asyncio.sleep(0.02)
    return []


async def test_a_role_that_exits_on_its_own_is_recorded_with_its_pid_code_and_time(controller):
    proc = await controller.start("witness_pool.w0", FAKE,
                                  ("ok", "--exit-after", "0.2", "--exit-code", "7"))
    [(name, t, attrs)] = await exited(controller)
    assert attrs["process"] == "witness_pool.w0" and attrs["pid"] == proc.pid
    assert attrs["code"] == 7
    assert t > controller.recorder.events[0][1]
    at = datetime.fromisoformat(attrs["at"])
    assert at.tzinfo is not None
    assert abs((datetime.now(UTC) - at).total_seconds()) < 30
    assert controller.unexpected_exits == [attrs]


async def test_a_role_killed_from_outside_the_harness_is_recorded_with_the_signal(controller):
    proc = await controller.start("issuer", FAKE, ("ok",))
    os.killpg(proc.pid, signal.SIGKILL)
    [(_, _, attrs)] = await exited(controller)
    assert attrs["code"] == -signal.SIGKILL and attrs["process"] == "issuer"


async def test_kills_restarts_and_stop_all_are_not_unexpected(controller):
    await controller.start("holder", FAKE, ("ok",))
    await controller.start("verifier", FAKE, ("ok",))
    await controller.kill("holder")
    await controller.restart("holder")
    await controller.restart("verifier")
    await controller.stop_all(timeout=0.5)
    await asyncio.sleep(0.2)
    assert "process_exited" not in [e[0] for e in controller.recorder.events]
    assert controller.unexpected_exits == []


async def test_a_role_that_died_can_be_restarted_and_its_next_stop_is_intended(controller):
    first = await controller.start("observer", FAKE, ("ok", "--exit-after", "0.1"))
    assert len(await exited(controller)) == 1
    second = await controller.restart("observer")
    assert second.pid != first.pid
    await controller.stop_all(timeout=0.5)
    await asyncio.sleep(0.2)
    assert [e[2]["pid"] for e in controller.recorder.events if e[0] == "process_exited"] == [
        first.pid]
    assert [e[0] for e in controller.recorder.events] == [
        "process_ready", "process_exited", "process_ready"]


async def test_spawn_alone_stops_cleanly(tmp_path):
    proc = await spawn("solo", FAKE, ("ok",), run_dir=tmp_path, timeout=5)
    await proc.stop()
    await proc.stop()
    assert not alive(proc.pid)


DRIVER = """
import asyncio, json, sys
from pathlib import Path
from keri_bench.procs import ProcessController

async def main():
    ctl = ProcessController(Path(sys.argv[1]))
    proc = await ctl.start("child", tuple(sys.argv[2:]), ("ok",), timeout=10)
    print(json.dumps(proc.pid), flush=True)
    await asyncio.sleep(3600)

asyncio.run(main())
"""


async def test_children_die_with_a_harness_that_is_killed(tmp_path):
    driver = await asyncio.create_subprocess_exec(
        sys.executable, "-c", DRIVER, str(tmp_path), *FAKE, stdout=asyncio.subprocess.PIPE)
    pid = json.loads(await asyncio.wait_for(driver.stdout.readline(), 10))
    assert alive(pid)
    driver.send_signal(signal.SIGKILL)
    await driver.wait()
    assert await gone(pid), "a killed harness must not orphan its roles"


async def test_output_after_the_announcement_is_drained_into_the_log(controller, tmp_path):
    # A pipe holds about 64 KiB; a role that keeps writing to stdout must never block on it.
    marker = tmp_path / "wrote-it-all"
    await controller.start("chatty", FAKE, ("ok", "--chatter", str(1 << 20),
                                             "--marker", str(marker)))
    for _ in range(100):
        if marker.exists():
            break
        await asyncio.sleep(0.05)
    assert marker.exists()
    await controller.stop_all()
    assert "x" * 1000 in (tmp_path / "chatty.log").read_text()


async def test_the_controller_lists_its_process_names(controller):
    await controller.start("one", FAKE, ("ok",))
    await controller.start("two", FAKE, ("ok",))
    assert controller.names() == ["one", "two"]
