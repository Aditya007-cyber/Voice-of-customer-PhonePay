"""Stage 5: recommendations. Numbers come from db.cluster_stats (SQL); the playbook comes from taxonomy.PLAYBOOK.

Offline mode is template-driven so it is deterministic and eval-able. With an LLM you would pass the same
stats + verified quotes as INPUT and ask for prose; the eval suite's count/quote checks stay identical.

priority = impact / effort, where
  impact (0-100) = 100 * (0.45 * min(1, recent_share / 12%) + 0.25 * avg_severity / 5
                          + 0.20 * churn_rate + 0.10 * trend_boost)
"""
from __future__ import annotations

from .taxonomy import CATEGORY_LABELS, PLAYBOOK


def pct(n: int, total: int) -> int:
    return round(100 * n / total) if total else 0


def finding_text(name: str, s: dict) -> str:
    w = s["window_days"]
    cur = pct(s["n_recent"], s["total_recent"])
    prior = pct(s["n_prior"], s["total_prior"])
    base = (f"{s['n_recent']} of {s['total_recent']} feedback records ({cur}%) in the last {w} days mention "
            f"'{name}'")
    if s["total_prior"] == 0:
        trend = "no earlier period to compare with yet"
    elif s["n_prior"] == 0 and s["n_recent"] > 0:
        trend = "new in this period (0% in the previous 14 days)"
    elif cur > prior:
        trend = f"up from {prior}% in the previous {w} days"
    elif cur < prior:
        trend = f"down from {prior}% in the previous {w} days"
    else:
        trend = f"flat versus the previous {w} days ({prior}%)"
    overall = f"{s['n_all']} of {s['total_all']} records ({pct(s['n_all'], s['total_all'])}%) overall"
    return f"{base}, {trend}. {overall}."


def impact_score(s: dict) -> float:
    churn_rate = s["churn_records"] / s["n_all"] if s["n_all"] else 0
    ratio = (s["share_recent"] / s["share_prior"]) if s["share_prior"] else (3 if s["share_recent"] else 1)
    trend_boost = min(1.0, max(0.0, (ratio - 1) / 2)) if s["total_prior"] else 0.0  # no history, no trend credit
    score = 100 * (0.45 * min(1, s["share_recent"] / 0.12) + 0.25 * s["avg_severity"] / 5
                   + 0.20 * min(1, churn_rate * 2) + 0.10 * trend_boost)
    return round(score, 1)


def build_recommendations(theme_rows: list[dict], top_k: int = 6, min_records: int = 8) -> list[dict]:
    """theme_rows: [{category, label, cluster_ids, stats, quotes, finding}] -> ranked recommendations (one per theme)."""
    recs = []
    for c in theme_rows:
        if c["category"] == "long_tail" or c["stats"]["n_all"] < min_records:
            continue
        pb = PLAYBOOK.get(c["category"])
        if not pb:
            continue
        s = c["stats"]
        imp = impact_score(s)
        recs.append({
            "category": c["category"],
            "cluster_ids": c["cluster_ids"],
            "title": CATEGORY_LABELS[c["category"]],
            "finding": c["finding"],
            "evidence_pp_ids": [q["pp_id"] for q in c["quotes"]],
            "change": pb["change"],
            "impact": imp,
            "effort": pb["effort"],
            "effort_text": pb["effort_text"],
            "priority": round(imp / pb["effort"], 1),
            "owner": pb["owner"],
            "dependencies": pb["dependencies"],
            "compliance": pb["compliance"],
            "thin_evidence": s["n_all"] < 8,  # fewer than 8 records: treat as a lead, not a conclusion
            "status": "proposed",
        })
    recs.sort(key=lambda r: r["priority"], reverse=True)
    for i, r in enumerate(recs):
        r["rank"] = i + 1
    return recs[:top_k]
