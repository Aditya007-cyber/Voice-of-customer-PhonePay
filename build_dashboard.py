"""Embed a results JSON into dashboard/template.html.

  python scripts/build_dashboard.py                       # out/results.json (synthetic demo) -> out/dashboard*.html
  python scripts/build_dashboard.py --real                # out/real.json (real reviews)      -> out/dashboard_real*.html

Writes two files per run:
  <name>.html           complete standalone page (open it in any browser)
  <name>_fragment.html  same page without the <html>/<body> wrapper (for hosts that add their own)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(results: Path, name: str) -> Path:
    data = json.loads(results.read_text(encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    tpl = (ROOT / "dashboard" / "template.html").read_text(encoding="utf-8")
    assert "/*__DATA__*/" in tpl
    fragment = tpl.replace("/*__DATA__*/", payload)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / f"{name}_fragment.html").write_text(fragment, encoding="utf-8")
    full = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            '<style>:root{color-scheme:light}body{margin:0}</style></head><body>\n' + fragment + "\n</body></html>\n")
    (out / f"{name}.html").write_text(full, encoding="utf-8")
    print(f"built out/{name}.html ({len(full)//1024} KB)")
    return out / f"{name}.html"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--results", default=None)
    args = ap.parse_args()
    if args.real:
        build(Path(args.results) if args.results else ROOT / "out" / "real.json", "dashboard_real")
    else:
        build(Path(args.results) if args.results else ROOT / "out" / "results.json", "dashboard")


if __name__ == "__main__":
    main()
