"""Fetcher, real-data pipeline and the Fetch button. The network call itself is faked; the rest runs for real."""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voc import db, evals, fetch, pipeline  # noqa: E402
from voc.churn import detect_churn  # noqa: E402
from voc.extract import extract_rules  # noqa: E402

# Three entries copied from the public feed shape (names kept here on purpose: they must NOT reach a row).
FEED = {"feed": {"entry": [
    {"author": {"name": {"label": "Some Reviewer"}, "uri": {"label": "https://itunes.apple.com/in/reviews/id1"}},
     "updated": {"label": "2026-11-02T13:53:53-07:00"}, "im:rating": {"label": "1"}, "im:version": {"label": "25.0613.05"},
     "id": {"label": "14743337390"}, "title": {"label": "App stopped working on iPhone 7 Plus ios 15"},
     "content": {"label": "App stopped working on ios15.8.8 so I stopped using this app. Bye bye."}},
    {"author": {"name": {"label": "Another Person"}}, "updated": {"label": "2026-11-02T06:52:00-07:00"},
     "im:rating": {"label": "5"}, "im:version": {"label": "26.1009.05"}, "id": {"label": "14742028234"},
     "title": {"label": "Good"}, "content": {"label": "Good"}},
    {"id": {"label": "1170055821"}, "title": {"label": "PhonePe app metadata entry"}},   # no rating: not a review
]}}


def test_compose_text():
    assert fetch.compose_text("Good", "Good") == "Good"
    assert fetch.compose_text("Server Down", "server is down daily") == "Server Down. server is down daily"
    assert fetch.compose_text("Wow!", "so fast") == "Wow! so fast"
    assert fetch.compose_text("", "only body") == "only body"


def test_parse_apple_feed_keeps_reviews_and_drops_names():
    rows = fetch.parse_apple_feed(FEED)
    assert [r["id"] for r in rows] == ["ap_14743337390", "ap_14742028234"]
    r = rows[0]
    assert r["timestamp"] == "2026-11-02 13:53:53" and r["rating"] == "1" and r["app_version"] == "25.0613.05"
    assert r["is_synthetic"] == "false" and r["source"] == "app_store"
    blob = json.dumps(rows)
    assert "Some Reviewer" not in blob and "Another Person" not in blob and "itunes.apple.com/in/reviews" not in blob
    assert set(r) == set(fetch.FIELDS)


def test_fetch_apple_stops_at_empty_page_and_dedups():
    calls = []

    def get(url):
        calls.append(url)
        page = int(url.split("page=")[1].split("/")[0])
        return json.dumps(FEED if page <= 2 else {"feed": {}}).encode()

    rows = fetch.fetch_apple(countries=("in",), pages=10, get=get)
    assert len(rows) == 2                 # page 2 repeats page 1: deduplicated
    assert len(calls) == 3                # stopped at the first empty page
    assert "/in/rss/customerreviews/page=1/id=1170055821/sortBy=mostRecent/json" in calls[0]


def test_merge_into_csv_dedups_and_sorts(tmp_path):
    p = tmp_path / "r.csv"
    rows = fetch.parse_apple_feed(FEED)
    assert fetch.merge_into_csv(p, rows) == (2, 2)
    assert fetch.merge_into_csv(p, rows) == (0, 2)           # nothing new the second time
    newer = dict(rows[0], id="ap_1", timestamp="2026-11-04 01:00:00")
    assert fetch.merge_into_csv(p, [newer]) == (1, 3)
    with p.open(newline="", encoding="utf-8") as f:
        ts = [r["timestamp"] for r in csv.DictReader(f)]
    assert ts == sorted(ts)


def test_refresh_one_failing_source_does_not_stop_the_other(tmp_path):
    def boom(**_):
        raise RuntimeError("no network")

    rep = fetch.refresh(tmp_path / "r.csv", apple=True, play=True,
                        apple_fetcher=lambda **_: fetch.parse_apple_feed(FEED), play_fetcher=boom)
    assert rep["sources"]["app_store"] == {"fetched": 2, "new": 2, "error": None}
    assert "no network" in rep["sources"]["play_store"]["error"]
    assert rep["added"] == 2 and rep["total"] == 2


