"""Shared test fixtures for config isolation."""
import os
import pytest


@pytest.fixture
def isolated_env(monkeypatch):
    """Isolate tests from .env by clearing channel env vars and mocking load_dotenv."""
    monkeypatch.setattr('dotenv.load_dotenv', lambda *a, **k: None)
    for key in ["CHANNEL_max", "CHANNEL_pypi", "CHANNEL_media", "CHANNEL_backup"]:
        monkeypatch.setitem(os.environ, key, "")
