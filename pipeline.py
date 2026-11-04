"""Orchestrates the five stages and exports everything the dashboard needs as one JSON document."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from . import db
from .churn import detect_churn
from .cluster import cluster_pain_points
from .extract import extract
from .recommend import build_recommendations, finding_text
from .redact import redact
from .taxonomy import CATEGORY_LABELS


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ingest(conn, rows):
    """Stage 0: normalise + redact. Analysis only ever sees redacted_text."""
    pii_total = {}
    for r in rows:
        red, counts = redact(r["text"])
        for k, v in counts.items():
            pii_total[k] = pii_total.get(k, 0) + v
        conn.execute(
            "INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["source"], r["timestamp"], r["text"], red,
             int(r["rating"]) if str(r.get("rating", "")).strip() else None,
             r.get("app_version") or None, r.get("user_segment") or None,
             1 if str(r.get("is_synthetic", "")).lower() == "true" else 0, 1 if counts else 0))
    conn.commit()
    return pii_total


def run(conn, backend="rules", llm_client=None, log=print):
    recs = conn.execute("SELECT id, source, redacted_text FROM records ORDER BY timestamp").fetchall()
    rejected = 0
    pps = []
    # Stage 1 + 4
    for r in recs:
        res = extract(r["redacted_text"], backend=backend, client=llm_client)
        rejected += res.get("rejected", 0)
        for p in res["pain_points"]:
            cur = conn.execute(
                "INSERT INTO pain_points(record_id, category, severity, sentiment, issue, evidence_quote) VALUES (?,?,?,?,?,?)",
                (r["id"], p["category"], p["severity"], p["sentiment"], p["issue"], p["evidence_quote"]))
            pps.append({"pp_id": cur.lastrowid, "record_id": r["id"], "category": p["category"],
                        "quote": p["evidence_quote"], "source": r["source"]})
        ch = detect_churn(r["redacted_text"])
        conn.execute("INSERT INTO churn VALUES (?,?,?,?)",
                     (r["id"], int(ch["flag"]), ch["trigger_phrase"], ch["competitor"]))
    conn.commit()
    log(f"stage 1/4  extracted {len(pps)} pain points from {len(recs)} records (LLM-rejected: {rejected})")

    # Stage 2 + 3
    cl = cluster_pain_points(pps)
    for pp_id, cid in cl["assign"].items():
        conn.execute("UPDATE pain_points SET cluster_id = ? WHERE pp_id = ?", (cid, pp_id))
    for cid, m in cl["meta"].items():
        conn.execute("INSERT INTO clusters VALUES (?,?,?,?,?)",
                     (cid, m["name"], m["category"], m["purity"], ", ".join(m["top_terms"])))
    conn.commit()
    log(f"stage 2/3  {len([c for c in cl['meta'] if c != -1])} clusters + long tail")
    return cl


def export(conn, cl, out_path, pii_total=None, backend="rules"):
    """Build the JSON the dashboard reads. Quotes are re-verified against the redacted source here."""
    red = {r["id"]: r for r in conn.execute("SELECT * FROM records")}
    pp_rows = {r["pp_id"]: dict(r) for r in conn.execute("SELECT * FROM pain_points")}
    quote_fail = 0

    cluster_rows = []
    for cid, m in sorted(cl["meta"].items()):
        stats = db.cluster_stats(conn, cid)
        quotes = []
        for pid in m["quote_pp_ids"]:
            p = pp_rows[pid]
            rec = red[p["record_id"]]
            if p["evidence_quote"] not in rec["redacted_text"]:  # hard guard: never ship a non-verbatim quote
                quote_fail += 1
                continue
            quotes.append({"pp_id": pid, "record_id": p["record_id"], "source": rec["source"],
                           "quote": p["evidence_quote"], "timestamp": rec["timestamp"]})
        cluster_rows.append({
            "cluster_id": cid, "name": m["name"], "category": m["category"],
            "category_label": CATEGORY_LABELS.get(m["category"], "Long tail"),
            "purity": m["purity"], "top_terms": m["top_terms"], "stats": stats, "quotes": quotes,
            "finding": finding_text(m["name"], stats), "weekly": db.weekly_series(conn, cid),
            "record_ids": [r[0] for r in conn.execute(
                "SELECT DISTINCT record_id FROM pain_points WHERE cluster_id = ?", (cid,))],
        })

    themes = []
    by_cat = {}
    for c in cluster_rows:
        by_cat.setdefault(c["category"], []).append(c)
    for cat, cs in by_cat.items():
        ids = [c["cluster_id"] for c in cs]
        st = db.cluster_stats(conn, ids)
        label = CATEGORY_LABELS.get(cat, "Long tail")
        cs_sorted = sorted(cs, key=lambda c: -c["stats"]["n_all"])
        quotes = []
        for k in range(3):  # round-robin across sub-themes so the evidence is varied
            for c in cs_sorted:
                if len(quotes) < 3 and k < len(c["quotes"]):
                    quotes.append(c["quotes"][k])
        themes.append({"category": cat, "label": label, "cluster_ids": ids, "stats": st, "quotes": quotes,
                       "finding": finding_text(label, st), "weekly": db.weekly_series(conn, ids),
                       "record_ids": sorted({rid for c in cs for rid in c["record_ids"]})})
    themes.sort(key=lambda t: -t["stats"]["n_all"])
    # small datasets still deserve recommendations, flagged as thin evidence when under 8 records
    recs = build_recommendations(themes, min_records=max(3, min(8, round(0.03 * len(red)))))
    churn_rows = []
    for r in conn.execute("SELECT c.*, r.source, r.timestamp, r.redacted_text, r.app_version FROM churn c "
                          "JOIN records r ON r.id = c.record_id WHERE c.flag = 1 ORDER BY r.timestamp DESC"):
        churn_rows.append({"record_id": r["record_id"], "trigger_phrase": r["trigger_phrase"],
                           "competitor": r["competitor"], "source": r["source"], "timestamp": r["timestamp"],
                           "text": r["redacted_text"], "app_version": r["app_version"]})

    rec_pp = {}
    for r in conn.execute("SELECT record_id, cluster_id, MAX(severity) sev FROM pain_points GROUP BY record_id, cluster_id"):
        rec_pp.setdefault(r["record_id"], []).append({"cluster_id": r["cluster_id"], "severity": r["sev"]})
    churn_by = {c["record_id"]: c for c in churn_rows}
    records = []
    for rid, r in red.items():
        records.append({"id": rid, "source": r["source"], "timestamp": r["timestamp"], "app_version": r["app_version"],
                        "rating": r["rating"], "text": r["redacted_text"], "synthetic": bool(r["is_synthetic"]),
                        "clusters": rec_pp.get(rid, []),
                        "churn": ({"trigger_phrase": churn_by[rid]["trigger_phrase"], "competitor": churn_by[rid]["competitor"]}
                                  if rid in churn_by else None)})
    records.sort(key=lambda x: x["timestamp"], reverse=True)

    doc = {
        "meta": {"as_of": db.as_of(conn).strftime("%Y-%m-%d"), "records": len(red),
                 "synthetic": all(r["is_synthetic"] for r in red.values()), "backend": backend,
                 "windows": db.windows(conn), "pii_redacted": pii_total or {}, "quote_verification_failures": quote_fail,
                 "sources": {r[0]: r[1] for r in conn.execute("SELECT source, COUNT(*) FROM records GROUP BY source ORDER BY 2 DESC")}},
        "weekly_total": db.weekly_series(conn),
        "themes": themes, "clusters": cluster_rows, "recommendations": recs, "churn": churn_rows, "records": records,
        "category_labels": CATEGORY_LABELS,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc
