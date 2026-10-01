#!/usr/bin/env python3
"""Prepare a release: pick the next version, update version files and CHANGELOG.md.

Run by .github/workflows/release.yml on every push to main. Stdlib only.

Version rules:
- No `v*` tag yet: release the version already in manifest.json (first release).
- Otherwise bump the latest tag based on commits since it:
  `#major` or `BREAKING CHANGE` -> major, `feat` / `#minor` -> minor, anything else -> patch.
- No new commits since the latest tag: nothing to release.

Changelog: entries under `## [Unreleased]` become the new version's notes. If that
section is empty, notes are generated from commit subjects.

Writes `version=` and `released=` to $GITHUB_OUTPUT and the notes to --notes-file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "custom_components" / "truenas_lightweight" / "manifest.json"
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"

RELEASE_COMMIT_PREFIX = "chore(release):"
UNRELEASED = "## [Unreleased]"
FEAT_RE = re.compile(r"^feat(\(.+\))?!?:", re.IGNORECASE)
BREAKING_RE = re.compile(r"^\w+(\(.+\))?!:")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def latest_tag() -> str | None:
    try:
        return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    except subprocess.CalledProcessError:
        return None


def commits_since(tag: str | None) -> list[tuple[str, str]]:
    """(subject, body) of non-merge, non-release commits since tag."""
    rev = f"{tag}..HEAD" if tag else "HEAD"
    raw = git("log", rev, "--no-merges", "--format=%s%x1f%b%x1e")
    commits = []
    for entry in raw.split("\x1e"):
        if not entry.strip():
            continue
        subject, _, body = entry.strip().partition("\x1f")
        if not subject.startswith(RELEASE_COMMIT_PREFIX):
            commits.append((subject.strip(), body.strip()))
    return commits


def bump_level(commits: list[tuple[str, str]]) -> str:
    level = "patch"
    for subject, body in commits:
        text = f"{subject}\n{body}"
        if "#major" in text or "BREAKING CHANGE" in text or BREAKING_RE.match(subject):
            return "major"
        if "#minor" in text or FEAT_RE.match(subject):
            level = "minor"
    return level


def bump(version: str, level: str) -> str:
    major, minor, patch = (int(p) for p in version.lstrip("v").split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def split_unreleased(changelog: str) -> tuple[str, str, str]:
    """Return (text before Unreleased body, Unreleased body, rest starting at next version)."""
    start = changelog.index(UNRELEASED) + len(UNRELEASED)
    next_section = re.search(r"^## \[", changelog[start:], re.MULTILINE)
    end = start + next_section.start() if next_section else len(changelog)
    return changelog[:start], changelog[start:end].strip(), changelog[end:]


def update_changelog(changelog: str, version: str, date: str, commits: list[tuple[str, str]]) -> tuple[str, str]:
    """Move Unreleased entries (or commit subjects) under a new version heading. Returns (changelog, notes)."""
    head, unreleased, rest = split_unreleased(changelog)
    notes = unreleased or "\n".join(f"- {subject}" for subject, _ in commits) or "- Maintenance release"
    new = f"{head}\n\n## [{version}] - {date}\n\n{notes}\n\n{rest.lstrip()}"
    return new.rstrip() + "\n", notes


def set_versions(version: str) -> None:
    manifest = json.loads(MANIFEST.read_text())
    manifest["version"] = version
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    PYPROJECT.write_text(
        re.sub(r'^version = ".*"$', f'version = "{version}"', PYPROJECT.read_text(), count=1, flags=re.MULTILINE)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--notes-file", type=Path, default=ROOT / "release_notes.md")
    parser.add_argument("--dry-run", action="store_true", help="print the decision without writing files")
    args = parser.parse_args()

    tag = latest_tag()
    commits = commits_since(tag)
    if tag is None:
        version = json.loads(MANIFEST.read_text())["version"]
    elif not commits:
        version = None
    else:
        version = bump(tag, bump_level(commits))

    if version is None:
        print(f"No changes since {tag}; nothing to release.")
    elif args.dry_run:
        print(f"Would release {version} (previous: {tag or 'none'}, {len(commits)} commits).")
    else:
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        changelog, notes = update_changelog(CHANGELOG.read_text(), version, date, commits)
        CHANGELOG.write_text(changelog)
        set_versions(version)
        args.notes_file.write_text(notes + "\n")
        print(f"Prepared release {version}.")

    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as fh:
            fh.write(f"version={version or ''}\nreleased={'true' if version and not args.dry_run else 'false'}\n")


if __name__ == "__main__":
    main()
