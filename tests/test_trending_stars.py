"""The star threshold on GitHub finds. No network: the API call is stubbed.

Why it exists: the search asks for repositories created within the last few
days, so they cannot have accumulated stars yet, and the results fill up with
personal templates on zero stars that crowd out the curated search finds. One
week measured: 48 unique repositories, median 7 stars, minimum 0.
"""
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "skills" / "trending-skills" / "scripts"))
import fetch_trending  # noqa: E402


def _stub(monkeypatch, items):
    class _R:
        def __init__(self, payload): self._p = payload
        def read(self): return json.dumps(self._p).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(fetch_trending.urllib.request, "urlopen",
                        lambda req, timeout=None: _R({"items": items}))


REPOS = [
    {"full_name": "big/one", "description": "d", "html_url": "u1", "stargazers_count": 1459},
    {"full_name": "mid/two", "description": "d", "html_url": "u2", "stargazers_count": 23},
    {"full_name": "small/three", "description": "d", "html_url": "u3", "stargazers_count": 9},
    {"full_name": "tiny/four", "description": "d", "html_url": "u4", "stargazers_count": 1},
    {"full_name": "zero/five", "description": "d", "html_url": "u5", "stargazers_count": 0},
]


@pytest.mark.parametrize("threshold,expected", [
    (0, 5),     # off
    (10, 2),    # the configured default
    (25, 1),
    (5000, 0),  # nothing survives; the section simply stays empty
])
def test_threshold_keeps_only_repos_above_it(threshold, expected, monkeypatch):
    cfg = fetch_trending.load_config("/x/none")
    cfg["github_min_stars"] = threshold
    _stub(monkeypatch, REPOS)
    assert len(fetch_trending.github_search("claude-code", 7, cfg)) == expected


def test_default_threshold_is_above_the_measured_median():
    """7 was the median of a measured week; the default must sit above it."""
    cfg = fetch_trending.load_config("/x/none")
    assert cfg["github_min_stars"] > 7


def test_a_repo_without_a_star_count_is_treated_as_zero(monkeypatch):
    cfg = fetch_trending.load_config("/x/none")
    cfg["github_min_stars"] = 1
    _stub(monkeypatch, [{"full_name": "no/count", "description": "d", "html_url": "u"}])
    assert fetch_trending.github_search("claude-code", 7, cfg) == []
