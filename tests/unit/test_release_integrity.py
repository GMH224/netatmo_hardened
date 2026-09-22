"""Release-integrity tests.

Two of this project's defects were not in the code at all. `P0-x` shipped an
archive whose files sat one directory too deep, and `E-011` shipped a
translation file that was correct in the source it was copied from and wrong in
the form the runtime reads. Both passed every test and every review, because
everything that looked was looking at the source rather than at what ships.

The release gate added in `AUDIT_0.1.2.md` §7 states the rule as *validate the
shipped artefact, not the source it was derived from*. These tests are the
local half of that gate; `.github/workflows/release.yml` is the other half and
runs on the tag.

Traceability: P0-1, P0-2, E-011, and the ICS requirement that version documents
and tests ship **inside** the deployment package.
"""

from __future__ import annotations

import json
import re

import pytest

REPO_ROOT_MARKERS = ("custom_components", "docs", "tests")


@pytest.fixture(scope="session")
def repo_root(component_dir):
    """Return the repository root."""
    root = component_dir.parents[1]
    for marker in REPO_ROOT_MARKERS:
        assert (root / marker).is_dir(), f"{marker} not found under {root}"
    return root


@pytest.fixture(scope="session")
def manifest(component_dir):
    """Return the shipped manifest."""
    return json.loads((component_dir / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_declares_a_semantic_version(manifest):
    """HACS compares this string to the release tag."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]), manifest["version"]


def test_version_documents_exist_for_the_shipped_version(repo_root, manifest):
    """The release record and audit for *this* version must be in the package.

    ICS requirement: version documents are part of the deployment package, not
    a separate deliverable that can be produced later or not at all. A release
    that cannot point at its own audit has not been audited.
    """
    version = manifest["version"]
    for name in (f"RELEASE_{version}.md", f"AUDIT_{version}.md"):
        assert (repo_root / "docs" / name).is_file(), f"docs/{name} is missing"


def test_changelog_records_the_shipped_version(repo_root, manifest):
    """A version with no changelog entry is a version nobody can review."""
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{manifest['version']}]" in changelog


def test_hacs_floor_is_not_below_the_supported_baseline(repo_root):
    """Guards P0-1 from recurring.

    0.1.0's packaging advertised HA 2024.1.0 while the code required 2026.3+,
    so the integration could not be installed at all on most of the versions it
    claimed. The floor is asserted rather than trusted.
    """
    hacs = json.loads((repo_root / "hacs.json").read_text(encoding="utf-8"))
    major, minor, _ = (int(part) for part in hacs["homeassistant"].split("."))
    assert (major, minor) >= (2026, 9), hacs["homeassistant"]


def test_runtime_translation_file_is_shipped(component_dir):
    """Guards P0-2.

    ``strings.json`` is a core build-time file. A custom integration that ships
    only that renders raw dotted keys in its config flow.
    """
    assert (component_dir / "translations" / "en.json").is_file()


def test_manifest_and_hacs_agree_on_the_name(repo_root, manifest):
    """A mismatch shows one name in HACS and another in the integrations list."""
    hacs = json.loads((repo_root / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"] == manifest["name"]


def test_domain_does_not_shadow_the_built_in_integration(manifest, component_dir):
    """The independent-domain decision is structural, so assert it.

    Reverting to the `netatmo` domain would silently override Home Assistant's
    built-in integration, removing the fallback the whole fork is premised on -
    and it would do so with no error at install time.
    """
    assert manifest["domain"] == "netatmo_hardened"
    assert component_dir.name == "netatmo_hardened"


def test_dependency_pin_is_exact(manifest):
    """pyatmo is pinned, not ranged.

    A range would let a dependency upgrade change control-path behaviour
    between two installations reporting the same integration version.
    """
    assert manifest["requirements"] == ["pyatmo==9.9.0"]


def test_tests_ship_inside_the_package(repo_root):
    """ICS requirement: the test suite is part of the deployment package.

    Not a separate CI concern. An operator receiving the archive must be able
    to run the tier-1 suite against the exact code they were given.
    """
    assert (repo_root / "tests" / "unit").is_dir()
    assert (repo_root / "tests" / "integration").is_dir()
    assert any((repo_root / "tests" / "unit").glob("test_*.py"))