# ------------------------------------------------------------------ the bundled real reviews
@pytest.fixture(scope="module")
def real(tmp_path_factory):
    d = tmp_path_factory.mktemp("real")
    conn = db.connect(str(d / "real.db"))
    db.init(conn)
    rows = pipeline.load_csv(ROOT / "data" / "real_feedback.csv")
    pii = pipeline.ingest(conn, rows)
    cl = pipeline.run(conn, backend="rules", log=lambda *_: None)
    doc = pipeline.export(conn, cl, str(d / "r.json"), pii_total=pii, backend="rules")
    return rows, conn, doc


def test_real_data_is_real_and_has_no_reviewer_names(real):
    rows, _, _ = real
    assert len(rows) == 100 and all(r["is_synthetic"] == "false" for r in rows)
    assert all(r["id"].startswith("ap_") for r in rows)
    assert set(rows[0]) == set(fetch.FIELDS)


def test_real_pipeline_passes_hard_gates_without_a_golden_set(real):
    _, conn, doc = real
    rep = evals.run_all(conn, doc, None)
    assert rep["hard_gates_passed"], [e["id"] for e in rep["results"] if e["kind"] == "hard" and not e["passed"]]


def test_real_findings_do_not_invent_a_trend(real):
    """The data spans under 14 days, so no finding may claim to be rising or falling."""
    _, _, doc = real
    for t in doc["themes"]:
        assert t["stats"]["total_prior"] == 0
        assert "up from" not in t["finding"] and "down from" not in t["finding"]
        assert "no earlier period" in t["finding"]


def test_new_taxonomy_catches_real_complaints():
    cases = {
        "Too much add. Too much add every single click has add": "ads_popups",
        "Keep getting Pop-up for loan application. While doing UPI payment, getting pop-up for loan in between": "ads_popups",
        "Collecting extra amount. Not fast": "fees_charges",
        "Server Down. Daily exact 11:45 pm to 12:30 am server is down": "app_performance",
        "Worst app. Can not reach customer care person": "customer_support",
    }
    for text, cat in cases.items():
        got = {p["category"] for p in extract_rules(text)["pain_points"]}
        assert cat in got, (text, got)


def test_praise_and_train_tickets_are_not_pain_points():
    assert extract_rules("Excellent. User friendly interface")["pain_points"] == []
    got = {p["category"] for p in extract_rules("I am not able to book IXIGO TRAIN TICKETS.")["pain_points"]}
    assert "customer_support" not in got


def test_curly_apostrophes_still_match_and_quotes_stay_verbatim():
    text = "Raised issue but didn’t get response."
    pp = extract_rules(text)["pain_points"]
    assert pp and pp[0]["category"] == "customer_support" and pp[0]["evidence_quote"] in text
    ch = detect_churn("I won’t use this app again, so I stopped using it")
    assert ch["flag"] and ch["trigger_phrase"] in "I won’t use this app again, so I stopped using it"


# ------------------------------------------------------------------ the button, end to end (network faked)
def test_fetch_button_updates_data_and_dashboard(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    (tmp_path / "data").mkdir()
    (tmp_path / "out").mkdir()
    rows = fetch.read_csv(ROOT / "data" / "real_feedback.csv")[:30]
    fetch.merge_into_csv(tmp_path / "data" / "real_feedback.csv", rows)
    monkeypatch.setenv("VOC_HOME", str(tmp_path))

    new = dict(rows[0], id="ap_999000111", timestamp="2026-11-04 08:00:00",
               text="Server Down. server is down again every night")
    monkeypatch.setattr(fetch, "fetch_apple", lambda **_: [new])

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    btn = next(b for b in at.sidebar.button if "Fetch latest real reviews" in b.label)
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]

    assert (tmp_path / "out" / "real.json").exists()
    doc = json.loads((tmp_path / "out" / "real.json").read_text())
    assert doc["meta"]["records"] == 31 and doc["evals"]["hard_gates_passed"]
    assert any(r["id"] == "ap_999000111" for r in doc["records"])
    assert any("1 new" in s.value for s in at.sidebar.success)
