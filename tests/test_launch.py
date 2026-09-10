"""Regression checks for starting only missing local services."""

import pytest
from scripts import launch


def test_recover_missing_backend(monkeypatch):
    monkeypatch.setattr(launch, "healthy", lambda port, backend=False: port == 5173)
    monkeypatch.setattr(launch, "available", lambda port: port == 8000)
    assert launch.service_plan() == [(8000, True)]


def test_running_services_are_reused(monkeypatch):
    monkeypatch.setattr(launch, "healthy", lambda *args: True)
    monkeypatch.setattr(
        launch,
        "available",
        lambda port: pytest.fail("Do not probe or replace a healthy service"),
    )
    assert launch.service_plan() == []


def test_unrelated_port_is_not_replaced(monkeypatch):
    monkeypatch.setattr(launch, "healthy", lambda *args: False)
    monkeypatch.setattr(launch, "available", lambda port: False)
    with pytest.raises(RuntimeError, match="Port 8000 is occupied"):
        launch.service_plan()


def test_production_only_requires_backend(monkeypatch):
    monkeypatch.setattr(launch, "healthy", lambda *args: False)
    monkeypatch.setattr(launch, "available", lambda port: True)
    assert launch.service_plan(production=True) == [(8000, True)]
