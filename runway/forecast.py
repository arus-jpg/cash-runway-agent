"""Driver-based 13-week cash forecast with scenario levers."""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta

from .data import Company, History

WEEKS = 13
WEEKS_PER_MONTH = 52 / 12

# Monte Carlo drivers: the uncertainty lives in collections, not in contractual
# outflows (payroll, bills, opex are known). We perturb collection *timing* and
# whether each invoice is realized, plus the size of future billings. Without a
# Calibration (see calibrate()) these hardcoded fallbacks are used; with one,
# the shape/spread/correlation come from history instead.
SIM_SIGMA_DAYS = 10.0       # std dev of collection timing, in days
SIM_SIGMA_BILLINGS = 0.12   # std dev of weekly billings, as a fraction
SIM_BILLINGS_COMMON = 0.6   # share of billings variance that is systematic (shared with collections)


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
    policy = company.min_cash_policy
    n = len(rows)
    total_net = sum(r["net_cash_flow"] for r in rows)
    weekly_net_burn = -total_net / n
    # Gross burn = all cash leaving the business, regardless of receipts.
    weekly_gross_burn = sum(r["payroll"] + r["ap"] + r["opex"] + r["other_out"] for r in rows) / n
    breach = next((r for r in rows if r["ending_cash"] < policy), None)
    zero = next((r for r in rows if r["ending_cash"] < 0), None)

    def runway_weeks(threshold: float):
        """Weeks from as-of until cash falls below `threshold` at the current net burn."""
        hit = next((r for r in rows if r["ending_cash"] < threshold), None)
        if hit:
            return hit["week"]
        if weekly_net_burn > 0:
            return round(n + (ending - threshold) / weekly_net_burn, 1)
        return None  # cash generative over the window

    to_zero = runway_weeks(0)
    to_policy = runway_weeks(policy)
    net_burn_monthly = weekly_net_burn * WEEKS_PER_MONTH
    arr = company.monthly_net_new_arr
    burn_multiple = round(net_burn_monthly / arr, 2) if arr and weekly_net_burn > 0 else None
    return {
        "opening_cash": round(company.opening_cash),
        "ending_cash_week13": ending,
        "lowest_cash": min_row["ending_cash"],
        "lowest_cash_week": min_row["week"],
        "avg_weekly_net_burn": round(weekly_net_burn),
        "avg_weekly_gross_burn": round(weekly_gross_burn),
        "net_burn_monthly": round(net_burn_monthly),
        "gross_burn_monthly": round(weekly_gross_burn * WEEKS_PER_MONTH),
        "runway_months_at_current_burn": round(ending / net_burn_monthly, 1) if weekly_net_burn > 0 else None,
        "runway_weeks_to_zero": to_zero,
        "runway_months_to_zero": round(to_zero * 12 / 52, 1) if to_zero is not None else None,
        "runway_weeks_to_policy": to_policy,
        "runway_months_to_policy": round(to_policy * 12 / 52, 1) if to_policy is not None else None,
        "monthly_net_new_arr": round(arr),
        "burn_multiple": burn_multiple,
        "min_cash_policy": round(policy),
        "first_week_below_policy": breach["week"] if breach else None,
        "first_week_cash_negative": zero["week"] if zero else None,
    }


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation percentile of an already-sorted list."""
    if not sorted_vals:
        return 0.0
    idx = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


@dataclass
class Calibration:
    """Monte Carlo inputs fitted from historical actuals (see calibrate())."""
    lateness_residuals: list[int]  # actual days-late minus each customer's own mean; the empirical spread/skew
    lateness_sd: float             # std dev of those residuals (scales the systematic timing shift)
    median_days_late: float
    p90_days_late: float
    writeoff_rate: float           # $-weighted, from history
    blended_collect_risk: float    # $-weighted collect_risk on the open book
    realization_factor: float      # history write-off rate / blended risk (anchors realization to actuals)
    billings_cv: float             # coefficient of variation of weekly billings
    corr_billings_lateness: float  # Pearson corr: soft billings weeks pay slower (negative)
    n_invoices: int
    n_weeks: int


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (sx * sy) if sx and sy else 0.0


def calibrate(company: Company, history: History) -> Calibration:
    """Fit the Monte Carlo's distributions from historical actuals.

    - Collection timing: empirical lateness, centered per customer so the open
      book keeps its own means but inherits history's real (right-skewed) spread.
    - Realization: historical $-weighted write-off rate, anchored to the open
      book's blended collect_risk.
    - Billings: coefficient of variation and its correlation with collection
      speed, so downturns hit receipts and timing together.
    """
    paid = [c for c in history.collections if c.paid_date]
    by_cust: dict[str, list[int]] = defaultdict(list)
    for c in paid:
        dl = (date.fromisoformat(c.paid_date) - date.fromisoformat(c.due_date)).days
        by_cust[c.customer].append(dl)
    means = {k: statistics.fmean(v) for k, v in by_cust.items()}
    residuals, all_late = [], []
    for c in paid:
        dl = (date.fromisoformat(c.paid_date) - date.fromisoformat(c.due_date)).days
        residuals.append(int(round(dl - means[c.customer])))
        all_late.append(dl)
    all_late.sort()

    total_amt = sum(c.amount for c in history.collections) or 1.0
    writeoff_rate = sum(c.amount for c in history.collections if c.written_off) / total_amt
    open_amt = sum(i.amount for i in company.invoices) or 1.0
    blended = sum(i.amount * i.collect_risk for i in company.invoices) / open_amt
    # History anchors realization but only within a guardrail: the per-customer
    # collect_risk still carries the shape; a book/history mix difference (e.g. a
    # riskier open book) can't swing effective risk more than ±25%.
    realization_factor = min(1.25, max(0.8, writeoff_rate / blended)) if blended > 0 else 1.0

    bvals = [b.billings for b in history.billings]
    lvals = [b.avg_days_late for b in history.billings]
    bmean = statistics.fmean(bvals) if bvals else 1.0
    billings_cv = (statistics.pstdev(bvals) / bmean) if bmean and len(bvals) > 1 else SIM_SIGMA_BILLINGS

    return Calibration(
        lateness_residuals=residuals or [0],
        lateness_sd=statistics.pstdev(residuals) if len(residuals) > 1 else SIM_SIGMA_DAYS,
        median_days_late=round(_percentile(all_late, 0.50), 1),
        p90_days_late=round(_percentile(all_late, 0.90), 1),
        writeoff_rate=round(writeoff_rate, 4),
        blended_collect_risk=round(blended, 4),
        realization_factor=round(realization_factor, 3),
        billings_cv=round(billings_cv, 4),
        corr_billings_lateness=round(_pearson(bvals, lvals), 3),
        n_invoices=len(history.collections),
        n_weeks=len(history.billings),
    )


def simulate(company: Company, scenario: Scenario | None = None,
             n: int = 2000, seed: int = 12345, calibration: "Calibration | None" = None) -> dict:
    """Monte Carlo on collection timing + realization and billings size.

    Contractual outflows (payroll, AP, opex) are taken from the deterministic
    build and held fixed; the spread comes from when/whether cash is collected.
    With a Calibration, timing is bootstrapped from the empirical (right-skewed)
    lateness residuals, realization is anchored to historical write-offs, and a
    per-run demand factor couples soft billings to slower collections via the
    historical correlation; without one, symmetric hardcoded fallbacks are used.
    Returns per-week P10/P50/P90 ending-cash bands and breach probabilities.
    Seeded, so the dashboard is reproducible across rebuilds.
    """
    sc = scenario or Scenario()
    start = date.fromisoformat(company.as_of)
    policy = company.min_cash_policy
    det = build(company, sc)
    outflow = [r["payroll"] + r["ap"] + r["opex"] + r["other_out"] for r in det["rows"]]
    lag_weeks = math.ceil((company.dso_days + sc.collections_delay_days) / 7)
    base_weekly = company.base_weekly_billings * (1 + sc.billings_change_pct)

    cal = calibration
    residuals = cal.lateness_residuals if cal else None
    lateness_sd = cal.lateness_sd if cal else SIM_SIGMA_DAYS
    billings_cv = cal.billings_cv if cal else SIM_SIGMA_BILLINGS
    rho = cal.corr_billings_lateness if cal else 0.0
    realization_factor = cal.realization_factor if cal else 1.0
    nres = len(residuals) if residuals else 0

    rng = random.Random(seed)
    paths: list[list[float]] = []
    for _ in range(n):
        z = rng.gauss(0, 1)  # the run's demand/liquidity condition
        billings_factor = max(0.3, 1 + SIM_BILLINGS_COMMON * billings_cv * z)
        timing_shift = (-rho) * lateness_sd * z  # soft demand (z<0) -> later collections
        receipts = [0.0] * WEEKS
        for inv in company.invoices:
            due = date.fromisoformat(inv.due_date)
            resid = residuals[rng.randrange(nres)] if residuals else rng.gauss(0, lateness_sd)
            days = inv.typical_days_late + sc.collections_delay_days + timing_shift + resid
            wk = _week_idx(due + timedelta(days=round(days)), start)
            if wk >= WEEKS:
                continue
            p_collect = 1 - min(1.0, inv.collect_risk * sc.bad_debt_multiplier * realization_factor)
            if rng.random() < p_collect:
                receipts[wk] += inv.amount
        for w in range(WEEKS):
            if w + lag_weeks < WEEKS:
                idio = rng.gauss(0, billings_cv * (1 - SIM_BILLINGS_COMMON))
                receipts[w + lag_weeks] += max(0.0, base_weekly * billings_factor * (1 + idio))
        cash = company.opening_cash
        path = []
        for w in range(WEEKS):
            cash += receipts[w] - outflow[w]
            path.append(cash)
        paths.append(path)

    p10, p50, p90 = [], [], []
    for w in range(WEEKS):
        col = sorted(p[w] for p in paths)
        p10.append(round(_percentile(col, 0.10)))
        p50.append(round(_percentile(col, 0.50)))
        p90.append(round(_percentile(col, 0.90)))
    lows = sorted(min(p) for p in paths)
    return {
        "n": n,
        "p10": p10, "p50": p50, "p90": p90,
        "prob_breach_policy": round(sum(1 for p in paths if min(p) < policy) / n, 3),
        "prob_cash_negative": round(sum(1 for p in paths if min(p) < 0) / n, 3),
        "worst_case_low_p10": round(_percentile(lows, 0.10)),  # pessimistic lowest cash
        "median_low": round(_percentile(lows, 0.50)),
        "best_case_low_p90": round(_percentile(lows, 0.90)),
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
