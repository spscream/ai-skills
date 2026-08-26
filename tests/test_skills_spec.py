"""Validate every SKILL.md in the repo against the Agent Skills open spec:
requires YAML frontmatter with `name` + `description`; name matches folder
and is lowercase+hyphens. No network needed."""
import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIRS = sorted(p for p in (_ROOT / "skills").iterdir() if p.is_dir())


def _frontmatter(skill_dir):
    p = skill_dir / "SKILL.md"
    assert p.is_file(), f"{skill_dir.name}: missing SKILL.md"
    content = p.read_text()
    assert content.startswith("---"), f"{skill_dir.name}: SKILL.md must start with '---'"
    m = re.search(r"\n---\s*\n", content[3:])
    assert m, f"{skill_dir.name}: frontmatter not closed"
    return yaml.safe_load(content[3:m.start() + 3]), content


def test_every_skill_has_valid_frontmatter():
    assert SKILL_DIRS, "no skills/ directories found"
    for skill_dir in SKILL_DIRS:
        fm, _ = _frontmatter(skill_dir)
        assert fm.get("name") == skill_dir.name, \
            f"{skill_dir.name}: frontmatter 'name' must equal folder name"
        assert re.fullmatch(r"[a-z0-9-]+", fm.get("name", "")), \
            f"{skill_dir.name}: 'name' must be lowercase+hyphens"
        desc = fm.get("description", "")
        assert desc, f"{skill_dir.name}: 'description' is required"
        assert len(desc) <= 1024, f"{skill_dir.name}: description too long"


def test_every_skill_has_body():
    for skill_dir in SKILL_DIRS:
        _, content = _frontmatter(skill_dir)
        m = re.search(r"\n---\s*\n", content[3:])
        body = content[m.end() + 3:]
        assert body.strip(), f"{skill_dir.name}: empty body after frontmatter"


def test_skill_names_unique():
    names = [skill_dir.name for skill_dir in SKILL_DIRS]
    assert len(names) == len(set(names)), "duplicate skill folder names"