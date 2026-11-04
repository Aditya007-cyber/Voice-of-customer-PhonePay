"""Fetch real, public app-store reviews into the pipeline's row format.

Sources
  - Apple App Store: Apple's public customer-reviews feed (no key needed). At most 10 pages x 50 reviews per
    country storefront, so use several countries for more.
  - Google Play: the open-source `google-play-scraper` package (pip install google-play-scraper). It reads public
    pages, which is unofficial: check Google Play's terms and keep volumes small.

Privacy: reviewer names and profile links are never read into a row. Rows carry only the review id, date,
rating, app version and text.

Network code takes an injectable `get` function so everything except the actual HTTP call is unit-tested.
"""
from __future__ import annotations

import csv
import json
import urllib.request
from pathlib import Path

FIELDS = ["id", "source", "timestamp", "text", "rating", "app_version", "user_segment", "is_synthetic"]
PHONEPE_APPLE_ID = "1170055821"
PHONEPE_PLAY_ID = "com.phonepe.app"
APPLE_URL = "https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortBy=mostRecent/json"


def compose_text(title: str, content: str) -> str:
    """Join an App Store review's title and body into one text, without repeating identical words."""
    title = " ".join((title or "").split())
    content = " ".join((content or "").split())
    if not content or content.lower() == title.lower():
        return title or content
    if not title:
        return content
    return f"{title} {content}" if title[-1] in ".!?" else f"{title}. {content}"


def _label(entry: dict, key: str) -> str:
    v = entry.get(key) or {}
    return v.get("label", "") if isinstance(v, dict) else str(v)


def parse_apple_feed(doc: dict) -> list[dict]:
    """Turn one page of Apple's JSON feed into pipeline rows. Skips the app-metadata entry and empty reviews."""
    entries = (doc.get("feed") or {}).get("entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    rows = []
    for e in entries:
        if "im:rating" not in e:
            continue  # the first entry of some feeds describes the app, not a review
        text = compose_text(_label(e, "title"), _label(e, "content"))
        if len(text) < 2:
            continue
        rows.append({
            "id": f"ap_{_label(e, 'id')}",
            "source": "app_store",
            "timestamp": _label(e, "updated")[:19].replace("T", " "),
            "text": text,
            "rating": _label(e, "im:rating"),
            "app_version": _label(e, "im:version"),
            "user_segment": "",
            "is_synthetic": "false",
        })
    return rows


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (voc-copilot)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def fetch_apple(app_id: str = PHONEPE_APPLE_ID, countries=("in",), pages: int = 10, get=_http_get) -> list[dict]:
    rows, seen = [], set()
    for country in countries:
        for page in range(1, max(1, min(pages, 10)) + 1):
            doc = json.loads(get(APPLE_URL.format(country=country, page=page, app_id=app_id)))
            batch = parse_apple_feed(doc)
            if not batch:
                break
            for r in batch:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    rows.append(r)
    return rows


def fetch_play(app_id: str = PHONEPE_PLAY_ID, count: int = 500, lang: str = "en", country: str = "in") -> list[dict]:
    try:
        from google_play_scraper import Sort, reviews
    except ImportError as e:
        raise RuntimeError("Google Play needs the optional package: pip install google-play-scraper") from e
    rows, token = [], None
    while len(rows) < count:
        batch, token = reviews(app_id, lang=lang, country=country, sort=Sort.NEWEST,
                               count=min(200, count - len(rows)), continuation_token=token)
        if not batch:
            break
        for r in batch:
            text = " ".join((r.get("content") or "").split())
            if len(text) < 2:
                continue
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
    return rows


def read_csv(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def merge_into_csv(path, rows: list[dict]) -> tuple[int, int]:
    """Add new rows (deduplicated by id) to the CSV. Returns (rows added, total rows)."""
    existing = read_csv(path)
    known = {r["id"] for r in existing}
    fresh = [r for r in rows if r["id"] not in known]
    allrows = sorted(existing + fresh, key=lambda r: r["timestamp"])
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in allrows)
    return len(fresh), len(allrows)


def refresh(csv_path, apple: bool = True, play: bool = True, countries=("in",), pages: int = 10,
            play_count: int = 500, apple_fetcher=None, play_fetcher=None) -> dict:
    """Fetch from each enabled source, merge into csv_path, and report per source. One failing source never
    stops the others."""
    report = {"sources": {}, "added": 0, "total": len(read_csv(csv_path))}
    jobs = []
    if apple:
        jobs.append(("app_store", lambda: (apple_fetcher or fetch_apple)(countries=countries, pages=pages)))
    if play:
        jobs.append(("play_store", lambda: (play_fetcher or fetch_play)(count=play_count)))
    for name, job in jobs:
        try:
            rows = job()
            added, total = merge_into_csv(csv_path, rows)
            report["sources"][name] = {"fetched": len(rows), "new": added, "error": None}
            report["added"] += added
            report["total"] = total
        except Exception as e:  # network down, package missing, site changed
            report["sources"][name] = {"fetched": 0, "new": 0, "error": f"{type(e).__name__}: {e}"}
    return report
