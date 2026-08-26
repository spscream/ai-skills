"""main() of each collector, end to end, without network.

Why this file exists: every other test either checks a pure function or starts a
script with --help, which returns before the body of main() runs. So the whole
collecting loop -- dedup, the call that marks an item as seen, the max_results
slice, the output format -- shipped untested. A wrong name inside main() passed
the entire suite and only failed on a real run.

The network is replaced by a stub, so these tests are as fast and as offline as
the rest.
"""
import io
import contextlib
import sqlite3
import sys
from email.utils import formatdate
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "skills" / "ai-daily" / "scripts"))
sys.path.insert(0, str(_ROOT / "skills" / "trending-skills" / "scripts"))
import fetch_news  # noqa: E402
import fetch_trending  # noqa: E402

# Distinct on purpose: near-identical headlines collapse under fuzzy dedup and
# would hide whatever the test means to measure.
TITLES = [
    "OpenAI ships a Sora update",
    "Anthropic closes a funding round",
    "Mistral opens a lab in Paris",
    "A robotics firm wins a defence contract",
    "Chip export rules tighten again",
    "A quantum startup raises a seed round",
    "Datacenter power crunch worsens",
    "The EU drafts a liability directive",
    "A voice cloning lawsuit is filed",
    "The benchmark suite gets a rewrite",
]


def _cfg_file(tmp_path, db_path, **extra):
    lines = [f"db_path: {db_path}"]
    for k, v in extra.items():
        lines.append(f"{k}: {v}")
    lines += [
        "max_hours: 999999",
        # ai-daily reads feeds; trending-skills reads queries. Both collectors
        # loop over their own list, so each needs one entry or the stubbed
        # search is never called and the run looks empty for the wrong reason.
        "feeds:",
        "  - name: FAKE",
        "    url: http://example.invalid/feed",
        'full_ai_feeds: ["FAKE"]',
        "queries:",
        '  - {"q": "fake query", "domain": null}',
        "github_topics: []",
    ]
    p = tmp_path / "c.yaml"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def _run(module, argv, monkeypatch, titles=TITLES):
    """Run main() with the network stubbed out; return stdout."""
    stamp = formatdate(localtime=False)
    monkeypatch.setattr(
        module, "fetch_rss",
        # Four fields: fetch_rss also carries the categories the rules read.
        lambda url, cfg: [(t, f"http://example.invalid/{i}", stamp, [])
                          for i, t in enumerate(titles)],
        raising=False,
    )
    monkeypatch.setattr(
        module, "tavily_search",
        lambda q, domain, days, cfg: [(t, f"http://example.invalid/{i}", "")
                                      for i, t in enumerate(titles)],
        raising=False,
    )
    monkeypatch.setattr(module, "github_search", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(module, "crawl_tavily", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(sys, "argv", argv)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        module.main()
    return buf.getvalue()


def _seen_count(db_path):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


COLLECTORS = [
    pytest.param(fetch_news, "СТАТЬИ (заголовок | источник | дата | url):", id="ai-daily"),
    pytest.param(fetch_trending, "СТАТЬИ (заголовок | источник | url):", id="trending-skills"),
]


@pytest.mark.parametrize("module,header", COLLECTORS)
def test_main_runs_and_emits_items(module, header, tmp_path, monkeypatch):
    db = tmp_path / "seen.db"
    cfg = _cfg_file(tmp_path, db)
    out = _run(module, ["x", "--config", cfg, "--no-crawl"], monkeypatch)
    assert header in out
    for t in TITLES:
        assert t in out
    assert _seen_count(db) == len(TITLES)


@pytest.mark.parametrize("module,header", COLLECTORS)
def test_dry_run_emits_the_same_items_but_writes_nothing(module, header, tmp_path, monkeypatch):
    db = tmp_path / "seen.db"
    cfg = _cfg_file(tmp_path, db)
    first = _run(module, ["x", "--config", cfg, "--no-crawl", "--dry-run"], monkeypatch)
    assert _seen_count(db) == 0
    # A dry run must not consume anything: a second one sees the same items.
    second = _run(module, ["x", "--config", cfg, "--no-crawl", "--dry-run"], monkeypatch)
    assert first == second
    assert _seen_count(db) == 0


@pytest.mark.parametrize("module,header", COLLECTORS)
def test_a_real_run_consumes_what_it_emitted(module, header, tmp_path, monkeypatch):
    db = tmp_path / "seen.db"
    cfg = _cfg_file(tmp_path, db)
    _run(module, ["x", "--config", cfg, "--no-crawl"], monkeypatch)
    again = _run(module, ["x", "--config", cfg, "--no-crawl"], monkeypatch)
    for t in TITLES:
        assert t not in again


@pytest.mark.parametrize("module,header", COLLECTORS)
def test_only_emitted_items_are_marked_seen(module, header, tmp_path, monkeypatch):
    """The regression this file was written for.

    max_results truncates the output. Marking every candidate rather than every
    emitted item silently destroys the remainder: it is recorded as already sent
    and no later run will ever show it.
    """
    db = tmp_path / "seen.db"
    cfg = _cfg_file(tmp_path, db, max_results=3)
    out = _run(module, ["x", "--config", cfg, "--no-crawl"], monkeypatch)

    emitted = [t for t in TITLES if t in out]
    assert len(emitted) == 3, "max_results must bound the output"
    assert _seen_count(db) == 3, "an item that was not emitted must not be marked seen"

    # The remaining seven survive for the next run rather than vanishing.
    again = _run(module, ["x", "--config", cfg, "--no-crawl"], monkeypatch)
    assert [t for t in TITLES if t in again], "the untruncated remainder was lost"
