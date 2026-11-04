"""Eval suite: defines what a GOOD output is, and fails the run when it is not.

Gate types
  hard     must be 100% / zero. A failure blocks the release (run_pipeline.py exits non-zero).
  soft     quality target. Below `block_below` blocks; between block_below and threshold warns.
  monitor  tracked, never blocks.

Design principle: the LLM (or rules) tags and drafts; CODE counts and verifies. Count accuracy and quote
grounding re-derive the truth with independent SQL / string checks and compare.

Gold labels in data/golden.json come from the synthetic generator. For real data, replace with a
hand-labelled golden set (150+ records, two labellers on 20%) in the same format.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .redact import has_pii
from .taxonomy import CATEGORIES, CATEGORY_LABELS

HINGLISH = re.compile(r"\b(nahi|kab|hai|paise|paisa|kat|wapas|chhod|band|bhejta|mera|raha|hoon|lekin|abhi|aayenge|dikha|sahi|bata|milta|hi)\b", re.I)


# ---------------------------------------------------------------- helpers
def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


def _entry(id, name, kind, score, threshold, passed, detail, block_below=None, unit="ratio"):
    return {"id": id, "name": name, "kind": kind, "score": score, "threshold": threshold, "passed": passed,
            "block_below": block_below, "detail": detail, "unit": unit}


# ---------------------------------------------------------------- rubric (deterministic stand-in for an LLM judge)
ACTION = re.compile(r"\b(add|show|redesign|replace|route|improve|ship|triage|explain|run|state|offer|provide)\b", re.I)
UI_NOUN = re.compile(r"\b(screen|flow|tracker|state|step|view|warning|card|button|action|status|error|inline|banner|notification|policy|SLA)\b", re.I)


def rubric(finding: str, change: str, n_verified_quotes: int, fields: dict, traceable: bool) -> dict:
    """Score a recommendation 1-5 on five dimensions. Same rubric an LLM judge would be asked to apply;
    calibrate the judge against this + human scores on ~30 outputs before trusting it."""
    specific = 5 if (len(change) >= 80 and ACTION.search(change) and UI_NOUN.search(change)) else (3 if len(change) >= 40 else 1)
    has_pct = bool(re.search(r"\d+%", finding))
    has_of = bool(re.search(r"\d+ of \d+", finding))
    quantified = 5 if (has_pct and has_of) else (3 if has_pct or has_of else 1)
    grounded = {0: 1, 1: 2, 2: 4}.get(n_verified_quotes, 5)
    present = sum(1 for k in ("owner", "effort_text", "dependencies", "compliance") if fields.get(k))
    feasible = 5 if present == 4 else 1 + present
    trace = 5 if traceable else 1
    dims = {"specific": specific, "quantified": quantified, "grounded": grounded, "feasible": feasible, "traceable": trace}
    dims["mean"] = round(sum(dims.values()) / 5, 2)
    return dims


BAD_EXAMPLE = {"finding": "Users are unhappy with the onboarding", "change": "Improve onboarding", "n": 0, "fields": {}, "trace": False}
GOOD_EXAMPLE = {
    "finding": "12 of 50 Play Store reviews (24%) mention confusion at OTP and bank linking during sign-up, up from 6% last month",
    "change": "Add inline progress and clear error states to bank-linking step 3, with a retry-OTP action.",
    "n": 3, "fields": {"owner": "Growth", "effort_text": "about 2 sprints", "dependencies": "bank SDK", "compliance": "security review"},
    "trace": True,
}


# ---------------------------------------------------------------- individual evals
def eval_count_accuracy(conn, doc):
    """Every number in every finding string must equal an independent SQL recount."""
    mx = conn.execute("SELECT MAX(timestamp) FROM records").fetchone()[0]

    def q(cids, lo_days, hi_days):
        cids = [cids] if isinstance(cids, int) else list(cids)
        ph = ",".join("?" * len(cids))
        return conn.execute(
            "SELECT COUNT(DISTINCT p.record_id) FROM pain_points p JOIN records r ON r.id = p.record_id "
            f"WHERE p.cluster_id IN ({ph}) AND r.timestamp > datetime(?, ?) AND r.timestamp <= datetime(?, ?)",
            (*cids, mx, f"-{lo_days} days", mx, f"-{hi_days} days")).fetchone()[0]

    def tot(lo_days, hi_days):
        return conn.execute("SELECT COUNT(*) FROM records WHERE timestamp > datetime(?, ?) AND timestamp <= datetime(?, ?)",
                            (mx, f"-{lo_days} days", mx, f"-{hi_days} days")).fetchone()[0]

    checks, bad = 0, []
    texts = ([(c["cluster_id"], c["finding"]) for c in doc["clusters"]]
             + [(t["cluster_ids"], t["finding"]) for t in doc["themes"]]
             + [(r["cluster_ids"], r["finding"]) for r in doc["recommendations"]])
    for cid, text in texts:
        n_r, N_r, p_r = q(cid, 14, 0), tot(14, 0), None
        n_p, N_p = q(cid, 28, 14), tot(28, 14)
        ids_ = [cid] if isinstance(cid, int) else list(cid)
        n_a = conn.execute(f"SELECT COUNT(DISTINCT record_id) FROM pain_points WHERE cluster_id IN ({','.join('?' * len(ids_))})", ids_).fetchone()[0]
        N_a = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        pct = lambda a, b: round(100 * a / b) if b else 0
        m1 = re.search(r"^(\d+) of (\d+) feedback records \((\d+)%\)", text)
        m2 = re.search(r"(?:up from|down from) (\d+)%|flat versus the previous \d+ days \((\d+)%\)|new in this period \((\d+)%", text)
        m3 = re.search(r"(\d+) of (\d+) records \((\d+)%\) overall", text)
        expected = [
            (m1 and int(m1.group(1)), n_r), (m1 and int(m1.group(2)), N_r), (m1 and int(m1.group(3)), pct(n_r, N_r)),
            (m2 and int(next(g for g in m2.groups() if g is not None)), pct(n_p, N_p)) if N_p else (None, None),
            (m3 and int(m3.group(1)), n_a), (m3 and int(m3.group(2)), N_a), (m3 and int(m3.group(3)), pct(n_a, N_a)),
        ]
        for got, want in expected:
            checks += 1
            if got != want:
                bad.append({"cluster_id": cid, "text": text, "got": got, "want": want})
    # self-test: the checker must catch a tampered number
    tampered = re.sub(r"^\d+", lambda m: str(int(m.group(0)) + 1), texts[0][1]) if texts else ""
    caught = bool(texts) and (re.search(r"^(\d+)", tampered).group(1) != str(q(texts[0][0], 14, 0)))
    score = 1 - len(bad) / checks if checks else 0.0
    return _entry("count_accuracy", "Count accuracy (numbers match SQL recount)", "hard", round(score, 4), 1.0,
                  score == 1.0 and caught, {"checks": checks, "mismatches": bad[:5], "tamper_selftest_caught": caught})


def eval_quote_grounding(conn, doc):
    red = {r["id"]: r["redacted_text"] for r in conn.execute("SELECT id, redacted_text FROM records")}
    total, bad = 0, []
    for r in conn.execute("SELECT pp_id, record_id, evidence_quote FROM pain_points"):
        total += 1
        if r["evidence_quote"] not in red.get(r["record_id"], ""):
            bad.append({"pp_id": r["pp_id"], "quote": r["evidence_quote"]})
    for c in doc["clusters"] + doc["themes"]:
        for qt in c["quotes"]:
            total += 1
            if qt["quote"] not in red.get(qt["record_id"], ""):
                bad.append({"cluster": c.get("cluster_id", c.get("category")), "quote": qt["quote"]})
    for ch in doc["churn"]:
        total += 1
        if ch["trigger_phrase"] not in red.get(ch["record_id"], ""):
            bad.append({"churn_record": ch["record_id"], "quote": ch["trigger_phrase"]})
    score = 1 - len(bad) / total if total else 0.0
    return _entry("quote_grounding", "Quote grounding (verbatim in cited record)", "hard", round(score, 4), 1.0,
                  score == 1.0, {"quotes_checked": total, "violations": bad[:5]})


def eval_pii(conn, doc):
    hits = []
    scanned = 0
    for r in conn.execute("SELECT id, redacted_text FROM records"):
        scanned += 1
        f = has_pii(r["redacted_text"])
        if f:
            hits.append({"record": r["id"], "types": f})
    blobs = [c["finding"] for c in doc["clusters"]] + [r["finding"] for r in doc["recommendations"]]
    blobs += [q["quote"] for c in doc["clusters"] for q in c["quotes"]] + [c["text"] for c in doc["churn"]]
    for b in blobs:
        scanned += 1
        f = has_pii(b)
        if f:
            hits.append({"output": b[:80], "types": f})
    return _entry("pii_leakage", "PII leakage (zero tolerance)", "hard", len(hits), 0, len(hits) == 0,
                  {"texts_scanned": scanned, "hits": hits[:5], "known_gap": "personal names are not detected by the rule-based redactor",
                   "redacted": doc["meta"].get("pii_redacted", {})}, unit="count")


def eval_schema(conn, doc):
    bad = 0
    n = 0
    for r in conn.execute("SELECT * FROM pain_points"):
        n += 1
        if (r["category"] not in CATEGORIES or not (1 <= r["severity"] <= 5) or not r["evidence_quote"]
                or r["sentiment"] not in ("negative", "mixed", "positive")):
            bad += 1
    score = 1 - bad / n if n else 0.0
    return _entry("schema_validity", "Schema validity (categories, severity range, quote present)", "hard",
                  round(score, 4), 1.0, bad == 0, {"checked": n, "invalid": bad})


def _gold_pred(conn, golden):
    pred = defaultdict(set)
    for r in conn.execute("SELECT record_id, category FROM pain_points"):
        pred[r["record_id"]].add(r["category"])
    return pred


def eval_extraction(conn, doc, golden):
    pred = _gold_pred(conn, golden)
    texts = {r["id"]: (r["redacted_text"], r["source"]) for r in conn.execute("SELECT id, redacted_text, source FROM records")}
    tp = fp = fn = 0
    per_cat = defaultdict(lambda: [0, 0, 0])
    slices = {"hinglish": [0, 0, 0], "english": [0, 0, 0]}
    by_source = defaultdict(lambda: [0, 0, 0])
    fns, fps = [], []
    for rid, g in golden.items():
        gold = set(g["categories"])
        p = pred.get(rid, set())
        sl = "hinglish" if HINGLISH.search(texts[rid][0]) else "english"
        src = texts[rid][1]
        for c in gold & p:
            tp += 1; per_cat[c][0] += 1; slices[sl][0] += 1; by_source[src][0] += 1
        for c in p - gold:
            fp += 1; per_cat[c][1] += 1; slices[sl][1] += 1; by_source[src][1] += 1
            fps.append({"record_id": rid, "predicted": c, "gold": sorted(gold), "text": texts[rid][0]})
        for c in gold - p:
            fn += 1; per_cat[c][2] += 1; slices[sl][2] += 1; by_source[src][2] += 1
            fns.append({"record_id": rid, "missed": c, "text": texts[rid][0]})
    P, R, F = _prf(tp, fp, fn)
    detail = {
        "precision": P, "recall": R, "f1": F, "tp": tp, "fp": fp, "fn": fn,
        "per_category": {CATEGORY_LABELS[c]: dict(zip(("precision", "recall", "f1"), _prf(*v))) | {"support": v[0] + v[2]} for c, v in sorted(per_cat.items())},
        "slices": {k: dict(zip(("precision", "recall", "f1"), _prf(*v))) | {"support": v[0] + v[2]} for k, v in slices.items()},
        "by_source": {k: dict(zip(("precision", "recall", "f1"), _prf(*v))) | {"support": v[0] + v[2]} for k, v in by_source.items()},
        "false_negatives": fns[:6], "false_positives": fps[:6],
    }
    return _entry("extraction_f1", "Extraction quality (F1 vs gold, record x category)", "soft", F, 0.85, F >= 0.85, detail, block_below=0.80)


def eval_category_accuracy(conn, doc, golden):
    ok = tot = 0
    for r in conn.execute("SELECT record_id, category FROM pain_points"):
        g = golden.get(r["record_id"])
        if g is None:
            continue
        tot += 1
        ok += r["category"] in g["categories"]
    s = ok / tot if tot else 0.0
    return _entry("category_accuracy", "Category accuracy (predicted category is in gold)", "soft", round(s, 3), 0.85, s >= 0.85,
                  {"correct": ok, "total": tot}, block_below=0.75)


def eval_churn(conn, doc, golden):
    flagged = {r["record_id"] for r in conn.execute("SELECT record_id FROM churn WHERE flag = 1")}
    gold = {k for k, v in golden.items() if v["churn"]}
    tp, fp, fn = len(flagged & gold), len(flagged - gold), len(gold - flagged)
    P, R, F = _prf(tp, fp, fn)
    red = {r["id"]: r["redacted_text"] for r in conn.execute("SELECT id, redacted_text FROM records")}
    return _entry("churn_flags", "Churn flags (recall >= 0.85, precision >= 0.70)", "soft", R, 0.85, R >= 0.85 and P >= 0.70,
                  {"precision": P, "recall": R, "f1": F, "tp": tp, "fp": fp, "fn": fn,
                   "false_negatives": [{"record_id": r, "text": red[r]} for r in sorted(gold - flagged)[:6]],
                   "false_positives": [{"record_id": r, "text": red[r]} for r in sorted(flagged - gold)[:6]]},
                  block_below=0.75)


def eval_cluster_purity(conn, doc, golden):
    ok = tot = 0
    rows = conn.execute("SELECT p.record_id, c.category FROM pain_points p JOIN clusters c ON c.cluster_id = p.cluster_id "
                        "WHERE c.category != 'long_tail'")
    for r in rows:
        g = golden.get(r["record_id"])
        if g is None:
            continue
        tot += 1
        ok += r["category"] in g["categories"]
    s = ok / tot if tot else 0.0
    return _entry("cluster_purity", "Cluster purity (members belong to the cluster's gold category)", "soft", round(s, 3), 0.80, s >= 0.80,
                  {"members_checked": tot, "correct": ok}, block_below=0.70)


def eval_hallucination(conn, doc, golden):
    """Records with NO gold pain point must not produce findings (praise, spam, negated keywords)."""
    clean = [k for k, v in golden.items() if not v["categories"]]
    pred = _gold_pred(conn, golden)
    texts = {r["id"]: r["redacted_text"] for r in conn.execute("SELECT id, redacted_text FROM records")}
    invented = [k for k in clean if pred.get(k)]
    rate = len(invented) / len(clean) if clean else 0.0
    return _entry("hallucination_probe", "Hallucination probe (false findings on complaint-free records)", "soft", round(rate, 3), 0.02,
                  rate <= 0.02, {"clean_records": len(clean), "false_findings": len(invented),
                                 "examples": [{"record_id": k, "predicted": sorted(pred[k]), "text": texts[k]} for k in invented[:6]]},
                  block_below=None, unit="rate (lower is better)")


def eval_recommendations(conn, doc):
    quote_ok = {t["category"]: {q["pp_id"] for q in t["quotes"]} for t in doc["themes"]}
    rows = []
    for r in doc["recommendations"]:
        n_q = len([p for p in r["evidence_pp_ids"] if p in quote_ok.get(r["category"], set())])
        d = rubric(r["finding"], r["change"], n_q, r, traceable=(r["category"] in quote_ok and n_q == len(r["evidence_pp_ids"])))
        rows.append({"rank": r["rank"], "title": r["title"], **d})
    if not rows:  # not enough evidence yet: warn, never block
        return _entry("recommendation_rubric", "Recommendation rubric (mean 1-5, no dimension under 3)", "soft", 0.0, 4.0, False,
                      {"note": "no recommendations generated: not enough evidence yet"}, block_below=None, unit="score /5")
    mean = round(sum(x["mean"] for x in rows) / len(rows), 2)
    low_dim = min((min(v for k, v in x.items() if k in ("specific", "quantified", "grounded", "feasible", "traceable")) for x in rows), default=0)
    return _entry("recommendation_rubric", "Recommendation rubric (mean 1-5, no dimension under 3)", "soft", mean, 4.0,
                  mean >= 4.0 and low_dim >= 3, {"per_recommendation": rows, "lowest_dimension": low_dim,
                                                 "judge": "deterministic rubric; replace/augment with a calibrated LLM judge"},
                  block_below=3.5, unit="score /5")


def eval_judge_sanity(doc):
    """The rubric must clearly separate the bad example from the good one, otherwise it is useless as a gate."""
    bad = rubric(BAD_EXAMPLE["finding"], BAD_EXAMPLE["change"], BAD_EXAMPLE["n"], BAD_EXAMPLE["fields"], BAD_EXAMPLE["trace"])
    good = rubric(GOOD_EXAMPLE["finding"], GOOD_EXAMPLE["change"], GOOD_EXAMPLE["n"], GOOD_EXAMPLE["fields"], GOOD_EXAMPLE["trace"])
    ok = bad["mean"] <= 2.0 and good["mean"] >= 4.0
    return _entry("judge_sanity", "Rubric sanity (bad example <= 2.0, good example >= 4.0)", "hard", good["mean"] - bad["mean"], 2.0, ok,
                  {"bad": {"text": BAD_EXAMPLE["finding"], **bad}, "good": {"text": GOOD_EXAMPLE["finding"], **good}}, unit="gap")


# ---------------------------------------------------------------- entry points
def run_all(conn, doc, golden):
    results = [eval_count_accuracy(conn, doc), eval_quote_grounding(conn, doc), eval_pii(conn, doc), eval_schema(conn, doc),
               eval_judge_sanity(doc)]
    if golden:
        results += [eval_extraction(conn, doc, golden), eval_category_accuracy(conn, doc, golden), eval_cluster_purity(conn, doc, golden),
                    eval_churn(conn, doc, golden), eval_hallucination(conn, doc, golden)]
    results.append(eval_recommendations(conn, doc))
    hard_ok = all(e["passed"] for e in results if e["kind"] == "hard")
    blocking_soft = []
    for e in results:
        if e["kind"] == "soft" and not e["passed"] and e["block_below"] is not None:
            low = e["score"] < e["block_below"] if e["unit"] != "rate (lower is better)" else False
            if low:
                blocking_soft.append(e["id"])
    return {"hard_gates_passed": hard_ok and not blocking_soft, "hard_ok": hard_ok, "blocking_soft": blocking_soft,
            "golden_source": "generator-labelled synthetic (replace with human labels)" if golden else None,
            "results": results}


def print_report(rep):
    print("\n=== EVAL REPORT ===")
    for e in rep["results"]:
        mark = "PASS" if e["passed"] else ("FAIL" if e["kind"] == "hard" else "WARN")
        s = e["score"]
        print(f"[{mark}] {e['kind']:<5} {e['name']:<66} score={s} (target {e['threshold']})")
    verdict = "release allowed" if rep["hard_gates_passed"] else "RELEASE BLOCKED"
    print(f"=> {verdict}. soft metrics below target are warnings unless under their block line "
          f"({', '.join(rep['blocking_soft']) or 'none blocking'})\n")
