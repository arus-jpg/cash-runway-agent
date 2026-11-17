"""Regenerate the dashboard data for docs/.

Writes docs/data.json (consumed by docs/index.html over HTTP / GitHub Pages) and
refreshes the inline <script id="inline-data"> fallback block so the page also works
when opened directly from disk (file://).

Usage:
    py scripts/build_dashboard.py                 # uses data/sample
    py scripts/build_dashboard.py --data-dir data/private
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
sys.path.insert(0, str(ROOT))

from runway import data, forecast as fc  # noqa: E402


def build_payload(data_dir: pathlib.Path) -> dict:
    company = data.load_csvs(data_dir)
    payload = {
        "as_of": company.as_of,
        "min_cash_policy": company.min_cash_policy,
        "opening_cash": company.opening_cash,
        "scenarios": {},
        "compare": fc.compare(company, list(fc.PRESETS)),
        "ar_risk": fc.ar_risk(company)[:8],
    }
    payload["monthly_net_new_arr"] = round(company.monthly_net_new_arr)
    for name in fc.PRESETS:
        res = fc.build(company, fc.PRESETS[name])
        payload["scenarios"][name] = {
            "rows": res["rows"],
            "summary": res["summary"],
            "band": fc.simulate(company, fc.PRESETS[name]),
        }
    return payload


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="data/sample")
    args = p.parse_args(argv)

    payload = build_payload(ROOT / args.data_dir)
    text = json.dumps(payload, indent=2)

    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.json").write_text(text, encoding="utf-8")

    index = DOCS / "index.html"
    html = index.read_text(encoding="utf-8")
    block = '<script id="inline-data" type="application/json">\n' + text + "\n</script>"
    pattern = re.compile(
        r'<script id="inline-data" type="application/json">.*?</script>', re.S
    )
    if pattern.search(html):
        html = pattern.sub(block, html)
    else:
        html = html.replace("</body>", block + "\n</body>", 1)
    index.write_text(html, encoding="utf-8")

    print(f"Wrote {DOCS / 'data.json'} and refreshed inline fallback in {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
