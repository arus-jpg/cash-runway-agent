"""Borrowing base certificate: invoice-level eligibility -> eligible A/R -> availability.

Ineligibility is tested in a fixed order and each invoice takes only its first failing
reason, so every dollar is excluded once and the waterfall ties to the aging total.
Concentration is applied last, at the customer level, to what is still eligible.
The Excel export rebuilds the same logic in live formulas; tests assert they agree.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .data import Company, FacilityTerms

FUTURE, FOREIGN, AFFILIATE, GOV_NO_AOC, PAST_DUE, CROSS_AGED, CONCENTRATION = (
    "Future-dated", "Foreign", "Affiliate", "Gov't - no AoC", "Past ineligible days", "Cross-aged", "Concentration")

# Order matters: it is the order of the BBC waterfall and of the invoice-level tests.
INELIGIBLE_CATEGORIES = [FUTURE, FOREIGN, AFFILIATE, GOV_NO_AOC, PAST_DUE, CROSS_AGED, CONCENTRATION]

CATEGORY_HELP = {
    FUTURE: "Invoice dated after the as-of date (pre-billing)",
    FOREIGN: "Account debtor outside the U.S.",
    AFFILIATE: "Account debtor is an affiliate of the borrower",
    GOV_NO_AOC: "U.S. Government account without an Assignment of Claims Act filing",
    PAST_DUE: "More than the ineligible-days limit past invoice date",
    CROSS_AGED: "Customer has too large a share of balances past the ineligible-days limit",
    CONCENTRATION: "Customer's eligible A/R above its concentration limit",
}


def aging_bucket(age: int) -> str:
    if age < 0:
        return "Future"
    if age <= 30:
        return "0-30"
    if age <= 60:
        return "31-60"
    if age <= 90:
        return "61-90"
    return "91+"


AGING_BUCKETS = ["Future", "0-30", "31-60", "61-90", "91+"]


@dataclass
class BBC:
    invoices: list[dict]            # one row per invoice with age, bucket, reason, ineligible amounts
    concentration: list[dict]       # one row per customer
    ineligibles: dict[str, float]   # category -> amount, in waterfall order
    gross_ar: float
    eligible_ar: float
    terms: FacilityTerms
    loan: float

    @property
    def total_ineligible(self) -> float:
        return sum(self.ineligibles.values())

    @property
    def eligible_pct(self) -> float:
        return self.eligible_ar / self.gross_ar if self.gross_ar else 0.0

    @property
    def gross_availability(self) -> float:
        return self.eligible_ar * self.terms.advance_rate

    @property
    def borrowing_base(self) -> float:
        return self.gross_availability - self.terms.availability_block

    @property
    def max_borrowing(self) -> float:
        return max(0.0, min(self.terms.commitment, self.borrowing_base))

    @property
    def availability(self) -> float:
        """Negative means over-advanced."""
        return self.max_borrowing - self.loan

    def aging_summary(self) -> dict[str, float]:
        out = {b: 0.0 for b in AGING_BUCKETS}
        for r in self.invoices:
            out[r["bucket"]] += r["amount"]
        return out


def _limit(t: FacilityTerms, customer_type: str) -> float:
    return t.gov_concentration_limit if customer_type == "US Gov - Assigned" else t.concentration_limit


def compute(company: Company, loan: float | None = None) -> BBC:
    t = company.terms
    as_of = date.fromisoformat(company.as_of)
    rows = []
    for inv in company.invoices:
        age = (as_of - date.fromisoformat(inv.invoice_date)).days
        rows.append({"customer": inv.customer, "customer_type": inv.customer_type, "num": inv.num,
                     "invoice_date": inv.invoice_date, "due_date": inv.due_date, "amount": inv.amount,
                     "age": age, "bucket": aging_bucket(age),
                     "past_limit_amount": inv.amount if age > t.ineligible_days else 0.0})

    cust_total: dict[str, float] = {}
    cust_past: dict[str, float] = {}
    for r in rows:
        cust_total[r["customer"]] = cust_total.get(r["customer"], 0.0) + r["amount"]
        cust_past[r["customer"]] = cust_past.get(r["customer"], 0.0) + r["past_limit_amount"]

    for r in rows:
        c = r["customer"]
        share_past = cust_past[c] / cust_total[c] if cust_total[c] else 0.0
        if r["age"] < 0:
            reason = FUTURE
        elif r["customer_type"] == "Foreign":
            reason = FOREIGN
        elif r["customer_type"] == "Affiliate":
            reason = AFFILIATE
        elif r["customer_type"] == "US Gov - Unassigned":
            reason = GOV_NO_AOC
        elif r["age"] > t.ineligible_days:
            reason = PAST_DUE
        elif share_past >= t.cross_age_pct:
            reason = CROSS_AGED
        else:
            reason = ""
        r["reason"] = reason
        r["ineligible"] = r["amount"] if reason else 0.0
        r["eligible_pre_conc"] = r["amount"] - r["ineligible"]

    pool = sum(r["eligible_pre_conc"] for r in rows)
    conc = []
    for c in sorted(cust_total):
        ctype = next(r["customer_type"] for r in rows if r["customer"] == c)
        pre = sum(r["eligible_pre_conc"] for r in rows if r["customer"] == c)
        lim = _limit(t, ctype)
        excess = max(0.0, pre - lim * pool)
        conc.append({"customer": c, "customer_type": ctype, "gross": cust_total[c], "eligible_pre_conc": pre,
                     "share": pre / pool if pool else 0.0, "limit_pct": lim, "limit_amount": lim * pool,
                     "excess": excess})

    inel = {k: 0.0 for k in INELIGIBLE_CATEGORIES}
    for r in rows:
        if r["reason"]:
            inel[r["reason"]] += r["ineligible"]
    inel[CONCENTRATION] = sum(c["excess"] for c in conc)
    gross = sum(r["amount"] for r in rows)
    return BBC(invoices=rows, concentration=conc, ineligibles=inel, gross_ar=gross,
               eligible_ar=gross - sum(inel.values()), terms=t,
               loan=t.opening_loan if loan is None else loan)
