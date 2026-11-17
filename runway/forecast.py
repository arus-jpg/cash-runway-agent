"""13-week cash flow in lender format, with the revolver, borrowing base and covenants.

Operating lines (receipts, disbursements, one-time items) come from the schedules
below. Everything financing-related is then solved week by week, in an order with no
circularity, and the Excel export rebuilds that same order in live formulas:

  1. A/R roll-forward: beginning + billings - collections - write-offs
  2. Projected borrowing base: ending A/R x as-of eligible % x advance rate - reserves
  3. Interest and unused fee: accrue on the week's beginning loan balance,
     paid in arrears in the week that contains the 1st of the month
  4. Cash before revolver = beginning cash + operating flows - interest/fees paid
  5. Draw to hold minimum operating cash (capped at availability); repay any
     over-advance and sweep cash above min cash + buffer
  6. Availability = min(commitment, borrowing base) - loan; liquidity = cash + availability
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta

from . import borrowing_base as bb
from .data import Company

WEEKS = 13

RECEIPT_LINES = [("collections_ar", "Collections - existing A/R"),
                 ("collections_new", "Collections - new billings")]
DISB_LINES = [("payroll", "Payroll"), ("benefits", "Benefits"), ("vendors", "Vendors / A/P"),
              ("facilities", "Rent & facilities"), ("technology", "Technology / SaaS"),
              ("insurance", "Insurance"), ("taxes", "Taxes"), ("other_disb", "Other operating")]
DISB_KEYS = [k for k, _ in DISB_LINES]


@dataclass
class Scenario:
    name: str = "base"
    label: str = "Base case"
    collections_delay_days: int = 0      # extra days customers take to pay
    billings_change_pct: float = 0.0     # change in new weekly billings, e.g. -0.2
    opex_change_pct: float = 0.0         # change in recurring opex and vendor run-rate
    bad_debt_multiplier: float = 1.0     # scales each invoice's non-collection risk
    stalled_customers: list[str] = field(default_factory=list)  # pay nothing in the window
    one_time_items: list[tuple[int, float, str]] = field(default_factory=list)  # (week 1-13, amount, label); negative = outflow


def largest_customer(company: Company) -> tuple[str, float]:
    by: dict[str, float] = {}
    for i in company.invoices:
        by[i.customer] = by.get(i.customer, 0.0) + i.amount
    name = max(by, key=by.get)
    return name, by[name] / sum(by.values())


def presets(company: Company) -> dict[str, Scenario]:
    big, share = largest_customer(company)
    return {
        "base": Scenario("base", "Base case"),
        "slow_collections": Scenario("slow_collections", "Collections +15 days", collections_delay_days=15),
        "billings_down_20": Scenario("billings_down_20", "Billings -20%", billings_change_pct=-0.20),
        "lose_largest_customer": Scenario(
            "lose_largest_customer", f"Loss of largest customer ({big})",
            stalled_customers=[big], billings_change_pct=-round(share, 4)),
        "downside": Scenario("downside", "Downside (slow + billings -20% + 2x bad debt)",
                             collections_delay_days=15, billings_change_pct=-0.20, bad_debt_multiplier=2.0),
    }


def _week_idx(d: date, start: date) -> int:
    return max(0, (d - start).days // 7)


def _recurring_dates(freq: str, dom: int, start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        if freq == "monthly" or (freq == "quarterly" and d.month % 3 == 0):
            due = d.replace(day=min(dom, 28))
            if start <= due <= end:
                yield due
        d = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)


def week_dates(company: Company) -> list[tuple[date, date]]:
    start = date.fromisoformat(company.as_of)
    return [(start + timedelta(days=7 * w), start + timedelta(days=7 * w + 6)) for w in range(WEEKS)]


def operating_schedule(company: Company, sc: Scenario) -> dict[str, list[float]]:
    """Weekly operating lines plus the A/R roll drivers (billings, write-offs)."""
    start = date.fromisoformat(company.as_of)
    end = start + timedelta(days=WEEKS * 7 - 1)
    z = lambda: [0.0] * WEEKS  # noqa: E731
    s = {k: z() for k, _ in RECEIPT_LINES + DISB_LINES}
    s.update(one_time=z(), billings=z(), write_offs=z())

    # Existing A/R: expected pay date = due + customer's typical lateness + scenario delay.
    # Invoices already past that date are spread evenly over weeks 1-4 rather than all
    # landing in week 1 (a lender will not accept a week-1 catch-up of every past-due item).
    # The expected loss (collect_risk) is written off in the same week the cash lands.
    for inv in company.invoices:
        if inv.customer in sc.stalled_customers:
            continue
        expected = date.fromisoformat(inv.due_date) + timedelta(days=inv.typical_days_late + sc.collections_delay_days)
        p = 1 - min(1.0, inv.collect_risk * sc.bad_debt_multiplier)
        weeks = [_week_idx(expected, start)] if expected >= start else [0, 1, 2, 3]
        for wk in weeks:
            if wk < WEEKS:
                s["collections_ar"][wk] += inv.amount * p / len(weeks)
                s["write_offs"][wk] += inv.amount * (1 - p) / len(weeks)

    # New billings: issued weekly, collected after DSO (+ scenario delay)
    lag = -(-(company.dso_days + sc.collections_delay_days) // 7)  # ceil
    weekly = company.base_weekly_billings * (1 + sc.billings_change_pct)
    for w in range(WEEKS):
        s["billings"][w] = weekly
        if w + lag < WEEKS:
            s["collections_new"][w + lag] += weekly

    pay = date.fromisoformat(company.first_payroll_date)
    while pay <= end:
        s["payroll"][_week_idx(pay, start)] += company.payroll_per_period
        pay += timedelta(days=14)

    for b in company.bills:  # on due date; past-due bills paid in week 1
        wk = _week_idx(date.fromisoformat(b.due_date), start)
        if wk < WEEKS:
            s["vendors"][wk] += b.amount
    for w in range(8, WEEKS):  # beyond the window where actual bills exist
        s["vendors"][w] += company.weekly_vendor_run_rate * (1 + sc.opex_change_pct)

    for r in company.recurring:
        line = r.category if r.category in DISB_KEYS else "other_disb"
        for due in _recurring_dates(r.frequency, r.day_of_month, start, end):
            s[line][_week_idx(due, start)] += r.amount * (1 + sc.opex_change_pct)

    for wk, amt, _label in sc.one_time_items:
        if 1 <= wk <= WEEKS:
            s["one_time"][wk - 1] += amt
    return s


def pay_flags(company: Company) -> list[int]:
    """1 if the week contains the 1st of a month (interest and fees paid in arrears)."""
    return [int(any((a + timedelta(days=k)).day == 1 for k in range(7))) for a, _ in week_dates(company)]


def build(company: Company, scenario: Scenario | None = None) -> dict:
    sc = scenario or Scenario()
    t = company.terms
    bbc = bb.compute(company)
    s = operating_schedule(company, sc)
    flags = pay_flags(company)
    rate = t.rate

    cash, loan = company.opening_cash, t.opening_loan
    ar = bbc.gross_ar
    int_accr = t.opening_loan * rate * t.days_accrued / 360
    fee_accr = max(0.0, t.commitment - t.opening_loan) * t.unused_fee * t.days_accrued / 360
    rows = []
    for w, (wk_start, wk_end) in enumerate(week_dates(company)):
        receipts = sum(s[k][w] for k, _ in RECEIPT_LINES)
        disb = sum(s[k][w] for k in DISB_KEYS)
        net_operating = receipts - disb

        ar_beg = ar
        collections = s["collections_ar"][w] + s["collections_new"][w]
        ar = ar_beg + s["billings"][w] - collections - s["write_offs"][w]
        eligible = ar * bbc.eligible_pct
        borrowing_base = eligible * t.advance_rate - t.availability_block
        max_borrow = max(0.0, min(t.commitment, borrowing_base))

        int_paid = int_accr if flags[w] else 0.0
        fee_paid = fee_accr if flags[w] else 0.0
        int_accrual = loan * rate * 7 / 360
        fee_accrual = max(0.0, t.commitment - loan) * t.unused_fee * 7 / 360

        beg_cash, beg_loan = cash, loan
        pre_cash = beg_cash + net_operating + s["one_time"][w] - int_paid - fee_paid
        avail_pre = max(0.0, max_borrow - beg_loan)
        overadvance = max(0.0, beg_loan - max_borrow)
        draw = min(company.min_cash_policy - pre_cash, avail_pre) if pre_cash < company.min_cash_policy else 0.0
        sweep = max(0.0, pre_cash - (company.min_cash_policy + company.sweep_buffer))
        repay = min(beg_loan, max(overadvance, sweep), max(0.0, pre_cash))
        loan = beg_loan + draw - repay
        cash = pre_cash + draw - repay
        int_accr = int_accr - int_paid + int_accrual
        fee_accr = fee_accr - fee_paid + fee_accrual

        availability = max_borrow - loan
        liquidity = cash + max(0.0, availability)
        row = {
            "week": w + 1, "week_start": wk_start.isoformat(), "week_end": wk_end.isoformat(),
            **{k: s[k][w] for k, _ in RECEIPT_LINES}, "total_receipts": receipts,
            **{k: s[k][w] for k in DISB_KEYS}, "total_disbursements": disb,
            "net_operating_cf": net_operating, "one_time": s["one_time"][w],
            "interest_paid": int_paid, "unused_fee_paid": fee_paid,
            "net_cf_before_revolver": pre_cash - beg_cash,
            "beginning_cash": beg_cash, "cash_before_revolver": pre_cash,
            "draws": draw, "repayments": repay, "ending_cash": cash,
            "ar_beginning": ar_beg, "billings": s["billings"][w], "collections": collections,
            "write_offs": s["write_offs"][w], "ar_ending": ar,
            "eligible_ar": eligible, "borrowing_base": borrowing_base, "max_borrowing": max_borrow,
            "loan_beginning": beg_loan, "availability_before_draw": avail_pre, "overadvance": overadvance,
            "loan_ending": loan, "pay_flag": flags[w],
            "interest_accrual": int_accrual, "unused_fee_accrual": fee_accrual,
            "interest_accrued_end": int_accr, "unused_fee_accrued_end": fee_accr,
            "availability": availability, "liquidity": liquidity,
        }
        row["covenants"] = covenant_tests(company, row)
        rows.append(row)
    return {"scenario": asdict(sc), "rows": rows, "summary": summarize(company, rows), "bbc": bbc}


def covenant_tests(company: Company, r: dict) -> dict:
    t = company.terms
    return {
        "min_liquidity": {"actual": r["liquidity"], "required": t.min_liquidity,
                          "cushion": r["liquidity"] - t.min_liquidity, "pass": r["liquidity"] >= t.min_liquidity},
        "min_excess_availability": {"actual": r["availability"], "required": t.min_excess_availability,
                                    "cushion": r["availability"] - t.min_excess_availability,
                                    "pass": r["availability"] >= t.min_excess_availability},
        "fccr_springing": {"actual": r["availability"], "trigger": t.fccr_trigger_pct * t.commitment,
                           "triggered": r["availability"] < t.fccr_trigger_pct * t.commitment},
        "no_overadvance": {"pass": r["loan_ending"] <= r["max_borrowing"] + 0.005},
        "cash_positive": {"pass": r["ending_cash"] >= 0},
    }


COVENANT_LABELS = {
    "min_liquidity": "Minimum Liquidity",
    "min_excess_availability": "Minimum Excess Availability",
    "no_overadvance": "No over-advance",
    "cash_positive": "Cash stays positive",
}


def summarize(company: Company, rows: list[dict]) -> dict:
    low = min(rows, key=lambda r: r["ending_cash"])
    low_liq = min(rows, key=lambda r: r["liquidity"])
    low_av = min(rows, key=lambda r: r["availability"])
    breaches = [(r["week"], COVENANT_LABELS[k]) for r in rows for k in COVENANT_LABELS
                if not r["covenants"][k]["pass"]]
    trig = [r["week"] for r in rows if r["covenants"]["fccr_springing"]["triggered"]]
    return {
        "opening_cash": company.opening_cash,
        "ending_cash": rows[-1]["ending_cash"],
        "lowest_cash": low["ending_cash"], "lowest_cash_week": low["week"],
        "total_receipts": sum(r["total_receipts"] for r in rows),
        "total_disbursements": sum(r["total_disbursements"] for r in rows),
        "opening_loan": company.terms.opening_loan,
        "ending_loan": rows[-1]["loan_ending"],
        "peak_loan": max(r["loan_ending"] for r in rows),
        "total_draws": sum(r["draws"] for r in rows),
        "total_repayments": sum(r["repayments"] for r in rows),
        "interest_and_fees": sum(r["interest_paid"] + r["unused_fee_paid"] for r in rows),
        "lowest_availability": low_av["availability"], "lowest_availability_week": low_av["week"],
        "lowest_liquidity": low_liq["liquidity"], "lowest_liquidity_week": low_liq["week"],
        "ending_liquidity": rows[-1]["liquidity"],
        "breaches": breaches,
        "first_breach_week": breaches[0][0] if breaches else None,
        "fccr_trigger_weeks": trig,
        "all_covenants_pass": not breaches,
    }


def compare(company: Company, scenarios: dict[str, Scenario] | None = None) -> list[dict]:
    out = []
    for name, sc in (scenarios or presets(company)).items():
        s = build(company, sc)["summary"]
        out.append({"scenario": name, "label": sc.label, **{k: s[k] for k in (
            "ending_cash", "lowest_cash", "lowest_cash_week", "peak_loan", "lowest_availability",
            "lowest_availability_week", "lowest_liquidity", "lowest_liquidity_week", "first_breach_week",
            "fccr_trigger_weeks", "all_covenants_pass")},
            "breach_detail": "; ".join(f"wk {w}: {c}" for w, c in s["breaches"][:3])})
    return out


def with_overrides(sc: Scenario, **kw) -> Scenario:
    return replace(sc, **kw)
