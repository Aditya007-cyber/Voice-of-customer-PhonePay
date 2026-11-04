"""Stage 0: PII redaction. Runs BEFORE anything is stored for analysis or sent to an LLM.

Rule-based, so it is fast and deterministic. Known gap: personal NAMES are not detected.
For production add an NER layer (for example Microsoft Presidio) behind the same interface.
"""
from __future__ import annotations

import re

# Order matters: longer / more specific patterns first.
PATTERNS = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("UPI_ID", re.compile(r"\b[\w.\-]{2,}@(?:ok[a-z]+|ybl|ibl|axl|paytm|upi|apl|sbi|icici|hdfcbank|axisbank|oksbi|okaxis|okhdfcbank|okicici|fbl|pthdfc|ptyes|ptsbi|barodampay|[a-z]{2,10})\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){15}\d\b")),
    ("AADHAAR", re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),  # also catches "98765 43210"
    ("ACCOUNT", re.compile(r"(?<!\d)\d{9,18}(?!\d)")),
]


def redact(text: str) -> tuple[str, dict]:
    """Return (redacted_text, counts_by_type)."""
    counts: dict[str, int] = {}
    out = text
    for name, pat in PATTERNS:
        def _sub(m, name=name):
            counts[name] = counts.get(name, 0) + 1
            return f"[{name}]"
        out = pat.sub(_sub, out)
    return out, counts


def has_pii(text: str) -> list[str]:
    """Names of PII types still detectable in text (used by the eval suite)."""
    found = []
    for name, pat in PATTERNS:
        # the placeholder itself looks like [UPI_ID] - strip placeholders before scanning
        scrubbed = re.sub(r"\[(?:EMAIL|UPI_ID|CARD|AADHAAR|PAN|PHONE|ACCOUNT)\]", "", text)
        if pat.search(scrubbed):
            found.append(name)
    return found
