"""Tests for scripts/release.py versioning and changelog logic."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("release", Path(__file__).parent.parent / "scripts" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

CHANGELOG = """# Changelog

Intro.

## [Unreleased]

{unreleased}
## [0.0.1] - 2026-10-01

- First.
"""


@pytest.mark.parametrize(
    ("subjects", "level"),
    [
        (["fix: pool parsing", "docs: readme"], "patch"),
        (["Add disk sensors #minor"], "minor"),
        (["feat(sensor): disk temperature", "fix: x"], "minor"),
        (["feat!: drop 25.04"], "major"),
        (["refactor: client #major", "feat: y"], "major"),
    ],
)
def test_bump_level(subjects: list[str], level: str) -> None:
    assert release.bump_level([(s, "") for s in subjects]) == level


def test_bump_level_breaking_in_body() -> None:
    assert release.bump_level([("refactor: api", "BREAKING CHANGE: new unique ids")]) == "major"


@pytest.mark.parametrize(
    ("level", "expected"),
    [("patch", "0.0.2"), ("minor", "0.1.0"), ("major", "1.0.0")],
)
def test_bump(level: str, expected: str) -> None:
    assert release.bump("v0.0.1", level) == expected


def test_changelog_uses_unreleased_entries() -> None:
    text = CHANGELOG.format(unreleased="### Fixed\n\n- Pool parsing.\n")
    new, notes = release.update_changelog(text, "0.0.2", "2026-10-02", [("fix: ignored", "")])

    assert notes == "### Fixed\n\n- Pool parsing."
    assert "## [Unreleased]\n\n## [0.0.2] - 2026-10-02\n\n### Fixed\n\n- Pool parsing.\n\n## [0.0.1]" in new


def test_changelog_falls_back_to_commits() -> None:
    new, notes = release.update_changelog(CHANGELOG.format(unreleased=""), "0.0.2", "2026-10-02", [("fix: a", "")])
    assert notes == "- fix: a"
    assert new.index("## [0.0.2]") < new.index("## [0.0.1]")
