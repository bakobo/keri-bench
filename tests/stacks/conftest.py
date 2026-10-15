"""Tests of the real stacks' processes and adapters. They need the ``stacks`` extra (private
bakobo repositories), so they skip without it; CI runs them in their own job."""

import pytest

pytest.importorskip("heti", reason="the real-stack tests need `uv sync --extra stacks`")
pytest.importorskip("imbu", reason="the real-stack tests need `uv sync --extra stacks`")
