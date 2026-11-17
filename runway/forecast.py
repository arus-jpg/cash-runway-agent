"""Driver-based 13-week cash forecast with scenario levers."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta

from .data import Company

WEEKS = 13


@dataclass
class Scenario:
    name: str = "base"
    collections_delay_days: int = 0      # extra days customers take to pay
    billings_change_pct: float = 0.0     # change in new weekly billings, e.g. -0.2
    opex_change_pct: float = 0.0         # change in recurring opex, e.g. -0.1
    new_hires: int = 0                   # incremental headcount
    hire_start_week: int = 4
    hire_monthly_cost: float = 18_000    # fully loaded per hire per month
    one_time_items: list[tuple[int, float, str]] = field(default_factory=list)  # (week, amount, label); negative = outflow
    bad_debt_multiplier: float = 1.0     # scales each invoice's non-collection risk


PRESETS: dict[str, Scenario] = {
    "base": Scenario("base"),
    "slow_collections": Scenario("slow_collections", collections_delay_days=15),
    "revenue_down_20": Scenario("revenue_down_20", billings_change_pct=-0.20),
    "hiring_plan": Scenario("hiring_plan", new_hires=12, hire_start_week=3),
    "cost_cut_10": Scenario("cost_cut_10", opex_change_pct=-0.10),
    "downside": Scenario("downside", collections_delay_days=15, billings_change_pct=-0.20,
                         bad_debt_multiplier=2.0),
}


def _week_idx(d: date, start: date) -> int:
    """Week bucket for a date; anything before week 0 lands in week 0."""
    return max(0, (d - start).days // 7)


def _recurring_dates(freq: str, dom: int, start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        if freq == "monthly" or (freq == "quarterly" and d.month % 3 == 0):
            due = d.replace(day=min(dom, 28))
            if start <= due <= end:
                yield due
        d = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)


def build(company: Company, scenario: Scenario | None = None) -> dict:
    sc = scenario or Scenario()
    start = date.fromisoformat(company.as_of)
    end = start + timedelta(days=WEEKS * 7 - 1)
    receipts = [0.0] * WEEKS
    payroll = [0.0] * WEEKS
    ap = [0.0] * WEEKS
    opex = [0.0] * WEEKS
    other = [0.0] * WEEKS

    # Collections on open AR
    for inv in company.invoices:
        due = date.fromisoformat(inv.due_date)
        expected = due + timedelta(days=inv.typical_days_late + sc.collections_delay_days)
        wk = _week_idx(expected, start)
        p_collect = 1 - min(1.0, inv.collect_risk * sc.bad_debt_multiplier)
        if wk < WEEKS:
            receipts[wk] += inv.amount * p_collect

    # Collections on future billings (driver-based)
    lag_weeks = math.ceil((company.dso_days + sc.collections_delay_days) / 7)
    weekly = company.base_weekly_billings * (1 + sc.billings_change_pct)
    for w in range(WEEKS):
        if w + lag_weeks < WEEKS:
            receipts[w + lag_weeks] += weekly

    # Payroll (biweekly) + incremental hires
    pay = date.fromisoformat(company.first_payroll_date)
    while pay <= end:
        wk = _week_idx(pay, start)
        extra = sc.new_hires * sc.hire_monthly_cost * 12 / 26 if wk >= sc.hire_start_week else 0
        payroll[wk] += company.payroll_per_period + extra
        pay += timedelta(days=14)

    # AP bills on due date (past-due paid in week 0)
    for b in company.bills:
        wk = _week_idx(date.fromisoformat(b.due_date), start)
        if wk < WEEKS:
            ap[wk] += b.amount

    # Vendor run-rate beyond the window where we have actual bills (week 8+)
    for w in range(8, WEEKS):
        ap[w] += company.weekly_vendor_run_rate

    # Recurring opex
    for r in company.recurring:
        for due in _recurring_dates(r.frequency, r.day_of_month, start, end):
            opex[_week_idx(due, start)] += r.amount * (1 + sc.opex_change_pct)

    # One-time items
    for wk, amt, _label in sc.one_time_items:
        if 0 <= wk < WEEKS:
            other[wk] += amt

    rows, cash = [], company.opening_cash
    for w in range(WEEKS):
        inflow = receipts[w] + max(other[w], 0)
        outflow = payroll[w] + ap[w] + opex[w] + max(-other[w], 0)
        begin, cash = cash, cash + inflow - outflow
        rows.append({
            "week": w, "week_start": (start + timedelta(days=7 * w)).isoformat(),
            "beginning_cash": round(begin), "receipts": round(inflow), "payroll": round(payroll[w]),
            "ap": round(ap[w]), "opex": round(opex[w]), "other_out": round(max(-other[w], 0)),
            "net_cash_flow": round(inflow - outflow), "ending_cash": round(cash),
        })
    return {"scenario": asdict(sc), "rows": rows, "summary": summarize(company, rows)}


def summarize(company: Company, rows: list[dict]) -> dict:
    ending = rows[-1]["ending_cash"]
    min_row = min(rows, key=lambda r: r["ending_cash"])
    total_net = sum(r["net_cash_flow"] for r in rows)
    weekly_burn = -total_net / len(rows)
    breach = next((r for r in rows if r["ending_cash"] < company.min_cash_policy), None)
    zero = next((r for r in rows if r["ending_cash"] < 0), None)
    if weekly_burn > 0:
        runway_months = round(ending / (weekly_burn * 52 / 12), 1)
    else:
        runway_months = None  # cash generative over the window
    return {
        "opening_cash": round(company.opening_cash),
        "ending_cash_week13": ending,
        "lowest_cash": min_row["ending_cash"],
        "lowest_cash_week": min_row["week"],
        "avg_weekly_net_burn": round(weekly_burn),
        "runway_months_at_current_burn": runway_months,
        "min_cash_policy": round(company.min_cash_policy),
        "first_week_below_policy": breach["week"] if breach else None,
        "first_week_cash_negative": zero["week"] if zero else None,
    }


def compare(company: Company, names: list[str]) -> list[dict]:
    out = []
    for n in names:
        res = build(company, PRESETS[n])
        s = res["summary"]
        out.append({"scenario": n, **{k: s[k] for k in (
            "ending_cash_week13", "lowest_cash", "lowest_cash_week", "avg_weekly_net_burn",
            "runway_months_at_current_burn", "first_week_below_policy")}})
    return out


def ar_risk(company: Company) -> list[dict]:
    """Open AR ranked by expected-loss exposure."""
    start = date.fromisoformat(company.as_of)
    rows = []
    for i in company.invoices:
        due = date.fromisoformat(i.due_date)
        rows.append({
            "customer": i.customer, "amount": i.amount,
            "days_past_due": max(0, (start - due).days),
            "typical_days_late": i.typical_days_late,
            "collect_risk": i.collect_risk,
            "expected_loss": round(i.amount * i.collect_risk),
        })
    return sorted(rows, key=lambda r: r["expected_loss"], reverse=True)
