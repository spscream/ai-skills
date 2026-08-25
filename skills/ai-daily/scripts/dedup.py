"""Shared fuzzy dedup helpers for the ai-skills collectors.

Exact-hash plus RapidFuzz partial_ratio (threshold ~65), with a
token-overlap fallback when RapidFuzz is not installed.

This file is the source. Each collector skill carries a byte-identical copy in its
own `scripts/` directory, so that copying one skill folder gives a working skill.
`tests/test_vendored_dedup.py` fails when a copy drifts from this file.
"""

import hashlib
import os
import re
import sys

# --- optional rapidfuzz bootstrap ---
_RF = None
try:  # pragma: no cover - import path varies
    from rapidfuzz import fuzz as _RF
except Exception:  # pragma: no cover
    # An escape hatch for an interpreter that cannot see the package: point
    # AI_SKILLS_SITE at the site-packages that has it. No path is guessed —
    # a guessed one is a machine-specific path, which this repository does not use.
    cand = os.environ.get("AI_SKILLS_SITE")
    if cand and os.path.isdir(cand) and cand not in sys.path:
        sys.path.insert(0, cand)
        try:
            from rapidfuzz import fuzz as _RF
        except Exception:
            _RF = None

MAX_TITLE_LEN = 150

# Stop words (ru/en) that don't identify a news item / skill and hurt matching.
FILLER = set(
    "and or the a an of in on for to with via new bei der die und in zu von mit "
    "что как для и в на по из о об при не это его ее их у от у по ии ai да раз".split()
)


def norm(title):
    t = re.sub(r"\s+", " ", str(title).strip().lower())
    t = re.sub(r"[.…:]$", "", t)
    return t[:MAX_TITLE_LEN]


def _sig(title):
    toks = re.sub(r"[^\w\s]", " ", norm(title), flags=re.UNICODE).split()
    return {w for w in toks if w not in FILLER and len(w) > 2}


def is_dup(title, known_titles, threshold=65):
    """True if `title` looks like it was already sent.

    known_titles: list of previously-published titles (strings).
    """
    a = norm(title)
    if _RF is not None:
        for k in known_titles:
            if k and _RF.partial_ratio(a, norm(k)) >= threshold:
                return True
        return False
    # fallback without rapidfuzz: token overlap
    fa = _sig(a)
    if not fa:
        return False
    for k in known_titles:
        fb = _sig(k)
        if not fb:
            continue
        shared = len(fa & fb)
        if shared >= 2 and shared / min(len(fa), len(fb)) >= 0.6:
            return True
    return False


def title_hash(title):
    return hashlib.sha256(norm(title).encode("utf-8")).hexdigest()[:20]