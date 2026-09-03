"""Ranking and the per-source cap: the two steps between "found" and "printed".

Why this file exists: until these landed, the order of the output was the order
of publication, and the slice at max_results was the whole of the selection
policy. Both new steps sit on the paid path -- whatever survives them is what
gets crawled, marked seen and shown to readers -- so the failure that matters is
not "ranking is imperfect" but "ranking failed and took the day's post with it".
Hence the emphasis below on the fallbacks.

The ranker is an external command, so these tests spawn a real one: a few lines
of Python written into tmp_path. That keeps the protocol itself under test --
the tab-separated payload, the numbers coming back -- instead of a mock that
would agree with whatever the code happens to send.
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
import fetch_news  # noqa: E402

# (title, source, date, url) -- the shape rank_pool and apply_source_quota pass
# around. Sources repeat on purpose: the cap has nothing to bite on otherwise.
POOL = [
    ("Big lab ships a model", "TechCrunch AI", "03.09", "http://x/1"),
    ("Local firm adopts an assistant", "CNews", "03.09", "http://x/2"),
    ("Regulator drafts AI rules", "BBC Tech", "03.09", "http://x/3"),
    ("Another vendor press release", "CNews", "03.09", "http://x/4"),
    ("A third vendor press release", "CNews", "03.09", "http://x/5"),
    ("Show HN: a small tool", "HN LLM", "02.09", "http://x/6"),
    ("A fourth vendor press release", "CNews", "02.09", "http://x/7"),
]


def _ranker(tmp_path, body, name="ranker.py"):
    """Write a ranker and return the argv that runs it."""
    p = tmp_path / name
    p.write_text("import sys\n" + body + "\n")
    return [sys.executable, str(p)]


def test_no_rank_cmd_leaves_the_order_alone():
    assert fetch_news.rank_pool(list(POOL), {}) == POOL


def test_picked_items_come_first_and_the_rest_keep_their_order(tmp_path):
    # Picks 2 and 0, in that order; everything else must follow untouched.
    cmd = _ranker(tmp_path, "sys.stdin.read()\nprint('2')\nprint('0')")
    out = fetch_news.rank_pool(list(POOL), {"rank_cmd": cmd})
    assert out[:2] == [POOL[2], POOL[0]]
    # The tail is the point: the cap on a source can drop part of the picks,
    # and max_results is filled from what follows.
    assert out[2:] == [it for it in POOL if it not in (POOL[0], POOL[2])]


def test_the_payload_carries_index_title_source_date_and_url(tmp_path):
    # Echoes back only the rows whose five fields arrived intact, so a change to
    # the wire format shows up here rather than as a quietly worse post.
    cmd = _ranker(tmp_path, """
rows = [l.split('\\t') for l in sys.stdin.read().splitlines()]
for r in rows:
    if len(r) == 5 and r[0].isdigit() and r[1] and r[2] and r[3] and r[4]:
        print(r[0])
