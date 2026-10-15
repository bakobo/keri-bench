import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "calibration: the end-to-end calibration (Part B #1); "
                            "slow, and run on every change in CI")
