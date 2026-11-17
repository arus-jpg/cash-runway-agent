"""Budget-to-actual variance: prior week and rolling cumulative, with permitted-variance tests."""
from __future__ import annotations

from .data import Company

VARIANCE_LINES = [("collections", "Collections", "receipt"),
                  ("payroll", "Payroll", "disb"), ("benefits", "Benefits", "disb"),
                  ("vendors", "Vendors / A/P", "disb"), ("facilities", "Rent & facilities", "disb"),
                  ("technology", "Technology / SaaS", "disb"), ("insurance", "Insurance", "disb"),
                  ("taxes", "Taxes", "disb"), ("other_disb", "Other operating", "disb"),
                  ("interest_fees", "Interest & fees", "financing")]
TEST_WINDOW = 4  # rolling weeks for the permitted-variance test


def _pct(actual: float, forecast: float):
    return actual / forecast if forecast else None


def report(company: Company) -> dict:
    hist = company.variance
    if not hist:
        return {"weeks": [], "lines": [], "tests": [], "available": False}
    weeks = sorted({v.week_ending for v in hist})
    window = weeks[-TEST_WINDOW:]
    prior = weeks[-1]
    known = {k for k, _, _ in VARIANCE_LINES}
    unknown = sorted({v.line for v in hist} - known)
    if unknown:
        raise ValueError(f"variance_history.csv: unknown line(s) {unknown}; use {sorted(known)}")

    def tot(line, wks, attr):
        return sum(getattr(v, attr) for v in hist if v.line == line and v.week_ending in wks)

    lines = []
    for key, label, kind in VARIANCE_LINES:
        pf, pa = tot(key, [prior], "forecast"), tot(key, [prior], "actual")
        cf, ca = tot(key, window, "forecast"), tot(key, window, "actual")
        # favorable = more receipts or fewer disbursements than forecast
        sign = 1 if kind == "receipt" else -1
        lines.append({"line": key, "label": label, "kind": kind,
                      "prior_forecast": pf, "prior_actual": pa, "prior_var": pa - pf,
                      "prior_fav": sign * (pa - pf), "prior_pct": _pct(pa, pf),
                      "cum_forecast": cf, "cum_actual": ca, "cum_var": ca - cf,
                      "cum_fav": sign * (ca - cf), "cum_pct": _pct(ca, cf)})

    def group(kind, prefix):
        g = [x for x in lines if x["kind"] == kind]
        return {f"{prefix}_forecast": sum(x[f"{prefix}_forecast"] for x in g),
                f"{prefix}_actual": sum(x[f"{prefix}_actual"] for x in g)}

    rec = {**group("receipt", "cum"), **group("receipt", "prior")}
    dis = {**group("disb", "cum"), **group("disb", "prior")}
    t = company.terms
    rec_ratio = _pct(rec["cum_actual"], rec["cum_forecast"])
    dis_ratio = _pct(dis["cum_actual"], dis["cum_forecast"])
    tests = [
        {"test": f"Receipts >= {t.variance_receipts_min:.0%} of forecast (rolling {len(window)} wks)",
         "forecast": rec["cum_forecast"], "actual": rec["cum_actual"], "ratio": rec_ratio,
         "limit": t.variance_receipts_min, "pass": rec_ratio is None or rec_ratio >= t.variance_receipts_min},
        {"test": f"Operating disbursements <= {t.variance_disb_max:.0%} of forecast (rolling {len(window)} wks)",
         "forecast": dis["cum_forecast"], "actual": dis["cum_actual"], "ratio": dis_ratio,
         "limit": t.variance_disb_max, "pass": dis_ratio is None or dis_ratio <= t.variance_disb_max},
    ]
    return {"weeks": weeks, "window": window, "prior_week": prior, "lines": lines,
            "receipts": rec, "disbursements": dis, "tests": tests, "available": True}
