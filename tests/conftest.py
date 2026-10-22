"""Shared test setup."""

from pathlib import Path

import dotenv
import pytest

from content_pipeline import config, image_backends

REPO_ENV = (Path(__file__).resolve().parents[1] / ".env").resolve()
REAL_DOTENV_VALUES = dotenv.dotenv_values
PROVIDER_VARIABLES = [
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "TAVILY_API_KEY",
]


def _without_repo_env(path=None, *args, **kwargs):
    """Read any .env a test writes, but never the developer's real keys in the repo root."""
    if path is not None and Path(path).resolve() == REPO_ENV:
        return {}

    return REAL_DOTENV_VALUES(path, *args, **kwargs)


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    """Keep real API keys out of tests, so a filled-in .env cannot trigger live calls."""
    monkeypatch.setattr(dotenv, "dotenv_values", _without_repo_env)
    monkeypatch.setattr(config, "dotenv_values", _without_repo_env)
    monkeypatch.setattr(image_backends, "dotenv_values", _without_repo_env)

    for name in PROVIDER_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("SEARCH_BACKEND", "fixture")


@pytest.fixture(autouse=True)
def advisor_on_for_pipeline_runs(monkeypatch):
    """The advisor is off by default; the pipeline tests exercise it, so they turn it on."""
    monkeypatch.setenv("ADVISOR_ENABLED", "true")
