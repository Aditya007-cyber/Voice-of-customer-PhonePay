"""Run the whole VoC pipeline: ingest -> redact -> extract -> cluster -> quotes -> churn -> recommend -> evals.

  python run_pipeline.py                                  # synthetic demo data, offline rules backend
  python run_pipeline.py --real                           # the real public reviews in data/real_feedback.csv
  python run_pipeline.py --input my.csv --golden none     # your own CSV: id,source,timestamp,text[,rating,app_version,user_segment,is_synthetic]
  VOC_BACKEND=llm ANTHROPIC_API_KEY=... python run_pipeline.py --real   # Claude extraction (quotes are verified verbatim)
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from voc import db, pipeline

ROOT = Path(__file__).resolve().parent
REAL_CSV = ROOT / "data" / "real_feedback.csv"


def run(input_csv, db_path, out_path, backend="rules", golden_path=None, log=print):
    """Run every stage and the eval suite. Returns (results document, eval report)."""
    Path(db_path).unlink(missing_ok=True)  # always rebuild from the CSV so stale rows never leak in
    conn = db.connect(str(db_path))
    db.init(conn)
    rows = pipeline.load_csv(input_csv)
    pii = pipeline.ingest(conn, rows)
    log(f"stage 0    ingested {len(rows)} records, redacted PII: {pii or 'none found'}")
    cl = pipeline.run(conn, backend=backend, log=log)
    doc = pipeline.export(conn, cl, str(out_path), pii_total=pii, backend=backend)

    from voc import evals
    golden = None
    if golden_path and str(golden_path).lower() != "none" and Path(golden_path).exists():
        golden = json.loads(Path(golden_path).read_text())
    report = evals.run_all(conn, doc, golden)
    doc["evals"] = report
    Path(out_path).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="use data/real_feedback.csv, out/real.db, out/real.json, no golden set")
    ap.add_argument("--input", default=None)
    ap.add_argument("--golden", default=None, help="golden JSON path, or 'none'")
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--backend", default=os.environ.get("VOC_BACKEND", "rules"), choices=["rules", "llm"])
    args = ap.parse_args()

    if args.real:
        input_csv = args.input or REAL_CSV
        db_path = args.db or ROOT / "out" / "real.db"
        out_path = args.out or ROOT / "out" / "real.json"
        golden = args.golden or "none"
    else:
        input_csv = args.input or ROOT / "data" / "feedback.csv"
        db_path = args.db or ROOT / "out" / "voc.db"
        out_path = args.out or ROOT / "out" / "results.json"
        golden = args.golden or ROOT / "data" / "golden.json"

    doc, report = run(input_csv, db_path, out_path, backend=args.backend, golden_path=golden)
    from voc import evals
    evals.print_report(report)
    if not report["hard_gates_passed"]:
        raise SystemExit("HARD GATE FAILED: do not ship these findings")
    print(f"done. results: {out_path}")


if __name__ == "__main__":
    main()
