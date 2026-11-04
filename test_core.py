"""Run: python -m pytest -q

Covers the trust properties the product depends on: PII never reaches analysis, quotes are verbatim,
numbers match a recount, and the eval gates actually fail when something is wrong.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voc import db, evals, pipeline  # noqa: E402
from voc.churn import detect_churn  # noqa: E402
from voc.extract import extract_llm, extract_rules  # noqa: E402
from voc.redact import has_pii, redact  # noqa: E402


# ------------------------------------------------------------------ helpers
class FakeClient:
    """Stands in for anthropic.Anthropic(): returns a canned JSON string."""

    def __init__(self, payload):
        self.payload = payload
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Full pipeline on the bundled synthetic data (rules backend, offline)."""
    d = tmp_path_factory.mktemp("voc")
    conn = db.connect(str(d / "t.db"))
    db.init(conn)
    rows = pipeline.load_csv(ROOT / "data" / "feedback.csv")
    pii = pipeline.ingest(conn, rows)
    cl = pipeline.run(conn, backend="rules", log=lambda *_: None)
    doc = pipeline.export(conn, cl, str(d / "r.json"), pii_total=pii, backend="rules")
    golden = json.loads((ROOT / "data" / "golden.json").read_text())
    return conn, doc, golden


# ------------------------------------------------------------------ redaction
def test_redaction_removes_all_pii_types():
    raw = ("Call me on 98765 43210 or +91 9123456780, mail raj.k@example.com, "
           "UPI raj.k@ybl, account 123456789012, card 4111 1111 1111 1111.")
    red, counts = redact(raw)
    assert has_pii(red) == []
    for needle in ("98765", "9123456780", "raj.k", "123456789012", "4111"):
        assert needle not in red
    assert {"PHONE", "EMAIL", "UPI_ID", "CARD"} <= set(counts) or {"PHONE", "EMAIL", "UPI_ID", "ACCOUNT"} <= set(counts)


# ------------------------------------------------------------------ extraction
def test_rules_quotes_are_verbatim():
    text = "Money got deducted but the transaction shows pending. Refund has not come in 10 days."
    out = extract_rules(text)
    assert out["pain_points"], "expected pain points"
    assert {p["category"] for p in out["pain_points"]} >= {"refund_delay"}
    for p in out["pain_points"]:
        assert p["evidence_quote"] in text
        assert 1 <= p["severity"] <= 5


def test_rules_praise_is_not_a_pain_point():
    assert extract_rules("Great app, super smooth and fast payments.")["pain_points"] == []


def test_llm_backend_drops_invented_quotes_and_categories():
    text = "The OTP never came during bank linking. Everything else was fine."
    payload = {"pain_points": [
        {"issue": "OTP missing", "category": "onboarding_bank_linking", "severity": 4,
         "sentiment": "negative", "evidence_quote": "The OTP never came during bank linking."},
        {"issue": "invented", "category": "refund_delay", "severity": 5,
         "sentiment": "negative", "evidence_quote": "My refund took a month"},          # not in the text
        {"issue": "bad category", "category": "made_up_category", "severity": 3,
         "sentiment": "negative", "evidence_quote": "Everything else was fine."},        # not in taxonomy
    ]}
    out = extract_llm(text, client=FakeClient(payload))
    assert [p["category"] for p in out["pain_points"]] == ["onboarding_bank_linking"]
    assert out["rejected"] == 2


def test_llm_backend_survives_non_json():
    out = extract_llm("anything", client=FakeClient("sorry, I cannot help with that"))
    assert out["pain_points"] == []


def test_llm_backend_clamps_bad_severity():
    text = "App crashes on launch."
    payload = {"pain_points": [{"issue": "crash", "category": "app_performance", "severity": "9",
                                "sentiment": "negative", "evidence_quote": "App crashes on launch."}]}
    out = extract_llm(text, client=FakeClient(payload))
    assert out["pain_points"][0]["severity"] == 5


# ------------------------------------------------------------------ churn
def test_churn_flags_with_exact_trigger_phrase():
    text = "Too many failures. Switching to Google Pay now."
    ch = detect_churn(text)
    assert ch["flag"] and ch["trigger_phrase"] in text


def test_churn_ignores_unrelated_text():
    assert detect_churn("Please add a bill split feature.")["flag"] is False


# ------------------------------------------------------------------ end to end + eval gates
def test_pipeline_passes_hard_gates(built):
    conn, doc, golden = built
    rep = evals.run_all(conn, doc, golden)
    failed = [e["id"] for e in rep["results"] if e["kind"] == "hard" and not e["passed"]]
    assert failed == [] and rep["hard_gates_passed"]


def test_no_pii_in_stored_analysis_text(built):
    conn, _, _ = built
    for (t,) in conn.execute("SELECT redacted_text FROM records"):
        assert has_pii(t) == []


def test_count_gate_fails_on_a_wrong_number(built):
    conn, doc, _ = built
    bad = copy.deepcopy(doc)
    first = bad["themes"][0]["finding"]
    n = int(first.split(" of ")[0])
    bad["themes"][0]["finding"] = first.replace(f"{n} of", f"{n + 5} of", 1)
    res = evals.eval_count_accuracy(conn, bad)
    assert res["passed"] is False and res["kind"] == "hard"


def test_quote_gate_fails_on_a_fabricated_quote(built):
    conn, doc, _ = built
    bad = copy.deepcopy(doc)
    bad["themes"][0]["quotes"][0]["quote"] = "This sentence was never written by any customer."
    res = evals.eval_quote_grounding(conn, bad)
    assert res["passed"] is False and res["kind"] == "hard"


def test_llm_backend_plumbing_end_to_end(tmp_path):
    """Whole pipeline with a fake Claude client on a 40-record slice: nothing breaks, quotes stay verbatim."""
    rows = pipeline.load_csv(ROOT / "data" / "feedback.csv")[:40]
    conn = db.connect(str(tmp_path / "llm.db"))
    db.init(conn)
    pipeline.ingest(conn, rows)

    class Echo(FakeClient):
        def _create(self, **kw):
            prompt = kw["messages"][0]["content"]
            body = prompt.split('"""')[1].strip()
            if "refund" in body.lower():
                pp = [{"issue": "refund", "category": "refund_delay", "severity": 3,
                       "sentiment": "negative", "evidence_quote": body}]
            else:
                pp = []
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"pain_points": pp}))])

    pipeline.run(conn, backend="llm", llm_client=Echo({}), log=lambda *_: None)
    red = {r["id"]: r["redacted_text"] for r in conn.execute("SELECT id, redacted_text FROM records")}
    for r in conn.execute("SELECT record_id, evidence_quote FROM pain_points"):
        assert r["evidence_quote"] in red[r["record_id"]]
