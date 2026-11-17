# Lender 13-Week Cash Flow & Borrowing Base

Weekly reporting package for an **asset-based revolving line of credit**. It covers a 13-week cash flow in
lender format, the revolver roll-forward, a borrowing base certificate, availability and liquidity,
weekly covenant tests, and a budget-to-actual variance report. Review it in a local app, then export
an Excel package with live formulas.

Branched from [`cash-runway-agent`](https://github.com/bparada-hub/cash-runway-agent) (remote `upstream`),
which was an internal CFO runway tool. This branch (`lender-revolver`) refocuses it on what a lender reviews.
Burn multiple, ARR, Monte Carlo, the hiring scenarios, the Claude Q&A agent, and the GitHub Pages
dashboard were removed.

> All data in `data/sample/` is **synthetic**, and the facility terms are an **illustrative scenario**,
> not an executed credit agreement. The app and the workbook carry a DRAFT banner until
> `facility_status` is set to `EXECUTED` in `facility_terms.csv`.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/streamlit run app.py                     # review app -> Checks & Export tab -> download .xlsx
.venv/bin/python -m runway.cli forecast            # 13-week CF + revolver + covenant status
.venv/bin/python -m runway.cli bbc                 # borrowing base certificate
.venv/bin/python -m runway.cli compare             # lender sensitivities
.venv/bin/python -m runway.cli variance            # budget-to-actual + permitted-variance tests
.venv/bin/python -m runway.cli export              # out/<as-of> Lender Package.xlsx (+ tie-out)
.venv/bin/python -m pytest                         # 21 tests, incl. workbook-vs-engine tie-out
```

Every command takes `--data-dir`. Put real exports in `data/private/`, which is gitignored and never committed.

## The package (Excel tabs)

| Tab | What the lender sees |
|---|---|
| Summary | BBC snapshot, 13-week outlook, covenant & variance results, integrity checks, CFO certification |
| 13-Week CF | Receipts / disbursements by line, interest & fees, revolver draws/repayments, ending cash, liquidity memo |
| BBC | Aging buckets → ineligibles by category → eligible A/R → advance rate → reserves → availability; concentration test; certification |
| Revolver | A/R roll-forward → projected borrowing base → draws/repayments → interest & unused-fee accruals → availability & liquidity |
| Covenants | Weekly PASS/FAIL: minimum liquidity, minimum excess availability, no over-advance, positive cash; springing-FCCR trigger |
| Variance | Prior week and rolling 4-week forecast vs actual by line; permitted-variance tests |
| AR Aging | Invoice-level eligibility with reason codes, all formula-driven |
| AP Aging | Supports vendor disbursements |
| Terms | Facility terms, borrower assumptions, bank balances, scenario levers |
| Sensitivities | Base / collections +15 days / billings −20% / loss of largest customer / downside |

**Blue font = input, black = formula.** Eligibility reasons, the BBC waterfall, concentration, the A/R roll,
borrowing base, draws/repayments, accruals, cash, availability, liquidity, and covenant status are all live.
Change the advance rate on `Terms` and the whole file reflows. Sensitivities are the one exception: they are
engine values.

The workbook is saved without cached values. Excel calculates on open, but Quick Look / Preview shows blanks.

## How the numbers are built

1. **Operating lines.** Existing A/R collects at due date + customer lateness, net of expected loss.
   Items already past that date are spread over weeks 1–4. New billings collect after DSO. Payroll is
   biweekly. A/P is paid on due date, with a vendor run-rate after week 8. Recurring opex is mapped to lender lines.
2. **Borrowing base.** Invoice-level tests, in this order: future-dated → foreign → affiliate →
   U.S. Gov't without Assignment of Claims → past the ineligible-days limit → cross-aged.
   Concentration is applied last, at the customer level. Each dollar is excluded once, so the waterfall
   ties to the aging. The projected BB holds the as-of eligible % against the rolled-forward A/R.
3. **Revolver.** Solved sequentially, with no circularity: interest and unused fee accrue on the
   beginning balance and are paid in the week containing the 1st. The line draws to hold minimum
   operating cash (capped at availability), and repays any over-advance plus cash above min cash + sweep buffer.
4. **Covenants.** Tested on each week's ending position.

`runway/tieout.py` recalculates the exported workbook with the `formulas` engine and compares every week's
cash flow, revolver, BB, availability, liquidity, covenant result, and every invoice's eligibility reason to
the Python engine (tolerance $1). Both the CLI export and the app's download run it. The app won't offer
the download unless there are zero differences.

## Inputs (`data/sample/`)

| File | Source |
|---|---|
| `ar_aging_detail.csv`, `ap_aging_detail.csv` | QuickBooks A/R and A/P Aging Detail |
| `bank_balances.csv` | Balance sheet bank accounts |
| `payroll_register.csv` | Payroll history (biweekly) |
| `customer_terms.csv` | Customer type (drives eligibility), typical days late, collection risk |
| `recurring_expenses.csv` | Rent, benefits, SaaS, insurance, taxes (category = lender line) |
| `assumptions.csv` | As-of date, minimum operating cash, sweep buffer, billings, DSO, vendor run-rate |
| `facility_terms.csv` | Credit agreement terms: commitment, advance rate, ineligibles, reserves, pricing, covenants |
| `variance_history.csv` | Prior weeks' forecast vs actual by line |

Customers missing from `customer_terms.csv` are rejected rather than defaulted, because their type decides eligibility.

## Illustrative scenario terms

$2.0M commitment · 85% advance on eligible billed A/R · ineligible > 90 days from invoice ·
50% cross-age · 25% concentration (50% for assigned U.S. Gov't) · $250K availability block ·
SOFR 4.30% + 3.00% · 0.375% unused fee · $750K opening balance · min liquidity $3.0M ·
min excess availability $150K · springing FCCR below 12.5% of commitment ·
permitted variance 85% receipts / 115% disbursements over a rolling 4 weeks.

Calibrated so the base case passes all 13 weeks and the downside cases breach from week 11–13.
