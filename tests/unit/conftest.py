"""Loader for tier 1 unit tests.

Tier 1 tests exercise the integration's pure logic with no Home Assistant
runtime, so they run on any Python 3.11+ interpreter and in any CI job. That
matters for an ICS deployment: the arithmetic that decides how long a heating
override lasts, and the validation that decides whether a hostile webhook
payload is accepted, should be verifiable without standing up a smart-home
platform first.

The modules are loaded directly from their files rather than imported as
``custom_components.netatmo_hardened.helper``, because importing the package would
execute ``custom_components/netatmo/__init__.py`` and pull in Home Assistant.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

COMPONENT_DIR = (
    Path(__file__).resolve().parents[2] / "custom_components" / "netatmo_hardened"
)


def _load(name: str) -> ModuleType:
    """Import a single component module in isolation."""
    path = COMPONENT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_netatmo_hardened_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def helper() -> ModuleType:
    """Return the component's helper module."""
    return _load("helper")


@pytest.fixture(scope="session")
def validate() -> ModuleType:
    """Return the component's webhook validation module."""
    return _load("event_validation")
