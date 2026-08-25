"""A skill folder must work on its own.

The README tells the reader to copy one folder out of `skills/` and nothing else.
That promise used to be false: the collectors imported `shared.dedup` from the repo
root, so a copied folder died with ModuleNotFoundError on the first run. These tests
copy each skill folder into a temporary directory, exactly as a reader would, and run
its scripts there.

The check runs the scripts, it does not import them: an import from inside the test
process would find the repository on sys.path and pass while the copy is broken.
No network is needed — every entry point is called with --help or -h.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIRS = sorted(p for p in (_ROOT / "skills").iterdir() if p.is_dir())

# How to ask each kind of script to start up and exit without doing any work.
_HELP_FLAG = {".py": "--help", ".sh": "-h"}


def _entry_points(skill_dir):
    scripts = skill_dir / "scripts"
    if not scripts.is_dir():
        return []
    return sorted(p for p in scripts.iterdir() if p.suffix in _HELP_FLAG and p.is_file())


def _runner(script):
    return [sys.executable, str(script)] if script.suffix == ".py" else ["bash", str(script)]


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_folder_runs_when_copied_alone(skill_dir, tmp_path):
    copied = tmp_path / skill_dir.name
    shutil.copytree(skill_dir, copied, ignore=shutil.ignore_patterns("__pycache__"))

    entry_points = _entry_points(copied)
    assert entry_points, f"{skill_dir.name}: no scripts to check"

    # An empty environment variable set keeps a shared venv or a stray PYTHONPATH from
    # hiding a missing file: the copy has to carry everything it needs.
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("AI_SKILLS_SITE", None)

    for script in entry_points:
        proc = subprocess.run(
            _runner(script) + [_HELP_FLAG[script.suffix]],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, (
            f"{skill_dir.name}/{script.name} fails when the folder is copied alone "
            f"(exit {proc.returncode}):\n{proc.stderr[-2000:]}"
        )


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_vendored_dedup_matches_the_shared_source(skill_dir):
    """A skill that carries dedup.py carries a byte-identical copy of the source.

    Self-containment costs a copy, and a copy drifts. This is the only guard against
    the two files parting ways.
    """
    copy = skill_dir / "scripts" / "dedup.py"
    if not copy.is_file():
        pytest.skip(f"{skill_dir.name} does not use dedup")
    source = _ROOT / "shared" / "dedup.py"
    assert copy.read_bytes() == source.read_bytes(), (
        f"{skill_dir.name}/scripts/dedup.py drifted from shared/dedup.py — "
        f"edit shared/dedup.py and copy it over the vendored files"
    )
