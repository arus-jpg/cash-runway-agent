"""Synthetic company data for the demo. No real numbers - safe to commit."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path

AS_OF = date(2026, 11, 16)  # a Monday; week 0 starts here


@dataclass
class Invoice:
    customer: str
    amount: float
    due_date: str  # ISO
    typical_days_late: int
    collect_risk: float  # probability the invoice is never collected (0-1)


@dataclass
class Bill:
    vendor: str
    amount: float
    due_date: str


@dataclass
class Recurring:
    name: str
    amount: float
    frequency: str  # monthly | quarterly
    day_of_month: int
    category: str = "opex"


@dataclass
class CollectionRecord:
    """A historical invoice and when it actually paid (or was written off)."""
    customer: str
    due_date: str          # ISO
    paid_date: str | None  # ISO, or None if written off
    amount: float
    written_off: bool


@dataclass
class BillingsWeek:
    """A historical week of new billings and the collection lateness realized that week."""
    week_start: str  # ISO
    billings: float
    avg_days_late: float


@dataclass
class History:
    collections: list[CollectionRecord] = field(default_factory=list)
    billings: list[BillingsWeek] = field(default_factory=list)


@dataclass
class Company:
    name: str
    as_of: str
    opening_cash: float
    min_cash_policy: float  # board-mandated minimum cash
    payroll_per_period: float  # biweekly, fully loaded
    first_payroll_date: str
    base_weekly_billings: float  # new invoices issued per week
    dso_days: int  # days from new invoice to cash
    weekly_vendor_run_rate: float = 0.0  # un-billed vendor spend per week beyond the known-bill window
    monthly_net_new_arr: float = 0.0  # net new ARR booked per month (for burn multiple); 0 = unknown
    invoices: list[Invoice] = field(default_factory=list)
    bills: list[Bill] = field(default_factory=list)
    recurring: list[Recurring] = field(default_factory=list)


CUSTOMERS = [
    ("Northwind Defense", 0.01), ("Helix Aerospace", 0.02), ("Brightline Energy", 0.03),
    ("Vertex Logistics", 0.05), ("Orion Health", 0.02), ("Atlas Manufacturing", 0.04),
    ("Pioneer Utilities", 0.01), ("Summit Robotics", 0.08), ("Cobalt Mining", 0.12),
    ("Redwood Retail", 0.15), ("Nimbus Telecom", 0.03), ("Granite Construction", 0.10),
]
VENDORS = ["AWS", "Azure", "Salesforce", "Workday", "Deloitte", "Wilson Sonsini", "WeWork",
           "Gusto", "Datadog", "Snowflake", "Okta", "CrowdStrike", "Marsh McLennan", "Zoom"]


def generate(seed: int = 7) -> Company:
    rng = random.Random(seed)
    inv = []
    for i in range(26):
        cust, risk = rng.choice(CUSTOMERS)
        inv.append(Invoice(
            customer=cust,
            amount=round(rng.uniform(30_000, 300_000), -2),
            due_date=(AS_OF + timedelta(days=rng.randint(-35, 60))).isoformat(),
            typical_days_late=max(0, int(rng.gauss(12, 14))),
            collect_risk=risk,
        ))
    bills = [Bill(rng.choice(VENDORS), round(rng.uniform(8_000, 260_000), -2),
                  (AS_OF + timedelta(days=rng.randint(-5, 55))).isoformat()) for _ in range(22)]
    recurring = [
        Recurring("Office rent", 62_000, "monthly", 1, "facilities"),
        Recurring("Cloud & SaaS", 118_000, "monthly", 15, "tech"),
        Recurring("Benefits & 401k", 96_000, "monthly", 5, "people"),
        Recurring("Insurance", 54_000, "quarterly", 20, "g&a"),
        Recurring("Sales tax / other taxes", 38_000, "quarterly", 28, "tax"),
    ]
    return Company(
        name="Acme Robotics (synthetic)",
        as_of=AS_OF.isoformat(),
        opening_cash=7_300_000,
        min_cash_policy=3_000_000,
        payroll_per_period=700_000,
        first_payroll_date=(AS_OF + timedelta(days=4)).isoformat(),
        base_weekly_billings=300_000,
        dso_days=48,
        weekly_vendor_run_rate=120_000,
        monthly_net_new_arr=800_000,
        invoices=inv, bills=bills, recurring=recurring,
    )


def generate_history(seed: int = 11, weeks: int = 78, invoices: int = 220) -> History:
    """Dummy ~18 months of actuals used to CALIBRATE the Monte Carlo.

    Built so there is real structure to recover: collection lateness is
    right-skewed (customers drag far more than they prepay), write-offs track
    customer risk, and a shared weekly demand factor makes billings and
    collection speed move together (soft weeks bill less AND pay slower).
    """
    rng = random.Random(seed)
    bweeks: list[BillingsWeek] = []
    start = AS_OF - timedelta(days=7 * weeks)
    for w in range(weeks):
        z = rng.gauss(0, 1)  # that week's demand/liquidity condition
        billings = max(0.0, 300_000 * (1 + 0.14 * z) + rng.gauss(0, 12_000))
        avg_late = max(0.0, 11 - 4.5 * z + rng.gauss(0, 2))  # slower when z is low -> neg corr
        bweeks.append(BillingsWeek((start + timedelta(days=7 * w)).isoformat(),
                                   round(billings, -2), round(avg_late, 1)))
    cols: list[CollectionRecord] = []
    for _ in range(invoices):
        cust, risk = rng.choice(CUSTOMERS)
        due = AS_OF - timedelta(days=rng.randint(30, 7 * weeks))
        days_late = int(round(max(0.0, rng.gauss(9, 5)) + rng.expovariate(1 / 8)))  # right tail
        written = rng.random() < risk
        paid = None if written else (due + timedelta(days=days_late)).isoformat()
        cols.append(CollectionRecord(cust, due.isoformat(), paid,
                                     round(rng.uniform(20_000, 260_000), -2), written))
    return History(cols, bweeks)


def save(company: Company, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(company), indent=2))


def load(path: Path) -> Company:
    d = json.loads(path.read_text())
    d["invoices"] = [Invoice(**x) for x in d["invoices"]]
    d["bills"] = [Bill(**x) for x in d["bills"]]
    d["recurring"] = [Recurring(**x) for x in d["recurring"]]
    return Company(**d)


# ---------------------------------------------------------------------------
# QuickBooks-style CSV import/export
#   ar_aging_detail.csv   <- QB report "A/R Aging Detail"      (open invoices)
#   ap_aging_detail.csv   <- QB report "A/P Aging Detail"      (unpaid bills)
#   bank_balances.csv     <- QB "Balance Sheet" bank accounts
#   payroll_register.csv  <- payroll provider / QB payroll history
#   recurring_expenses.csv, customer_terms.csv, assumptions.csv <- finance team inputs
# Dates are MM/DD/YYYY, as QuickBooks exports them.
# ---------------------------------------------------------------------------
import csv
from datetime import datetime

QB_FMT = "%m/%d/%Y"


def _qb(iso: str) -> str:
    return date.fromisoformat(iso).strftime(QB_FMT)


def _iso(qb: str) -> str:
    return datetime.strptime(qb.strip(), QB_FMT).date().isoformat()


def _money(s: str) -> float:
    return float(s.replace("$", "").replace(",", "").strip() or 0)


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def export_csvs(c: Company, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    as_of = date.fromisoformat(c.as_of)
    _write(outdir / "ar_aging_detail.csv",
           ["Customer", "Num", "Invoice Date", "Due Date", "Open Balance"],
           [[i.customer, f"INV-{1000 + n}", _qb((date.fromisoformat(i.due_date) - timedelta(days=30)).isoformat()),
             _qb(i.due_date), f"{i.amount:.2f}"] for n, i in enumerate(c.invoices)])
    _write(outdir / "ap_aging_detail.csv",
           ["Vendor", "Num", "Bill Date", "Due Date", "Open Balance"],
           [[b.vendor, f"BILL-{5000 + n}", _qb((date.fromisoformat(b.due_date) - timedelta(days=30)).isoformat()),
             _qb(b.due_date), f"{b.amount:.2f}"] for n, b in enumerate(c.bills)])
    operating = round(c.opening_cash * 0.8, 2)
    _write(outdir / "bank_balances.csv", ["Account", "Balance"],
           [["Operating Checking (...1234)", f"{operating:.2f}"],
            ["Money Market (...5678)", f"{c.opening_cash - operating:.2f}"]])
    last = date.fromisoformat(c.first_payroll_date) - timedelta(days=14)
    _write(outdir / "payroll_register.csv", ["Pay Date", "Total Payroll Cost"],
           [[_qb((last - timedelta(days=14 * k)).isoformat()), f"{c.payroll_per_period:.2f}"] for k in range(5, -1, -1)])
    _write(outdir / "recurring_expenses.csv", ["Name", "Amount", "Frequency", "Day of Month", "Category"],
           [[r.name, f"{r.amount:.2f}", r.frequency, r.day_of_month, r.category] for r in c.recurring])
    by_cust: dict[str, list[Invoice]] = {}
    for i in c.invoices:
        by_cust.setdefault(i.customer, []).append(i)
    _write(outdir / "customer_terms.csv", ["Customer", "Typical Days Late", "Collect Risk"],
           [[k, round(sum(i.typical_days_late for i in v) / len(v)), v[0].collect_risk] for k, v in sorted(by_cust.items())])
    _write(outdir / "assumptions.csv", ["Key", "Value"],
           [["company_name", c.name], ["as_of", _qb(c.as_of)], ["min_cash_policy", c.min_cash_policy],
            ["base_weekly_billings", c.base_weekly_billings], ["dso_days", c.dso_days],
            ["weekly_vendor_run_rate", c.weekly_vendor_run_rate],
            ["monthly_net_new_arr", c.monthly_net_new_arr]])
    hist = generate_history()
    _write(outdir / "collections_history.csv", ["Customer", "Due Date", "Paid Date", "Amount", "Status"],
           [[r.customer, _qb(r.due_date), (_qb(r.paid_date) if r.paid_date else ""),
             f"{r.amount:.2f}", "Written Off" if r.written_off else "Paid"] for r in hist.collections])
    _write(outdir / "billings_history.csv", ["Week Start", "Billings", "Avg Days Late"],
           [[_qb(b.week_start), f"{b.billings:.2f}", b.avg_days_late] for b in hist.billings])


def load_csvs(d: Path) -> Company:
    def rows(name):
        with open(d / name, newline="") as fh:
            return list(csv.DictReader(fh))

    a = {r["Key"]: r["Value"] for r in rows("assumptions.csv")}
    as_of = _iso(a["as_of"])
    terms = {r["Customer"]: (int(float(r["Typical Days Late"])), float(r["Collect Risk"]))
             for r in rows("customer_terms.csv")}
    default_terms = (10, 0.05)  # unknown customers: 10 days late, 5% risk
    invoices = [Invoice(r["Customer"], _money(r["Open Balance"]), _iso(r["Due Date"]), *terms.get(r["Customer"], default_terms))
                for r in rows("ar_aging_detail.csv")]
    bills = [Bill(r["Vendor"], _money(r["Open Balance"]), _iso(r["Due Date"])) for r in rows("ap_aging_detail.csv")]
    cash = sum(_money(r["Balance"]) for r in rows("bank_balances.csv"))
    pay = sorted((date.fromisoformat(_iso(r["Pay Date"])), _money(r["Total Payroll Cost"])) for r in rows("payroll_register.csv"))
    recent = pay[-4:]
    next_pay = pay[-1][0] + timedelta(days=14)
    while next_pay < date.fromisoformat(as_of):
        next_pay += timedelta(days=14)
    recurring = [Recurring(r["Name"], _money(r["Amount"]), r["Frequency"], int(r["Day of Month"]), r["Category"])
                 for r in rows("recurring_expenses.csv")]
    return Company(
        name=a.get("company_name", "Company"), as_of=as_of, opening_cash=cash,
        min_cash_policy=float(a["min_cash_policy"]),
        payroll_per_period=sum(x[1] for x in recent) / len(recent),
        first_payroll_date=next_pay.isoformat(),
        base_weekly_billings=float(a["base_weekly_billings"]), dso_days=int(float(a["dso_days"])),
        weekly_vendor_run_rate=float(a.get("weekly_vendor_run_rate", 0)),
        monthly_net_new_arr=float(a.get("monthly_net_new_arr", 0)),
        invoices=invoices, bills=bills, recurring=recurring)


def load_history(d: Path) -> History | None:
    """Load historical actuals for calibration. Returns None if not present."""
    cfile, bfile = d / "collections_history.csv", d / "billings_history.csv"
    if not (cfile.exists() and bfile.exists()):
        return None

    def rows(path):
        with open(path, newline="") as fh:
            return list(csv.DictReader(fh))

    cols = [CollectionRecord(
        r["Customer"], _iso(r["Due Date"]),
        _iso(r["Paid Date"]) if r.get("Paid Date", "").strip() else None,
        _money(r["Amount"]), r["Status"].strip().lower().startswith("written"))
        for r in rows(cfile)]
    bills = [BillingsWeek(_iso(r["Week Start"]), _money(r["Billings"]), float(r["Avg Days Late"]))
             for r in rows(bfile)]
    return History(cols, bills)
