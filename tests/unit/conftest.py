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

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

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


def _literal_constants(path: Path) -> dict[str, object]:
    """Return the literal module-level constants defined in a source file.

    ``const.py`` cannot be imported in tier 1 - it pulls in
    ``homeassistant.const.Platform`` - but the values tier 1 needs to pin down
    are all plain literals. Reading them out of the parse tree keeps the
    assertions against the *shipped source* without standing up Home
    Assistant, and without a stub module that could drift from the real one.

    Only simple ``NAME = <literal>`` assignments are returned; anything else
    (the ``Platform`` lists, f-strings, comprehensions) is skipped.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            constants[target.id] = ast.literal_eval(node.value)
        except ValueError:
            continue
    return constants


@pytest.fixture(scope="session")
def telemetry() -> ModuleType:
    """Return the component's API telemetry module."""
    return _load("telemetry")


@pytest.fixture(scope="session")
def const() -> SimpleNamespace:
    """Return the component's literal constants as attributes."""
    return SimpleNamespace(**_literal_constants(COMPONENT_DIR / "const.py"))


@pytest.fixture(scope="session")
def component_dir() -> Path:
    """Return the component's source directory.

    Used by the packaging and translation tests, which read the shipped files
    as data rather than importing them.
    """
    return COMPONENT_DIR
