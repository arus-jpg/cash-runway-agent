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
        invoices=inv, bills=bills, recurring=recurring,
    )


def save(company: Company, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(company), indent=2))


def load(path: Path) -> Company:
    d = json.loads(path.read_text())
    d["invoices"] = [Invoice(**x) for x in d["invoices"]]
    d["bills"] = [Bill(**x) for x in d["bills"]]
    d["recurring"] = [Recurring(**x) for x in d["recurring"]]
    return Company(**d)
