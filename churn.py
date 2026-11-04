"""Stage 4: churn-signal detection. Returns a flag plus the exact trigger phrase (a substring of the text)."""
from __future__ import annotations

import re
from .taxonomy import CHURN_PATTERNS, COMPETITORS

_COMPILED = [re.compile(p, re.I) for p in CHURN_PATTERNS]
# phrases that look like churn but are about someone else, or are retracted
_RETRACT = re.compile(r"(?:thought about|considered|friend is|friends are|my friend|he is|she is|they are)", re.I)


def detect_churn(text: str) -> dict:
    norm = text.replace("\u2019", "'")  # same length as text, so match offsets are valid in both
    for pat in _COMPILED:
        m = pat.search(norm)
        if not m:
            continue
        phrase = text[m.start():m.end()]  # always a verbatim slice of the original
        # light guard: ignore if the sentence around the match is clearly hypothetical / about someone else
        start = max(0, m.start() - 60)
        window = text[start:m.end() + 40]
        if _RETRACT.search(window):
            continue
        low = phrase.lower()
        competitor = next((c for c in COMPETITORS if c in low), None)
        return {"flag": True, "trigger_phrase": phrase, "competitor": competitor}
    return {"flag": False, "trigger_phrase": None, "competitor": None}
