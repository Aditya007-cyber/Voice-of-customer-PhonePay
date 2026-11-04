# VoC Co-pilot

Turns messy customer feedback about a UPI payments app into ranked, evidence-backed product decisions.

> "Users are unhappy with onboarding" becomes "30 of 119 records (25%) mention bank linking, up from 12%", with verbatim quotes, churn flags and a recommendation.

**Design rule:** the model tags and drafts, code counts and verifies. Every number comes from a database query, every quote is checked as an exact substring of its source record, and an eval suite blocks the release if either check fails.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py                 # dashboard with the "Fetch latest real reviews" button
python run_pipeline.py --real        # re-run on the real reviews from the command line
python run_pipeline.py               # the synthetic demo dataset, scored against its golden set
python scripts/build_dashboard.py --real   # standalone out/dashboard_real.html (no server needed)
python -m pytest -q                  # 25 tests
```

## Real data

`data/real_feedback.csv` holds **100 real public App Store reviews of PhonePe** (India storefront, 29 Oct to 3 Nov 2026), fetched from Apple's public customer-reviews feed. Reviewer names and profile links were never stored. See `data/real_feedback.SOURCE.txt`.

**The button.** In `app.py`, **Fetch latest real reviews** pulls fresh public reviews, merges them into the CSV (deduplicated by review id), re-runs the full pipeline and evals, and refreshes every tab. Open *Fetch settings* to choose sources, countries and pages.

| Source | How | Limit |
|---|---|---|
| Apple App Store | Public feed, no key (`voc/fetch.py`) | 500 reviews per country (10 pages x 50). Add countries to get more |
| Google Play | `pip install google-play-scraper`, tick it in *Fetch settings* | Unofficial scraping: check Google Play's terms, keep volumes small |
| Reddit | Not built. Needs an app registered with Reddit and its API terms reviewed | |

**Only real pain points.** Praise and vague reviews stay in the database so percentages use the right denominator, but the Records tab shows only reviews where a specific pain point was found (the *Only real pain points* checkbox, on by default). In this sample that is 19 of 100.

## What is real and what is a stand-in

| Part | Status |
|---|---|
| Pipeline, SQLite, SQL counts, quote verification, evals, both dashboards, tests | Working, run end to end |
| Real data (100 App Store reviews) | Real, but a 6-day slice: no trend can be shown yet, and the dashboard says so |
| Synthetic data (420 records) | Generated demo with PII, Hinglish and hard cases. Not about any real app |
| Golden set | `data/golden.json` is **labelled by the generator, not by people**, and only covers the synthetic data. Real data has **no accuracy scores** until you hand-label real reviews |
| Extractor `rules` (default) | Offline keyword baseline. Misses sarcasm and keyword-free complaints and has false positives (for example it flags "I checked with Google, but I can't find, but PhonePe within seconds" as a UX problem) |
| Extractor `llm` | Claude via the Anthropic SDK, every quote verified verbatim. Plumbing is tested with a fake client. **Not run against the live API** |
| Google Play fetch | Written, **never run** (no access when built) |
| Fetch button | Click flow tested with the network call faked. The live Apple call is the same URL pattern the build used to fetch the bundled reviews |
| PII redaction | Rules for phone, UPI ID, email, card, account, Aadhaar, PAN. **Names are not detected** |
| Recommendations | Playbook defaults for effort, owner and dependencies, not estimates. Recommendations built on fewer than 8 records are marked **Thin evidence** |

Timestamps are the App Store feed's own clock (UTC-7).

## Use your own data

```bash
export ANTHROPIC_API_KEY=...
python run_pipeline.py --input my.csv --golden none --backend llm --db out/mine.db --out out/mine.json
```

Any CSV with `id, source, timestamp, text` (plus optional `rating, app_version, user_segment, is_synthetic`) works: support tickets, call notes and interview transcripts follow the same format. `VOC_MODEL` changes the extraction model (default `claude-haiku-5-5`).

## Layout

```
app.py             Streamlit dashboard and the Fetch button
voc/fetch.py       Apple feed + Google Play fetchers, CSV merge, refresh()
voc/redact.py      stage 0  PII removal before anything is analysed
voc/extract.py     stage 1  pain points (rules or Claude), quotes verified
voc/cluster.py     stage 2  themes and sub-themes (thresholds scale with data size)
voc/db.py          SQL counts, windows, weekly series
voc/churn.py       stage 4  churn signals with the exact trigger phrase
voc/recommend.py   stage 5  findings text from counts, playbook recommendations
voc/taxonomy.py    13 categories incl. ads/pop-ups and fees, keywords, playbook
voc/evals.py       count accuracy, quote grounding, PII, schema, extraction F1, churn, purity, hallucination, rubric
dashboard/         standalone light-theme HTML dashboard template
tests/             25 tests, including "the gate fails when a number or quote is wrong"
```

## Eval gates

Hard (block release): count accuracy 100%, quote grounding 100%, zero PII, schema validity, rubric self-check.
Soft (warn, block under a floor; need a golden set): extraction F1 0.85, category accuracy 0.85, cluster purity 0.80, churn recall 0.85 / precision 0.70, hallucination under 2%, recommendation rubric 4.0.

`python run_pipeline.py` exits non-zero when a hard gate fails, so it can run in CI.

## Next steps

1. Run the fetch button with all 10 pages and several countries, then add Google Play from your own machine.
2. Hand-label 150 real reviews as the golden set (two labellers, 20% overlap) and freeze a test split.
3. Run the `llm` backend on the same reviews and compare it with `rules` on the same evals.
4. Add name detection to redaction before using any private data.
5. Collect for more than 14 days so trends and rising themes appear.
6. Add Reddit, WhatsApp screenshot OCR and scheduled fetches.
