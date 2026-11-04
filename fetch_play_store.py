"""Fetch public Google Play reviews into the pipeline's CSV format.

    pip install google-play-scraper
    python scripts/fetch_play_store.py --app com.phonepe.app --count 1500 --out data/play_store.csv
    python run_pipeline.py --input data/play_store.csv --golden none --db out/real.db --out out/real.json

NOT run in the build sandbox (it has no outbound access to Google Play), so treat it as untested:
check the first 20 rows by eye before trusting a full run. Review Google Play's terms before collecting
at scale, keep volumes small, and do not publish reviewer names (this script never stores them).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="com.phonepe.app", help="Play Store package id")
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--country", default="in")
    ap.add_argument("--out", default="data/play_store.csv")
    args = ap.parse_args()

    from google_play_scraper import Sort, reviews  # imported late so the rest of the project needs no extra install

    rows, token = [], None
    while len(rows) < args.count:
        batch, token = reviews(args.app, lang=args.lang, country=args.country, sort=Sort.NEWEST,
                               count=min(200, args.count - len(rows)), continuation_token=token)
        if not batch:
            break
        for r in batch:
            text = " ".join((r.get("content") or "").split())
            if len(text) < 8:
                continue  # empty or one-word reviews carry no signal
            rows.append({
                "id": f"pl_{r['reviewId']}",
                "source": "play_store",
                "timestamp": r["at"].strftime("%Y-%m-%d %H:%M:%S"),
                "text": text,
                "rating": r.get("score") or "",
                "app_version": r.get("appVersion") or r.get("reviewCreatedVersion") or "",
                "user_segment": "",
                "is_synthetic": "false",
            })
        if token is None:
            break

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "source", "timestamp", "text", "rating", "app_version", "user_segment", "is_synthetic"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} reviews to {out}")


if __name__ == "__main__":
    main()
