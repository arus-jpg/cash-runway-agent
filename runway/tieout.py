"""Evaluate an exported package with the `formulas` engine and tie it to the Python engine.

This is the proof that the live-formula workbook and the app show the same numbers.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import borrowing_base as bb
from . import forecast as fc

TOL = 1.0  # dollars


class Evaluated:
    def __init__(self, path: Path):
        import formulas  # heavy import; only needed for verification

        self.fname = Path(path).name
        self.sol = formulas.ExcelModel().loads(str(path)).finish().calculate()

    def get(self, ref: str):
        m = re.match(r"^'?(.+?)'?!\$?([A-Z]+)\$?(\d+)$", ref)
        sheet, col, row = m.groups()
        key = f"'[{self.fname}]{sheet.upper()}'!{col}{row}"
        v = self.sol[key].value[0, 0]
        return v.item() if hasattr(v, "item") else v


def tie(path: Path, builder) -> list[str]:
    """Return a list of mismatches (empty = workbook ties to engine)."""
    ev = Evaluated(path)
    out: list[str] = []

    def chk(label, ref, expected, tol=TOL):
        got = ev.get(ref)
        if isinstance(expected, str):
            if got != expected:
                out.append(f"{label}: workbook {got!r} vs engine {expected!r}")
        elif not isinstance(got, (int, float)) or abs(got - expected) > tol:
            out.append(f"{label}: workbook {got!r} vs engine {expected:,.2f}")

    rows, bbc = builder.rows, builder.bbc
    wk = builder.wk
    weekly = {  # workbook row -> engine field (sign flips for disbursements shown negative)
        "ending_cash": ("ending_cash", 1), "beginning_cash": ("beginning_cash", 1),
        "total_receipts": ("total_receipts", 1), "total_disbursements": ("total_disbursements", -1),
        "net_cf_before_revolver": ("net_cf_before_revolver", 1), "cf_interest": ("interest_paid", -1),
        "cf_fee": ("unused_fee_paid", -1), "draws": ("draws", 1), "repayments": ("repayments", -1),
        "ar_end": ("ar_ending", 1), "eligible_ar": ("eligible_ar", 1), "borrowing_base": ("borrowing_base", 1),
        "max_borrowing": ("max_borrowing", 1), "loan_end": ("loan_ending", 1), "availability": ("availability", 1),
        "liquidity": ("liquidity", 1), "int_accr_end": ("interest_accrued_end", 1),
        "fee_accr_end": ("unused_fee_accrued_end", 1),
    }
    for w, r in enumerate(rows):
        for name, (field, sign) in weekly.items():
            chk(f"wk{w + 1} {name}", wk(name, w), sign * r[field])
        cv = r["covenants"]
        chk(f"wk{w + 1} liquidity covenant", wk("cv_liq_st", w), "PASS" if cv["min_liquidity"]["pass"] else "FAIL")
        chk(f"wk{w + 1} EA covenant", wk("cv_ea_st", w), "PASS" if cv["min_excess_availability"]["pass"] else "FAIL")
        chk(f"wk{w + 1} FCCR trigger", wk("cv_fccr_st", w),
            "TRIGGERED" if cv["fccr_springing"]["triggered"] else "No")
    reg = builder.reg
    chk("BBC gross", reg["bbc.gross"], bbc.gross_ar)
    chk("BBC eligible", reg["bbc.eligible"], bbc.eligible_ar)
    chk("BBC borrowing base", reg["bbc.bb"], bbc.borrowing_base)
    chk("BBC availability", reg["bbc.availability"], bbc.availability)
    for cat in bb.INELIGIBLE_CATEGORIES:
        chk(f"BBC ineligible {cat}", reg[f"bbc.inel.{cat}"], -bbc.ineligibles[cat])
    for i, inv in enumerate(bbc.invoices):
        got = ev.get(reg[f"ar.reason.{i}"])
        if (got or "") != inv["reason"]:
            out.append(f"{inv['num']} reason: workbook {got!r} vs engine {inv['reason']!r}")
    v = builder.var
    if v["available"]:
        for t, key in zip(v["tests"], ("var.rec_status", "var.dis_status")):
            chk(t["test"], reg[key], "PASS" if t["pass"] else "FAIL")
            chk(t["test"] + " ratio", reg[key.replace("status", "ratio")], t["ratio"], tol=1e-9)
    s = builder.res["summary"]
    chk("Summary covenant status", reg["sum.Covenant status, all 13 weeks"], "PASS" if s["all_covenants_pass"] else "FAIL")
    chk("Summary lowest liquidity", reg["sum.Lowest liquidity"], s["lowest_liquidity"])
    chk("Summary peak revolver", reg["sum.Peak revolver balance"], s["peak_loan"])
    for label in ("A/R aging buckets less gross A/R", "Opening cash less week-1 beginning cash",
                  "Weeks with an over-advance"):
        chk(f"Integrity: {label}", reg[f"sum.{label}"], 0.0, tol=0.005)
    return out
