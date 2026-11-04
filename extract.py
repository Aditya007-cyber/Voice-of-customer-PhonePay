"""Stage 1: extract pain points from a (redacted) record.

Two backends behind one interface:
  - "rules": offline keyword engine from taxonomy.py. Fast, free, deterministic, deliberately limited
             (misses sarcasm and keyword-free complaints; the eval suite measures exactly that).
  - "llm":   Claude via the Anthropic SDK with a JSON contract. Every returned quote is verified to be an
             exact substring of the input; anything that is not is dropped and counted as rejected.

Output contract (per pain point):
  {issue, category, severity (1-5), sentiment, evidence_quote}
"""
from __future__ import annotations

import json
import os
import re

from .taxonomy import (CATEGORIES, CATEGORY_LABELS, NEGATION_GUARD, SEV_BLOCKED, SEV_MONEY, SEV_RECURRING)

_SENT = re.compile(r"[^.!?\n]+[.!?]?")
_KW = {
    cat: [re.compile(r"(?<![a-z])" + re.escape(k), re.I) for k in spec["keywords"]]
    for cat, spec in CATEGORIES.items()
}


def _sentences(text: str):
    for m in _SENT.finditer(text):
        s = m.group(0).strip()
        if len(s) >= 8:
            yield s


def _negated(sentence_low: str, pos: int) -> bool:
    before = sentence_low[max(0, pos - 24):pos]
    return any(g in before for g in NEGATION_GUARD) or sentence_low[:pos].strip().endswith(("never", "no"))


POSITIVE = ["decent", "great", "love", "best", "excellent", "happy", "smooth", "reliable", "recommend", "easy",
            "quick", "fast", "instantly", "clean", "simple", "good", "works well", "awesome", "perfect",
            "friendly", "wonderful", "thank"]
NEG_SIGNAL = [" not ", "n't", "never", "fail", "problem", "issue", "worst", "bad", "slow", "stuck", "crash",
              "unable", "cannot", "pending", "deducted", "declined", "rejected", "blocked", "nahi", "waiting",
              "gone", "still", "again", "no response", "no reply", "wrong", "error", "hangs", "freez"]


def _praise_only(low: str) -> bool:
    """Sentence is positive and carries no negative signal: keywords in it are not pain points."""
    return any(p in low for p in POSITIVE) and not any(n in f" {low} " for n in NEG_SIGNAL)


def _score_sentence(sentence: str):
    """Return (category, hits, first_pos) for the best category in this sentence, or None."""
    low = sentence.lower().replace("\u2019", "'")  # curly apostrophes; same length so offsets still match
    if _praise_only(low):
        return None
    best = None
    for cat, pats in _KW.items():
        hits, first = 0, 10 ** 6
        for p in pats:
            m = p.search(low)
            if m and not _negated(low, m.start()):
                hits += 1
                first = min(first, m.start())
        if hits and (best is None or (hits, -first) > (best[1], -best[2])):
            best = (cat, hits, first)
    return best


def _severity(sentence: str) -> int:
    low = sentence.lower().replace("\u2019", "'")
    sev = 2
    sev += any(w in low for w in SEV_MONEY)
    sev += any(w in low for w in SEV_BLOCKED)
    sev += any(w in low for w in SEV_RECURRING)
    return max(1, min(5, sev))


def extract_rules(text: str) -> dict:
    by_cat: dict[str, dict] = {}
    for sent in _sentences(text):
        res = _score_sentence(sent)
        if not res:
            continue
        cat = res[0]
        sev = _severity(sent) + (1 if cat == "fraud_scam" else 0)
        sev = min(5, sev)
        cur = by_cat.get(cat)
        if cur is None or sev > cur["severity"]:
            by_cat[cat] = {
                "issue": f"{CATEGORY_LABELS[cat]}: {sent[:90].rstrip('.!? ')}",
                "category": cat,
                "severity": sev,
                "sentiment": "negative",
                "evidence_quote": sent,
            }
    return {"pain_points": list(by_cat.values()), "rejected": 0}


LLM_PROMPT = """You analyse customer feedback about a UPI payments app.
Return ONLY JSON: {{"pain_points":[{{"issue":str,"category":str,"severity":int,"sentiment":"negative","evidence_quote":str}}]}}
Rules:
- category must be one of: {cats}
- severity 1-5: 1 annoyance, 3 task blocked, 5 money lost or locked out, repeated
- evidence_quote MUST be copied exactly (character for character) from the feedback
- If the feedback contains no complaint, return {{"pain_points":[]}}
- Never invent issues. Never count or estimate numbers.
Feedback:
\"\"\"{text}\"\"\""""


def extract_llm(text: str, client=None, model: str | None = None) -> dict:
    """LLM backend. `client` is an anthropic.Anthropic() (injectable for tests)."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    model = model or os.environ.get("VOC_MODEL", "claude-haiku-5-5")
    msg = client.messages.create(
        model=model,
        max_tokens=800,
        messages=[{"role": "user", "content": LLM_PROMPT.format(cats=", ".join(CATEGORIES), text=text)}],
    )
    raw = "".join(getattr(b, "text", "") for b in msg.content)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0)) if m else {"pain_points": []}
    except json.JSONDecodeError:
        return {"pain_points": [], "rejected": 1}
    kept, rejected = [], 0
    for pp in data.get("pain_points", []):
        quote = pp.get("evidence_quote", "")
        cat = pp.get("category")
        if cat not in CATEGORIES or not quote or quote not in text:
            rejected += 1  # hallucinated category or non-verbatim quote: drop it
            continue
        try:
            sev = max(1, min(5, int(pp.get("severity", 3))))
        except (TypeError, ValueError):
            sev = 3
        kept.append({"issue": str(pp.get("issue", ""))[:140], "category": cat, "severity": sev,
                     "sentiment": "negative", "evidence_quote": quote})
    return {"pain_points": kept, "rejected": rejected}


def extract(text: str, backend: str | None = None, client=None) -> dict:
    backend = backend or os.environ.get("VOC_BACKEND", "rules")
    if backend == "llm":
        return extract_llm(text, client=client)
    return extract_rules(text)