""")
    assert fetch_news.rank_pool(list(POOL), {"rank_cmd": cmd}) == POOL


@pytest.mark.parametrize("body,why", [
    ("sys.stdin.read()\nsys.exit(3)", "ненулевой код"),
    ("sys.stdin.read()", "пустой вывод"),
    ("sys.stdin.read()\nprint('не число')", "мусор вместо номеров"),
    ("sys.stdin.read()\nprint('99')", "номера вне диапазона"),
])
def test_a_broken_ranker_falls_back_to_the_incoming_order(tmp_path, body, why):
    """The one failure that must never cost a post.

    Every branch here ends at the same place: the order the collector already
    had. A day of ranking lost is a worse post; a day of the collector raising
    is no post at all, and the channel goes quiet with no signal.
    """
    cmd = _ranker(tmp_path, body)
    assert fetch_news.rank_pool(list(POOL), {"rank_cmd": cmd}) == POOL, why


def test_a_missing_ranker_falls_back_too():
    cmd = [str(Path("/nonexistent") / "ranker")]
    assert fetch_news.rank_pool(list(POOL), {"rank_cmd": cmd}) == POOL


def test_a_hanging_ranker_is_cut_off_and_falls_back(tmp_path):
    cmd = _ranker(tmp_path, "import time\nsys.stdin.read()\ntime.sleep(30)")
    cfg = {"rank_cmd": cmd, "rank_timeout": 1}
    assert fetch_news.rank_pool(list(POOL), cfg) == POOL


def test_repeated_numbers_are_taken_once(tmp_path):
    cmd = _ranker(tmp_path, "sys.stdin.read()\nprint('1')\nprint('1')\nprint('1')")
    out = fetch_news.rank_pool(list(POOL), {"rank_cmd": cmd})
    assert out[0] == POOL[1]
    assert len(out) == len(POOL), "дубль в ответе не должен размножать новость"


def test_the_cap_drops_the_surplus_and_keeps_the_order():
    out = fetch_news.apply_source_quota(list(POOL), {"max_per_source": {"CNews": 2}})
    assert [it[1] for it in out].count("CNews") == 2
    # Which two: the first two, and everything else stays where it was.
    assert out == [POOL[0], POOL[1], POOL[2], POOL[3], POOL[5]]


def test_an_uncapped_source_is_untouched():
    out = fetch_news.apply_source_quota(list(POOL), {"max_per_source": {"CNews": 99}})
    assert out == POOL
    assert fetch_news.apply_source_quota(list(POOL), {}) == POOL


# --- через main(), где обе ступени встречаются с max_results и seen ---

FEED = [
    ("Big lab ships a model", "TechCrunch AI"),
    ("Local firm adopts an assistant", "CNews"),
    ("Regulator drafts AI rules", "BBC Tech"),
    ("Another vendor press release", "CNews"),
    ("A third vendor press release", "CNews"),
    ("Show HN: a small tool", "HN LLM"),
]


def _cfg(tmp_path, db, extra):
    lines = [
        f"db_path: {db}",
        "max_hours: 999999",
        "max_results: 3",
        "feeds:",
    ]
    for src in dict.fromkeys(s for _, s in FEED):
        lines += [f"  - name: {src}", f"    url: http://example.invalid/{src}"]
    lines.append("full_ai_feeds: [" + ", ".join(f'"{s}"' for _, s in FEED) + "]")
    lines += extra
    p = tmp_path / "c.yaml"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def _run(cfg, monkeypatch):
    stamp = formatdate(localtime=False)
    monkeypatch.setattr(
        fetch_news, "fetch_rss",
        lambda url, cfg_, limit=None: [
            (t, f"http://example.invalid/{i}", stamp, [])
            for i, (t, src) in enumerate(FEED) if url.endswith(src)
        ],
        raising=False,
    )
    monkeypatch.setattr(fetch_news, "crawl_tavily", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(sys, "argv", ["x", "--config", cfg, "--no-crawl"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fetch_news.main()
    return buf.getvalue()


def _seen(db):
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def test_the_cap_holds_all_the_way_to_what_gets_printed(tmp_path, monkeypatch):
    """The regression worth guarding: a cap that the final slice undoes.

    Ranking runs over the whole pool and the cap over its result, but the post
    is the slice at max_results. Applying either one on the wrong side of that
    slice puts the surplus back on the page.
    """
    db = tmp_path / "seen.db"
    # Ранжировщик поднимает наверх все CNews — без квоты они заняли бы все три
    # места, ровно как утренняя пачка занимала их на живом прогоне.
    cmd = _ranker(tmp_path, """
rows = [l.split('\\t') for l in sys.stdin.read().splitlines()]
for r in rows:
    if r[2] == 'CNews':
        print(r[0])
for r in rows:
    if r[2] != 'CNews':
        print(r[0])
""")
    extra = ["max_per_source:", "  CNews: 1",
             "rank_cmd:"] + [f"  - {c}" for c in cmd]
    out = _run(_cfg(tmp_path, db, extra), monkeypatch)

    printed = [t for t, _ in FEED if t in out]
    assert len(printed) == 3, "max_results обязан ограничивать выдачу"
    cnews = [t for t, src in FEED if src == "CNews" and t in out]
    assert len(cnews) == 1, "квота обязана дожить до печати"
    # И прежний инвариант: помечено ровно напечатанное, остальное вернётся.
    assert _seen(db) == 3


def test_a_broken_ranker_still_produces_a_post(tmp_path, monkeypatch):
    db = tmp_path / "seen.db"
    cmd = _ranker(tmp_path, "sys.exit(1)")
    extra = ["rank_cmd:"] + [f"  - {c}" for c in cmd]
    out = _run(_cfg(tmp_path, db, extra), monkeypatch)
    assert "СТАТЬИ (заголовок | источник | дата | url):" in out
    assert len([t for t, _ in FEED if t in out]) == 3
    assert _seen(db) == 3
