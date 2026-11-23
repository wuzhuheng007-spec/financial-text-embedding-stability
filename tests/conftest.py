"""Waits between provider attempts never really sleep in tests; `sleeps` lists what was asked for."""
import pytest


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    try:
        from scripts.x_stance_v1 import provider
    except Exception:  # noqa: BLE001 - test modules that do not use this package
        yield []
        return
    asked = []
    monkeypatch.setattr(provider, "SLEEP", lambda s: asked.append(round(float(s), 3)))
    yield asked
