"""CLI: python -m runway.cli <generate|forecast|compare|ar|ask|chat>"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import data, forecast as fc

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
DATA_DIR = SAMPLE_DIR  # overridden by --data-dir


def m(x) -> str:
    if x is None:
        return "n/a"
    return f"-${abs(x) / 1e6:,.2f}M" if x < 0 else f"${x / 1e6:,.2f}M"


def load() -> data.Company:
    if not (DATA_DIR / "assumptions.csv").exists():
        data.export_csvs(data.generate(), SAMPLE_DIR)
    return data.load_csvs(DATA_DIR)


def print_forecast(res: dict, company: data.Company) -> None:
    print(f"\nScenario: {res['scenario']['name']}   (min cash policy {m(company.min_cash_policy)})")
    print(f"{'Wk':>2} {'Start':<10} {'Begin':>9} {'Receipts':>9} {'Payroll':>9} {'AP':>9} {'Opex':>9} {'End':>9}")
    for r in res["rows"]:
        flag = " <-- below policy" if r["ending_cash"] < company.min_cash_policy else ""
        print(f"{r['week']:>2} {r['week_start']:<10} {m(r['beginning_cash']):>9} {m(r['receipts']):>9} "
              f"{m(r['payroll']):>9} {m(r['ap']):>9} {m(r['opex']):>9} {m(r['ending_cash']):>9}{flag}")
    s = res["summary"]
    print(f"\nLowest cash: {m(s['lowest_cash'])} (wk {s['lowest_cash_week']}) | "
          f"Week-13 cash: {m(s['ending_cash_week13'])} | Avg weekly net burn: {m(s['avg_weekly_net_burn'])} | "
          f"Runway: {s['runway_months_at_current_burn'] or 'cash generative'} mo")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="runway")
    p.add_argument("--data-dir", help="folder of QuickBooks-style CSVs (default: data/sample)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate", help="regenerate the dummy QuickBooks CSVs in data/sample")
    f = sub.add_parser("forecast", help="show 13-week forecast")
    f.add_argument("--scenario", default="base", choices=list(fc.PRESETS))
    f.add_argument("--csv", help="also write rows to this CSV path")
    c = sub.add_parser("compare", help="compare preset scenarios")
    c.add_argument("names", nargs="*", default=list(fc.PRESETS))
    sub.add_parser("ar", help="top AR collection risks")
    a = sub.add_parser("ask", help="ask the Claude agent one question")
    a.add_argument("question")
    sub.add_parser("chat", help="interactive agent chat")
    args = p.parse_args(argv)

    global DATA_DIR
    if args.data_dir:
        DATA_DIR = Path(args.data_dir)
    if args.cmd == "generate":
        data.export_csvs(data.generate(), SAMPLE_DIR)
        print(f"Wrote dummy QuickBooks CSVs to {SAMPLE_DIR}")
        return 0
    company = load()
    if args.cmd == "forecast":
        res = fc.build(company, fc.PRESETS[args.scenario])
        print_forecast(res, company)
        if args.csv:
            with open(args.csv, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=res["rows"][0].keys())
                w.writeheader()
                w.writerows(res["rows"])
            print(f"Wrote {args.csv}")
    elif args.cmd == "compare":
        print(f"\n{'Scenario':<18} {'Wk13 cash':>10} {'Low cash':>10} {'Low wk':>6} {'Burn/wk':>9} {'Runway mo':>9} {'Below policy':>12}")
        for r in fc.compare(company, args.names):
            print(f"{r['scenario']:<18} {m(r['ending_cash_week13']):>10} {m(r['lowest_cash']):>10} "
                  f"{r['lowest_cash_week']:>6} {m(r['avg_weekly_net_burn']):>9} "
                  f"{str(r['runway_months_at_current_burn'] or 'n/a'):>9} "
                  f"{('wk ' + str(r['first_week_below_policy'])) if r['first_week_below_policy'] is not None else 'never':>12}")
    elif args.cmd == "ar":
        for r in fc.ar_risk(company)[:8]:
            print(f"{r['customer']:<22} {m(r['amount']):>9}  past due {r['days_past_due']:>3}d  "
                  f"risk {r['collect_risk']:.0%}  expected loss ${r['expected_loss']:,}")
    elif args.cmd in ("ask", "chat"):
        from .agent import ask
        if args.cmd == "ask":
            ans, _ = ask(company, args.question, verbose=True)
            print("\n" + ans)
        else:
            hist = None
            print("CFO runway agent. Ctrl+C to exit.")
            while True:
                try:
                    q = input("\nCFO> ").strip()
                except (KeyboardInterrupt, EOFError):
                    return 0
                if q:
                    ans, hist = ask(company, q, hist, verbose=True)
                    print("\n" + ans)
    return 0


if __name__ == "__main__":
    sys.exit(main())
