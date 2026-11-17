"""Inputs for the lender 13-week package, read from QuickBooks-style CSVs.

  ar_aging_detail.csv    <- QB "A/R Aging Detail"       (open invoices)
  ap_aging_detail.csv    <- QB "A/P Aging Detail"       (unpaid bills)
  bank_balances.csv      <- QB "Balance Sheet", bank accounts
  payroll_register.csv   <- payroll provider history (biweekly)
  customer_terms.csv     <- finance team: customer type (drives eligibility), pay behavior
  recurring_expenses.csv <- finance team: rent, benefits, SaaS, insurance, taxes
  assumptions.csv        <- borrower operating assumptions
  facility_terms.csv     <- the credit agreement terms (borrowing base, pricing, covenants)
  variance_history.csv   <- prior weeks' forecast vs actual, by cash-flow line

Dates are MM/DD/YYYY, as QuickBooks exports them. Everything in data/sample is synthetic.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from pathlib import Path

QB_FMT = "%m/%d/%Y"

CUSTOMER_TYPES = ("Commercial", "US Gov - Assigned", "US Gov - Unassigned", "Foreign", "Affiliate")


class InputError(ValueError):
    """Raised when an input file is missing, malformed, or fails a validation check."""


@dataclass
class Invoice:
    customer: str
    num: str
    invoice_date: str  # ISO
    due_date: str      # ISO
    amount: float
    customer_type: str
    typical_days_late: int
    collect_risk: float  # probability the invoice is never collected (0-1)


@dataclass
class Bill:
    vendor: str
    num: str
    bill_date: str
    due_date: str
    amount: float


@dataclass
class Recurring:
    name: str
    amount: float
    frequency: str  # monthly | quarterly
    day_of_month: int
    category: str   # benefits | facilities | technology | insurance | taxes | other_disb


@dataclass
class BankAccount:
    name: str
    balance: float


@dataclass
class VarianceRow:
    week_ending: str  # ISO
    line: str
    forecast: float
    actual: float


@dataclass
class FacilityTerms:
    lender_name: str = "Illustrative ABL Lender"
    facility_status: str = "ILLUSTRATIVE"
    commitment: float = 2_000_000
    advance_rate: float = 0.85
    ineligible_days: int = 90
    cross_age_pct: float = 0.50
    concentration_limit: float = 0.25
    gov_concentration_limit: float = 0.50
    availability_block: float = 250_000
    sofr: float = 0.043
    margin: float = 0.03
    unused_fee: float = 0.00375
    opening_loan: float = 750_000
    days_accrued: int = 15
    min_liquidity: float = 3_000_000
    min_excess_availability: float = 150_000
    fccr_trigger_pct: float = 0.125
    variance_receipts_min: float = 0.85
    variance_disb_max: float = 1.15

    @property
    def rate(self) -> float:
        return self.sofr + self.margin

    @property
    def is_draft(self) -> bool:
        return self.facility_status.strip().upper() != "EXECUTED"


TERM_DESCRIPTIONS: dict[str, str] = {}  # filled from facility_terms.csv on load


@dataclass
class Company:
    name: str
    as_of: str
    bank_accounts: list[BankAccount]
    min_cash_policy: float
    sweep_buffer: float
    payroll_per_period: float
    first_payroll_date: str
    base_weekly_billings: float
    dso_days: int
    weekly_vendor_run_rate: float
    terms: FacilityTerms
    invoices: list[Invoice] = field(default_factory=list)
    bills: list[Bill] = field(default_factory=list)
    recurring: list[Recurring] = field(default_factory=list)
    variance: list[VarianceRow] = field(default_factory=list)

    @property
    def opening_cash(self) -> float:
        return sum(a.balance for a in self.bank_accounts)


def _iso(qb: str) -> str:
    try:
        return datetime.strptime(qb.strip(), QB_FMT).date().isoformat()
    except ValueError as e:
        raise InputError(f"bad date {qb!r}: expected MM/DD/YYYY") from e


def _money(s: str) -> float:
    s = (s or "").replace("$", "").replace(",", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return float(s or 0)


def _rows(d: Path, name: str, required: list[str]) -> list[dict]:
    path = d / name
    if not path.exists():
        raise InputError(f"missing input file {path}")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if rows:
        missing = [c for c in required if c not in rows[0]]
        if missing:
            raise InputError(f"{name}: missing column(s) {missing}")
    return rows


def _coerce(cls_field, raw: str):
    t = cls_field.type if isinstance(cls_field.type, str) else cls_field.type.__name__
    if t == "float":
        return float(raw)
    if t == "int":
        return int(float(raw))
    return raw


def load_terms(d: Path) -> FacilityTerms:
    rows = _rows(d, "facility_terms.csv", ["Key", "Value"])
    by_key = {r["Key"].strip(): r for r in rows}
    kw = {}
    for f in fields(FacilityTerms):
        if f.name in by_key:
            kw[f.name] = _coerce(f, by_key[f.name]["Value"])
            TERM_DESCRIPTIONS[f.name] = by_key[f.name].get("Description", "")
    unknown = set(by_key) - {f.name for f in fields(FacilityTerms)}
    if unknown:
        raise InputError(f"facility_terms.csv: unknown key(s) {sorted(unknown)}")
    return FacilityTerms(**kw)


def save_terms(terms: FacilityTerms, d: Path) -> None:
    with open(d / "facility_terms.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Key", "Value", "Description"])
        for f in fields(FacilityTerms):
            w.writerow([f.name, getattr(terms, f.name), TERM_DESCRIPTIONS.get(f.name, "")])


def load_csvs(d: Path) -> Company:
    d = Path(d)
    a = {r["Key"]: r["Value"] for r in _rows(d, "assumptions.csv", ["Key", "Value"])}
    for k in ("as_of", "min_cash_policy", "base_weekly_billings", "dso_days"):
        if k not in a:
            raise InputError(f"assumptions.csv: missing key {k}")
    as_of = _iso(a["as_of"])

    terms_rows = _rows(d, "customer_terms.csv", ["Customer", "Customer Type", "Typical Days Late", "Collect Risk"])
    cust = {}
    for r in terms_rows:
        if r["Customer Type"] not in CUSTOMER_TYPES:
            raise InputError(f"customer_terms.csv: {r['Customer']} has unknown type {r['Customer Type']!r}; "
                             f"use one of {CUSTOMER_TYPES}")
        cust[r["Customer"]] = (r["Customer Type"], int(float(r["Typical Days Late"])), float(r["Collect Risk"]))

    invoices, seen = [], set()
    for r in _rows(d, "ar_aging_detail.csv", ["Customer", "Num", "Invoice Date", "Due Date", "Open Balance"]):
        if r["Num"] in seen:
            raise InputError(f"ar_aging_detail.csv: duplicate invoice number {r['Num']}")
        seen.add(r["Num"])
        if r["Customer"] not in cust:
            raise InputError(f"ar_aging_detail.csv: customer {r['Customer']!r} is not in customer_terms.csv "
                             "(its type decides eligibility, so it cannot be defaulted)")
        ctype, late, risk = cust[r["Customer"]]
        invoices.append(Invoice(r["Customer"], r["Num"], _iso(r["Invoice Date"]), _iso(r["Due Date"]),
                                _money(r["Open Balance"]), ctype, late, risk))

    bills = [Bill(r["Vendor"], r["Num"], _iso(r["Bill Date"]), _iso(r["Due Date"]), _money(r["Open Balance"]))
             for r in _rows(d, "ap_aging_detail.csv", ["Vendor", "Num", "Bill Date", "Due Date", "Open Balance"])]
    banks = [BankAccount(r["Account"], _money(r["Balance"]))
             for r in _rows(d, "bank_balances.csv", ["Account", "Balance"])]

    pay = sorted((date.fromisoformat(_iso(r["Pay Date"])), _money(r["Total Payroll Cost"]))
                 for r in _rows(d, "payroll_register.csv", ["Pay Date", "Total Payroll Cost"]))
    if not pay:
        raise InputError("payroll_register.csv is empty")
    recent = pay[-4:]
    next_pay = pay[-1][0] + timedelta(days=14)
    while next_pay < date.fromisoformat(as_of):
        next_pay += timedelta(days=14)

    recurring = [Recurring(r["Name"], _money(r["Amount"]), r["Frequency"], int(r["Day of Month"]), r["Category"])
                 for r in _rows(d, "recurring_expenses.csv", ["Name", "Amount", "Frequency", "Day of Month", "Category"])]

    variance = []
    vpath = d / "variance_history.csv"
    if vpath.exists():
        variance = [VarianceRow(_iso(r["Week Ending"]), r["Line"], _money(r["Forecast"]), _money(r["Actual"]))
                    for r in _rows(d, "variance_history.csv", ["Week Ending", "Line", "Forecast", "Actual"])]

    return Company(
        name=a.get("company_name", "Company"), as_of=as_of, bank_accounts=banks,
        min_cash_policy=float(a["min_cash_policy"]), sweep_buffer=float(a.get("sweep_buffer", 0)),
        payroll_per_period=sum(x[1] for x in recent) / len(recent),
        first_payroll_date=next_pay.isoformat(),
        base_weekly_billings=float(a["base_weekly_billings"]), dso_days=int(float(a["dso_days"])),
        weekly_vendor_run_rate=float(a.get("weekly_vendor_run_rate", 0)),
        terms=load_terms(d), invoices=invoices, bills=bills, recurring=recurring, variance=variance)
