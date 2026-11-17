"""CLI: python -m runway.cli [--data-dir DIR] <forecast|bbc|covenants|variance|compare|export>"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import borrowing_base as bb, data, forecast as fc, variance

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "data" / "sample"


def m(x) -> str:
    if x is None:
        return "n/a"
    if abs(x) < 500:
        return "-"
    return f"({abs(x) / 1e3:,.0f})" if x < 0 else f"{x / 1e3:,.0f}"


def print_forecast(res: dict) -> None:
    rows = res["rows"]
    print(f"\nScenario: {res['scenario']['label']}   ($000s)")
    cols = [("Receipts", "total_receipts", 1), ("Disburse", "total_disbursements", -1),
            ("Int/fees", None, -1), ("Draw", "draws", 1), ("Repay", "repayments", -1),
            ("End cash", "ending_cash", 1), ("Revolver", "loan_ending", 1), ("Avail", "availability", 1),
            ("Liquidity", "liquidity", 1)]
    print(f"{'Wk':>2} {'Begins':<10} " + " ".join(f"{c[0]:>9}" for c in cols) + "  Covenants")
    for r in rows:
        vals = []
        for _lab, key, sign in cols:
            v = (r["interest_paid"] + r["unused_fee_paid"]) if key is None else r[key]
            vals.append(m(sign * v))
        fails = [fc.COVENANT_LABELS[k] for k in fc.COVENANT_LABELS if not r["covenants"][k]["pass"]]
        flag = "FAIL: " + ", ".join(fails) if fails else "pass"
        if r["covenants"]["fccr_springing"]["triggered"]:
            flag += " | FCCR springs"
        print(f"{r['week']:>2} {r['week_start']:<10} " + " ".join(f"{v:>9}" for v in vals) + f"  {flag}")
    s = res["summary"]
    print(f"\nEnding cash {m(s['ending_cash'])} | lowest liquidity {m(s['lowest_liquidity'])} (wk "
          f"{s['lowest_liquidity_week']}) | peak revolver {m(s['peak_loan'])} | covenants: "
          f"{'PASS all 13 weeks' if s['all_covenants_pass'] else 'FAIL from week ' + str(s['first_breach_week'])}")


def print_bbc(company: data.Company) -> None:
    b = bb.compute(company)
    print(f"\nBorrowing Base Certificate - {company.name} - as of {company.as_of}")
    print(f"{'Gross A/R':<44}{b.gross_ar:>14,.0f}")
    for cat, amt in b.ineligibles.items():
        print(f"  less {cat:<39}{-amt:>14,.0f}")
    print(f"{'Eligible A/R':<44}{b.eligible_ar:>14,.0f}   ({b.eligible_pct:.1%} of gross)")
    print(f"{'x advance rate ' + format(company.terms.advance_rate, '.0%'):<44}{b.gross_availability:>14,.0f}")
    print(f"{'less availability block':<44}{-company.terms.availability_block:>14,.0f}")
    print(f"{'Borrowing base':<44}{b.borrowing_base:>14,.0f}")
    print(f"{'Maximum borrowing (lesser of commitment/BB)':<44}{b.max_borrowing:>14,.0f}")
    print(f"{'less revolver outstanding':<44}{-b.loan:>14,.0f}")
    print(f"{'Availability':<44}{b.availability:>14,.0f}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="runway", description="Lender 13-week cash flow & borrowing base")
    p.add_argument("--data-dir", default=str(SAMPLE_DIR), help="folder of input CSVs (default: data/sample)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, hlp in [("forecast", "13-week cash flow with revolver and covenant status"),
                      ("covenants", "weekly covenant tests")]:
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("--scenario", default="base")
    sub.add_parser("bbc", help="borrowing base certificate as of the as-of date")
    sub.add_parser("variance", help="budget-to-actual variance and permitted-variance tests")
    sub.add_parser("compare", help="all lender sensitivities side by side")
    e = sub.add_parser("export", help="write the Excel lender package")
    e.add_argument("--scenario", default="base")
    e.add_argument("--out", help="output .xlsx path (default: out/<as-of> Lender Package.xlsx)")
    e.add_argument("--no-verify", action="store_true", help="skip the workbook-to-engine tie-out")
    args = p.parse_args(argv)

    company = data.load_csvs(Path(args.data_dir))
    presets = fc.presets(company)
    scen = getattr(args, "scenario", "base")
    if scen not in presets:
        p.error(f"unknown scenario {scen!r}; choose from {', '.join(presets)}")

    if args.cmd == "forecast":
        print_forecast(fc.build(company, presets[scen]))
    elif args.cmd == "covenants":
        res = fc.build(company, presets[scen])
        for r in res["rows"]:
            cv = r["covenants"]
            print(f"wk {r['week']:>2}  liquidity {m(cv['min_liquidity']['actual']):>7} vs {m(cv['min_liquidity']['required'])}"
                  f" {'PASS' if cv['min_liquidity']['pass'] else 'FAIL'} | availability "
                  f"{m(cv['min_excess_availability']['actual']):>6} vs {m(cv['min_excess_availability']['required'])} "
                  f"{'PASS' if cv['min_excess_availability']['pass'] else 'FAIL'}"
                  f"{' | FCCR springs' if cv['fccr_springing']['triggered'] else ''}")
    elif args.cmd == "bbc":
        print_bbc(company)
    elif args.cmd == "variance":
        v = variance.report(company)
        if not v["available"]:
            print("no variance_history.csv")
            return 0
        print(f"\nPrior week ending {v['prior_week']} | rolling window {v['window'][0]} - {v['window'][-1]}")
        print(f"{'Line':<22}{'Prior fcst':>12}{'Prior act':>12}{'Fav/(unfav)':>12}{'Cum fcst':>12}{'Cum act':>12}{'Fav/(unfav)':>12}")
        for x in v["lines"]:
            z = lambda n: f"{n + 0.0:>12,.0f}"  # noqa: E731  (+0.0 turns -0 into 0)
            print(f"{x['label']:<22}" + "".join(z(x[k]) for k in ("prior_forecast", "prior_actual", "prior_fav",
                                                                  "cum_forecast", "cum_actual", "cum_fav")))
        for t in v["tests"]:
            print(f"{t['test']}: {t['ratio']:.1%} -> {'PASS' if t['pass'] else 'FAIL'}")
    elif args.cmd == "compare":
        print(f"\n{'Scenario':<48}{'End cash':>10}{'Low liq':>10}{'Wk':>4}{'Peak RLOC':>10}{'Covenants':>11}")
        for r in fc.compare(company, presets):
            print(f"{r['label']:<48}{m(r['ending_cash']):>10}{m(r['lowest_liquidity']):>10}"
                  f"{r['lowest_liquidity_week']:>4}{m(r['peak_loan']):>10}"
                  f"{('PASS' if r['all_covenants_pass'] else 'FAIL wk ' + str(r['first_breach_week'])):>11}")
    elif args.cmd == "export":
        from . import excel_export as xe
        out = Path(args.out) if args.out else ROOT / "out" / f"{date.fromisoformat(company.as_of):%Y-%m-%d} Lender Package.xlsx"
        _, _, b = xe.export(company, presets[scen], path=out, scenarios=presets)
        print(f"Wrote {out}")
        if not args.no_verify:
            from . import tieout
            bad = tieout.tie(out, b)
            if bad:
                print(f"TIE-OUT FAILED ({len(bad)} differences):")
                print("\n".join("  " + x for x in bad[:20]))
                return 1
            print("Tie-out: workbook formulas agree with the engine in every week (0 differences).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
