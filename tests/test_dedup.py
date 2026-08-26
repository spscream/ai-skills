"""Unit tests for shared/dedup.py (no network required)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import dedup


# --- norm / title_hash ---

def test_norm_lowercases_and_strips():
    assert dedup.norm("  OpenAI GPT-5.  ") == "openai gpt-5"
    assert dedup.norm("CLAUDE") == "claude"


def test_title_hash_stable():
    assert dedup.title_hash("Same Title") == dedup.title_hash("Same Title")
    assert dedup.title_hash("Same Title") != dedup.title_hash("Different Title")


# --- is_dup: exact + RapidFuzz fuzzy ---

def test_exact_duplicate_detected():
    assert dedup.is_dup("OpenAI launches GPT-5", ["OpenAI launches GPT-5"])


def test_reworded_title_is_dup():
    # strong rewordings (partial_ratio ~>65) collapse
    assert dedup.is_dup("OpenAI released GPT-5 today",
                        ["OpenAI launches GPT-5 for everyone"])
    assert dedup.is_dup("Claude 4 improves coding",
                        ["Claude 4 improved code writing"])


def test_loosely_reworded_dup_true():
    # at/above threshold: treated as duplicate
    assert dedup.is_dup("Meta trains new LLM on GPU", ["Meta trained a fresh model"])


def test_below_threshold_not_dup():
    # "AI startup raises $50M" vs "Startup secures funding" scores ~63 (<65) -> NOT dup
    assert not dedup.is_dup("AI startup raises $50M",
                            ["Startup secures funding for AI project"])


def test_clearly_different_not_dup():
    assert not dedup.is_dup(
        "Nvidia releases gaming GPU aimed at esports",
        ["Google launches a new browser"])


def test_russian_distinct_not_dup():
    # sub-65 partial ratio -> not a duplicate
    assert not dedup.is_dup("Сбер запустил свой нейросетевой сервис",
                        ["Сбер представил ИИ-модель для банка"])


def test_empty_known_never_dup():
    assert not dedup.is_dup("Anything", [])


def test_short_title_no_crash():
    assert dedup.is_dup("AI", []) in (True, False)

# --- remember() / --dry-run ---------------------------------------------

def _mem_cur():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE seen (added_ts INTEGER, h TEXT PRIMARY KEY, title TEXT)")
    return c, c.cursor()


def test_remember_writes_by_default():
    c, cur = _mem_cur()
    assert dedup.remember(cur, "Some AI headline") is True
    cur.execute("SELECT h, title FROM seen")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == dedup.title_hash("Some AI headline")


def test_remember_dry_run_writes_nothing():
    c, cur = _mem_cur()
    assert dedup.remember(cur, "Some AI headline", dry_run=True) is False
    cur.execute("SELECT COUNT(*) FROM seen")
    assert cur.fetchone()[0] == 0


def test_remember_dry_run_leaves_dedup_state_intact():
    """A dry run must not make an item look already-sent to the next real run."""
    c, cur = _mem_cur()
    title = "Anthropic ships a new model"
    dedup.remember(cur, title, dry_run=True)
    cur.execute("SELECT title FROM seen")
    known = [r[0] for r in cur.fetchall()]
    assert dedup.is_dup(title, known) is False   # still unseen
    dedup.remember(cur, title)                   # now a real run consumes it
    cur.execute("SELECT title FROM seen")
    known = [r[0] for r in cur.fetchall()]
    assert dedup.is_dup(title, known) is True


def test_remember_is_idempotent_on_same_title():
    c, cur = _mem_cur()
    dedup.remember(cur, "Repeated headline")
    dedup.remember(cur, "Repeated headline")
    cur.execute("SELECT COUNT(*) FROM seen")
    assert cur.fetchone()[0] == 1
