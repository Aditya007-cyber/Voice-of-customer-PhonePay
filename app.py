"""Streamlit dashboard.   streamlit run app.py

Two datasets: real public reviews (data/real_feedback.csv -> out/real.json) and a synthetic demo.
The "Fetch latest real reviews" button pulls fresh public reviews, merges them into the CSV, re-runs the whole
pipeline with its evals, and refreshes every tab. Counts are computed by code, never by the model.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from run_pipeline import run as run_pipeline
from voc import fetch

ROOT = Path(__file__).resolve().parent
BASE = Path(os.environ.get("VOC_HOME", ROOT))          # tests point this at a temp folder
REAL_CSV = BASE / "data" / "real_feedback.csv"
REAL_JSON = BASE / "out" / "real.json"
DEMO_JSON = BASE / "out" / "results.json"

st.set_page_config(page_title="VoC Co-pilot", layout="wide")


@st.cache_data
def load(path: str, mtime: float):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ sidebar: data + fetch button
choices = []
if REAL_JSON.exists() or REAL_CSV.exists():
    choices.append("Real public reviews")
if DEMO_JSON.exists():
    choices.append("Synthetic demo")
if not choices:
    st.error("No data yet. Run `python run_pipeline.py --real` first.")
    st.stop()
dataset = st.sidebar.radio("Data", choices)

if dataset == "Real public reviews":
    with st.sidebar.expander("Fetch settings"):
        use_apple = st.checkbox("App Store (public feed)", value=True)
        use_play = st.checkbox("Google Play (needs google-play-scraper)", value=False)
        countries = st.text_input("App Store countries", value="in", help="Comma-separated storefront codes, for example in,us,gb").split(",")
        pages = st.slider("App Store pages per country (50 reviews each)", 1, 10, 10)
        play_count = st.number_input("Google Play reviews", 50, 5000, 500, step=50)
    if st.sidebar.button("Fetch latest real reviews", type="primary", width="stretch"):
        with st.spinner("Fetching public reviews, then re-running the pipeline and evals..."):
            report = fetch.refresh(REAL_CSV, apple=use_apple, play=use_play,
                                   countries=tuple(c.strip() for c in countries if c.strip()) or ("in",),
                                   pages=pages, play_count=int(play_count))
            if report["total"]:
                _, ev = run_pipeline(REAL_CSV, BASE / "out" / "real.db", REAL_JSON, golden_path="none", log=lambda *_: None)
                report["gates_ok"] = ev["hard_gates_passed"]
        st.session_state["fetch_report"] = report
        st.cache_data.clear()
        st.rerun()

    rep = st.session_state.get("fetch_report")
    if rep:
        for name, r in rep["sources"].items():
            if r["error"]:
                st.sidebar.error(f"{name}: {r['error']}")
            else:
                st.sidebar.success(f"{name}: {r['fetched']} fetched, {r['new']} new")
        if rep.get("gates_ok") is False:
            st.sidebar.error("A hard eval gate failed. Do not trust the findings until it is fixed.")

target = REAL_JSON if dataset == "Real public reviews" else DEMO_JSON
if not target.exists():
    st.info("No analysed data yet. Click **Fetch latest real reviews** in the sidebar, or run `python run_pipeline.py --real`.")
    st.stop()

doc = load(str(target), target.stat().st_mtime)
meta = doc["meta"]
rec = pd.DataFrame(doc["records"])
rec["app_version"] = rec["app_version"].fillna("n/a")
rec["timestamp"] = pd.to_datetime(rec["timestamp"])

st.title("VoC Co-pilot")
if meta.get("synthetic"):
    st.warning(f"Synthetic demo data: {meta['records']} records, extractor: {meta['backend']}. "
               "Findings illustrate the product and are not about any real app.")
else:
    st.caption(f"Real public reviews: {meta['records']} records, extractor: {meta['backend']}, latest review {meta['as_of']}. "
               "Reviewers are a self-selected group, not a representative sample. Unaffiliated with the app's maker.")

# ------------------------------------------------------------------ filters
sources = sorted(rec["source"].unique())
versions = sorted(rec["app_version"].unique())
src = st.sidebar.multiselect("Source", sources, default=sources)
ver = st.sidebar.multiselect("App version", versions, default=versions)
view = rec[rec["source"].isin(src) & rec["app_version"].isin(ver)]
ids = set(view["id"])
total = len(view)
st.sidebar.metric("Records in view", total)
st.sidebar.caption("Severity and trend columns use all records, not the filtered view.")

pain_ids = {rid for t in doc["themes"] for rid in t["record_ids"]}
churn_ids = {c["record_id"] for c in doc["churn"]}
has_history = any(t["stats"]["total_prior"] > 0 for t in doc["themes"])

tab_over, tab_detail, tab_churn, tab_rec, tab_eval, tab_records = st.tabs(
    ["Overview", "Theme detail", "Churn watchlist", "Recommendations", "Eval health", "Records"])

# ---- overview
with tab_over:
    rows = []
    for t in doc["themes"]:
        hit = set(t["record_ids"]) & ids
        if not hit:
            continue
        row = {
            "Theme": t["label"],
            "Records": len(hit),
            "Share of view": round(100 * len(hit) / total, 1) if total else 0,
            "Avg severity": t["stats"]["avg_severity"],
            "Churn mentions": len(hit & churn_ids),
        }
        if has_history:
            row["14-day change (pp)"] = round(100 * (t["stats"]["share_recent"] - t["stats"]["share_prior"]), 1)
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("Records", ascending=False) if rows else pd.DataFrame()
    st.subheader("Pain points, ranked")
    st.caption("Share of records in the current view that mention each theme."
               + ("" if has_history else " Trends appear once the data covers more than 14 days."))
    if df.empty:
        st.info("No records match the current filters.")
    else:
        cfg = {"Share of view": st.column_config.ProgressColumn("Share of view (%)", min_value=0, max_value=100, format="%.1f")}
        if has_history:
            cfg["14-day change (pp)"] = st.column_config.NumberColumn("14-day change (pp)", format="%+.1f")
        st.dataframe(df, hide_index=True, width="stretch", column_config=cfg)

# ---- theme detail
with tab_detail:
    labels = {t["label"]: t for t in doc["themes"]}
    pick = st.selectbox("Theme", list(labels))
    t = labels[pick]
    st.markdown(f"**{t['finding']}**")
    st.caption("This sentence is generated from database counts and re-checked by the eval suite.")
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("**Verbatim quotes**")
        for q in t["quotes"]:
            st.markdown(f"> {q['quote']}\n\n`{q['record_id']}` · {q['source']} · {q['timestamp'][:10]}")
    with c2:
        st.markdown("**Weekly records mentioning this theme**")
        wk = pd.DataFrame(t["weekly"], columns=["week", "records"]).set_index("week")
        st.bar_chart(wk)
    st.markdown("**Matching records in view**")
    st.dataframe(view[view["id"].isin(t["record_ids"])][["id", "source", "timestamp", "app_version", "rating", "text"]],
                 hide_index=True, width="stretch")

# ---- churn
with tab_churn:
    ch = pd.DataFrame(doc["churn"])
    if not ch.empty:
        ch = ch[ch["record_id"].isin(ids)]
    st.subheader(f"Churn signals ({len(ch)})")
    if ch.empty:
        st.info("No churn signals in the current view.")
    else:
        st.dataframe(ch[["record_id", "source", "timestamp", "trigger_phrase", "competitor", "text"]],
                     hide_index=True, width="stretch")

# ---- recommendations
with tab_rec:
    st.caption("AI-drafted. Effort and dependencies are playbook defaults: confirm with engineering before committing.")
    if not doc["recommendations"]:
        st.info("Not enough evidence yet for recommendations. Fetch more reviews.")
    for r in doc["recommendations"]:
        thin = "  ·  thin evidence" if r.get("thin_evidence") else ""
        with st.expander(f"#{r['rank']}  {r['title']}  ·  priority {r['priority']}{thin}", expanded=r["rank"] == 1):
            if r.get("thin_evidence"):
                st.warning("Based on fewer than 8 records: treat as a lead to check, not a conclusion.")
            st.markdown(f"**Evidence:** {r['finding']}")
            st.markdown(f"**Proposed change:** {r['change']}")
            a, b, c = st.columns(3)
            a.metric("Impact score", r["impact"])
            b.metric("Effort", r["effort_text"])
            c.metric("Owner", r["owner"])
            st.markdown(f"**Depends on:** {r['dependencies']}")
            st.markdown(f"**Compliance note:** {r['compliance']}")

# ---- eval health
with tab_eval:
    ev = doc.get("evals")
    if not ev:
        st.info("Evals were skipped for this run.")
    else:
        (st.success if ev["hard_gates_passed"] else st.error)(
            "Hard gates passed: findings are safe to use." if ev["hard_gates_passed"]
            else "Hard gate failed: do not trust these findings.")
        st.caption(f"Golden set: {ev.get('golden_source') or 'none supplied (accuracy scores need hand-labelled real reviews)'}")
        out = pd.DataFrame([{"Eval": e["name"], "Gate": e["kind"], "Score": e["score"], "Target": e["threshold"],
                             "Status": "pass" if e["passed"] else ("FAIL" if e["kind"] == "hard" else "warn")}
                            for e in ev["results"]])
        st.dataframe(out, hide_index=True, width="stretch")

# ---- raw records
with tab_records:
    only_pain = st.checkbox("Only real pain points", value=True,
                            help="Hide praise and vague reviews: keep reviews where a specific pain point was found.")
    q = st.text_input("Search text")
    v = view[view["id"].isin(pain_ids)] if only_pain else view
    if q:
        v = v[v["text"].str.contains(q, case=False, na=False)]
    st.caption(f"{len(v)} of {len(view)} records")
    st.dataframe(v[["id", "source", "timestamp", "app_version", "rating", "text"]], hide_index=True, width="stretch")
