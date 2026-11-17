"""Lender package workbook (openpyxl) with live formulas.

Hard-coded inputs (blue font) are the source data: terms, aging detail, the weekly
operating lines, and variance history. Everything derived is a formula: eligibility
reason codes, the BBC waterfall, concentration, the A/R roll, projected borrowing
base, draws/repayments, interest accruals, cash, availability, liquidity, covenant
PASS/FAIL, and variance tests. So the lender can tie the file and flex any term.

`export()` returns a `Workbook` plus a registry mapping logical names to cells,
which the tests use to evaluate the file and compare it to the Python engine.
"""
from __future__ import annotations

import io
from dataclasses import fields
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.worksheet.page import PageMargins

from . import borrowing_base as bb
from . import forecast as fc
from . import variance as var
from .data import Company, TERM_DESCRIPTIONS, FacilityTerms

MONEY = '#,##0;(#,##0);"-"'
MONEY2 = '#,##0.00;(#,##0.00);"-"'
PCT = "0.0%"
PCT2 = "0.000%"
DATE = "mm/dd/yyyy"
INT = "0"

NAVY = "1F3A5F"
F_TITLE = Font(bold=True, size=14, color=NAVY)
F_SUB = Font(italic=True, size=10, color="555555")
F_HDR = Font(bold=True, color="FFFFFF")
F_BOLD = Font(bold=True)
F_INPUT = Font(color="1F4FBF")  # blue = hard-coded input, black = formula
F_DRAFT = Font(bold=True, color="C00000")
F_SECTION = Font(bold=True, color=NAVY)
FILL_HDR = PatternFill("solid", fgColor=NAVY)
FILL_SECTION = PatternFill("solid", fgColor="E8EEF5")
FILL_KEY = PatternFill("solid", fgColor="FFF7E0")
TOP = Border(top=Side(style="thin"))
DOUBLE = Border(top=Side(style="thin"), bottom=Side(style="double"))

SHEETS = ["Summary", "13-Week CF", "BBC", "Revolver", "Covenants", "Variance",
          "AR Aging", "AP Aging", "Terms", "Sensitivities"]

TERM_FORMAT = {
    "lender_name": ("Lender", None), "facility_status": ("Facility status", None),
    "commitment": ("Revolving commitment", MONEY), "advance_rate": ("Advance rate on eligible A/R", PCT),
    "ineligible_days": ("Ineligible after (days from invoice)", INT),
    "cross_age_pct": ("Cross-age threshold", PCT), "concentration_limit": ("Concentration limit - commercial", PCT),
    "gov_concentration_limit": ("Concentration limit - U.S. Gov't (assigned)", PCT),
    "availability_block": ("Availability block (reserve)", MONEY), "sofr": ("Term SOFR", PCT2),
    "margin": ("Applicable margin", PCT2), "unused_fee": ("Unused line fee", PCT2),
    "opening_loan": ("Revolver outstanding at as-of", MONEY),
    "days_accrued": ("Days accrued & unpaid at as-of", INT),
    "min_liquidity": ("Covenant: minimum liquidity", MONEY),
    "min_excess_availability": ("Covenant: minimum excess availability", MONEY),
    "fccr_trigger_pct": ("Springing FCCR trigger (% of commitment)", PCT),
    "variance_receipts_min": ("Permitted variance: receipts floor", PCT),
    "variance_disb_max": ("Permitted variance: disbursements cap", PCT),
}


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


class _Builder:
    def __init__(self, company: Company, scenario: fc.Scenario):
        self.c = company
        self.sc = scenario
        self.res = fc.build(company, scenario)
        self.rows = self.res["rows"]
        self.bbc: bb.BBC = self.res["bbc"]
        self.var = var.report(company)
        self.wb = Workbook()
        self.wb.remove(self.wb.active)
        self.ws = {n: self.wb.create_sheet(n) for n in SHEETS}
        self.reg: dict[str, str] = {}       # name -> absolute ref, e.g. 'Terms'!$B$5
        self.wrow: dict[str, tuple[str, int]] = {}  # weekly row name -> (sheet, row)

    # ---------- helpers ----------
    @staticmethod
    def q(sheet: str) -> str:
        return f"'{sheet}'"

    def ref(self, name: str) -> str:
        return self.reg[name]

    def wk(self, name: str, w: int, absolute: bool = False) -> str:
        """Cell for weekly row `name`, week index w (0-based)."""
        sheet, r = self.wrow[name]
        return f"{self.q(sheet)}!{L(2 + w)}{r}"

    def put(self, ws, row, col, value, fmt=None, font=None, fill=None, border=None, align=None):
        cell = ws.cell(row=row, column=col, value=value)
        if fmt:
            cell.number_format = fmt
        if font:
            cell.font = font
        if fill:
            cell.fill = fill
        if border:
            cell.border = border
        if align:
            cell.alignment = align
        return cell

    def title(self, ws, text, sub=None, width_cols=15):
        self.put(ws, 1, 1, text, font=F_TITLE)
        self.put(ws, 2, 1, sub or f"{self.c.name}  |  Prepared for {self.c.terms.lender_name}  |  "
                 f"As of {_d(self.c.as_of):%m/%d/%Y}  |  Scenario: {self.sc.label}", font=F_SUB)
        if self.c.terms.is_draft:
            self.put(ws, 3, 1, "DRAFT - ILLUSTRATIVE FACILITY TERMS, NOT AN EXECUTED CREDIT AGREEMENT", font=F_DRAFT)
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5)
        ws.oddFooter.left.text = "&A"
        ws.oddFooter.right.text = "Page &P of &N"

    def header_row(self, ws, row, labels, start_col=1):
        for i, lab in enumerate(labels):
            self.put(ws, row, start_col + i, lab, font=F_HDR, fill=FILL_HDR,
                     align=Alignment(horizontal="center", vertical="center", wrap_text=True))

    def section(self, ws, row, text, ncols):
        for col in range(1, ncols + 1):
            ws.cell(row=row, column=col).fill = FILL_SECTION
        self.put(ws, row, 1, text, font=F_SECTION, fill=FILL_SECTION)

    def week_header(self, ws, row, total=True):
        self.header_row(ws, row, ["Week"] + [f"Wk {w + 1}" for w in range(fc.WEEKS)] + (["13-Wk Total"] if total else []))
        self.put(ws, row + 1, 1, "Week beginning", font=F_BOLD)
        self.put(ws, row + 2, 1, "Week ending", font=F_BOLD)
        for w, r in enumerate(self.rows):
            self.put(ws, row + 1, 2 + w, _d(r["week_start"]), DATE, align=Alignment(horizontal="center"))
            self.put(ws, row + 2, 2 + w, _d(r["week_end"]), DATE, align=Alignment(horizontal="center"))
        ws.column_dimensions["A"].width = 40
        for col in range(2, 2 + fc.WEEKS + 1):
            ws.column_dimensions[L(col)].width = 12.5
        ws.freeze_panes = ws.cell(row=row + 3, column=2)

    def weekly(self, sheet, row, name, label, values=None, formula=None, fmt=MONEY, total=None,
               font=None, border=None, fill=None):
        """Write a 13-week row. `values` = list of inputs; `formula(w)` = formula string per week.
        total: 'sum' | 'first' | 'last' | 'min' | 'max' | None."""
        ws = self.ws[sheet]
        self.wrow[name] = (sheet, row)
        self.put(ws, row, 1, label, font=font, border=border, fill=fill)
        for w in range(fc.WEEKS):
            if values is not None:
                self.put(ws, row, 2 + w, values[w], fmt, font=F_INPUT if font is None else font,
                         border=border, fill=fill)
            else:
                self.put(ws, row, 2 + w, formula(w), fmt, font=font, border=border, fill=fill)
        tc = 2 + fc.WEEKS
        rng = f"B{row}:{L(1 + fc.WEEKS)}{row}"
        agg = {"sum": f"=SUM({rng})", "first": f"=B{row}", "last": f"={L(1 + fc.WEEKS)}{row}",
               "min": f"=MIN({rng})", "max": f"=MAX({rng})"}
        if total:
            self.put(ws, row, tc, agg[total], fmt, font=F_BOLD, border=border, fill=fill)
            self.reg[f"{name}.total"] = f"{self.q(sheet)}!${L(tc)}${row}"

    # ---------- sheets ----------
    def terms_sheet(self):
        ws = self.ws["Terms"]
        self.title(ws, "Facility Terms & Assumptions")
        ws.column_dimensions["A"].width = 44
        ws.column_dimensions["B"].width = 20
        ws.column_dimensions["C"].width = 90
        r = 5
        self.header_row(ws, r, ["Term", "Value", "Description / source"])
        r += 1
        self.section(ws, r, "Facility terms (credit agreement)", 3)
        r += 1
        t = self.c.terms
        for f in fields(FacilityTerms):
            label, fmt = TERM_FORMAT.get(f.name, (f.name, None))
            self.put(ws, r, 1, label)
            self.put(ws, r, 2, getattr(t, f.name), fmt, font=F_INPUT)
            self.put(ws, r, 3, TERM_DESCRIPTIONS.get(f.name, ""), font=F_SUB)
            self.reg[f"t.{f.name}"] = f"Terms!$B${r}"
            r += 1
        self.put(ws, r, 1, "All-in rate on drawn balance")
        self.put(ws, r, 2, f"={self.ref('t.sofr')}+{self.ref('t.margin')}", PCT2)
        self.reg["t.rate"] = f"Terms!$B${r}"
        r += 1
        self.put(ws, r, 1, "Accrued interest at as-of")
        self.put(ws, r, 2, f"={self.ref('t.opening_loan')}*{self.ref('t.rate')}*{self.ref('t.days_accrued')}/360", MONEY2)
        self.reg["t.opening_int_accr"] = f"Terms!$B${r}"
        r += 1
        self.put(ws, r, 1, "Accrued unused fee at as-of")
        self.put(ws, r, 2, f"=MAX(0,{self.ref('t.commitment')}-{self.ref('t.opening_loan')})*{self.ref('t.unused_fee')}"
                           f"*{self.ref('t.days_accrued')}/360", MONEY2)
        self.reg["t.opening_fee_accr"] = f"Terms!$B${r}"
        r += 2

        self.section(ws, r, "Borrower assumptions", 3)
        r += 1
        c = self.c
        for key, label, val, fmt, desc in [
            ("a.as_of", "As-of date (week 1 begins)", _d(c.as_of), DATE, "Forecast start"),
            ("a.min_cash", "Minimum operating cash", c.min_cash_policy, MONEY,
             "Revolver is drawn to hold this level of cash"),
            ("a.sweep_buffer", "Sweep buffer", c.sweep_buffer, MONEY,
             "Cash above minimum operating cash + this buffer is applied to the revolver"),
            ("a.billings", "Weekly billings (scenario-adjusted)", self.rows[0]["billings"], MONEY,
             "New invoices issued per week; feeds the A/R roll"),
            ("a.dso", "DSO on new billings (days)", c.dso_days, INT, "Lag from billing to collection"),
            ("a.payroll", "Payroll per biweekly period", c.payroll_per_period, MONEY,
             "Average of the last four pay periods in payroll_register.csv"),
            ("a.vendor_run_rate", "Vendor run-rate (weeks 9-13)", c.weekly_vendor_run_rate, MONEY,
             "Un-billed vendor spend beyond the A/P aging window"),
        ]:
            self.put(ws, r, 1, label)
            self.put(ws, r, 2, val, fmt, font=F_INPUT)
            self.put(ws, r, 3, desc, font=F_SUB)
            self.reg[key] = f"Terms!$B${r}"
            r += 1
        r += 1
        self.section(ws, r, "Bank accounts at as-of", 3)
        r += 1
        first = r
        for a in c.bank_accounts:
            self.put(ws, r, 1, a.name)
            self.put(ws, r, 2, a.balance, MONEY, font=F_INPUT)
            r += 1
        self.put(ws, r, 1, "Opening cash", font=F_BOLD)
        self.put(ws, r, 2, f"=SUM(B{first}:B{r - 1})", MONEY, font=F_BOLD, border=DOUBLE)
        self.reg["a.opening_cash"] = f"Terms!$B${r}"
        r += 2
        self.section(ws, r, "Scenario levers", 3)
        r += 1
        for label, val, fmt in [("Scenario", self.sc.label, None),
                                ("Collections delay (days)", self.sc.collections_delay_days, INT),
                                ("Billings change", self.sc.billings_change_pct, PCT),
                                ("Opex change", self.sc.opex_change_pct, PCT),
                                ("Bad-debt multiplier", self.sc.bad_debt_multiplier, '0.00"x"'),
                                ("Stalled customers", ", ".join(self.sc.stalled_customers) or "none", None)]:
            self.put(ws, r, 1, label)
            self.put(ws, r, 2, val, fmt, font=F_INPUT)
            r += 1
        for wk, amt, lab in self.sc.one_time_items:
            self.put(ws, r, 1, f"One-time item, week {wk}: {lab}")
            self.put(ws, r, 2, amt, MONEY, font=F_INPUT)
            r += 1
        r += 1
        self.put(ws, r, 1, "Blue font = hard-coded input. Black font = formula.", font=F_SUB)

    def ar_sheet(self):
        ws = self.ws["AR Aging"]
        self.title(ws, "A/R Aging & Eligibility (supports the Borrowing Base Certificate)")
        hdr = ["Customer", "Customer type", "Invoice #", "Invoice date", "Due date", "Open balance",
               "Days from invoice", "Aging bucket", "Past-limit amount", "Customer past-limit share",
               "Ineligible reason", "Ineligible (pre-concentration)", "Eligible before concentration"]
        widths = [24, 20, 11, 12, 12, 14, 10, 10, 13, 12, 22, 15, 15]
        for i, wdt in enumerate(widths):
            ws.column_dimensions[L(i + 1)].width = wdt
        self.header_row(ws, 5, hdr)
        ws.row_dimensions[5].height = 45
        ws.freeze_panes = "B6"
        first = 6
        last = first + len(self.bbc.invoices) - 1
        self.ar_first, self.ar_last = first, last
        rng = lambda col: f"'AR Aging'!${col}${first}:${col}${last}"  # noqa: E731
        T = self.ref
        for i, inv in enumerate(self.bbc.invoices):
            r = first + i
            self.put(ws, r, 1, inv["customer"], font=F_INPUT)
            self.put(ws, r, 2, inv["customer_type"], font=F_INPUT)
            self.put(ws, r, 3, inv["num"], font=F_INPUT)
            self.put(ws, r, 4, _d(inv["invoice_date"]), DATE, font=F_INPUT)
            self.put(ws, r, 5, _d(inv["due_date"]), DATE, font=F_INPUT)
            self.put(ws, r, 6, inv["amount"], MONEY2, font=F_INPUT)
            self.put(ws, r, 7, f"={T('a.as_of')}-D{r}", INT)
            self.put(ws, r, 8, f'=IF(G{r}<0,"Future",IF(G{r}<=30,"0-30",IF(G{r}<=60,"31-60",IF(G{r}<=90,"61-90","91+"))))')
            self.put(ws, r, 9, f"=IF(G{r}>{T('t.ineligible_days')},F{r},0)", MONEY2)
            self.put(ws, r, 10, f"=SUMIFS({rng('I')},{rng('A')},A{r})/SUMIFS({rng('F')},{rng('A')},A{r})", PCT)
            self.put(ws, r, 11,
                     f'=IF(G{r}<0,"{bb.FUTURE}",IF(B{r}="Foreign","{bb.FOREIGN}",IF(B{r}="Affiliate","{bb.AFFILIATE}",'
                     f'IF(B{r}="US Gov - Unassigned","{bb.GOV_NO_AOC}",IF(G{r}>{T("t.ineligible_days")},"{bb.PAST_DUE}",'
                     f'IF(J{r}>={T("t.cross_age_pct")},"{bb.CROSS_AGED}",""))))))')
            self.put(ws, r, 12, f'=IF(K{r}="",0,F{r})', MONEY2)
            self.put(ws, r, 13, f"=F{r}-L{r}", MONEY2)
            self.reg[f"ar.reason.{i}"] = f"'AR Aging'!$K${r}"
        t = last + 1
        self.put(ws, t, 1, "Total", font=F_BOLD, border=DOUBLE)
        for col in ("F", "I", "L", "M"):
            self.put(ws, t, ord(col) - 64, f"=SUM({col}{first}:{col}{last})", MONEY2, font=F_BOLD, border=DOUBLE)
        self.reg["ar.total"] = f"'AR Aging'!$F${t}"
        self.reg["ar.eligible_pre_conc"] = f"'AR Aging'!$M${t}"
        self.ar_rng = rng

    def bbc_sheet(self):
        ws = self.ws["BBC"]
        self.title(ws, "Borrowing Base Certificate")
        ws.column_dimensions["A"].width = 44
        for col, wdt in zip("BCDEFGHI", [16, 16, 16, 12, 12, 16, 16, 30]):
            ws.column_dimensions[col].width = wdt
        T, R = self.ref, self.ar_rng
        r = 5
        self.put(ws, r, 1, f"Borrower: {self.c.name}", font=F_BOLD)
        self.put(ws, r + 1, 1, f"Lender: {self.c.terms.lender_name}", font=F_BOLD)
        self.put(ws, r + 2, 1, "Certificate date:")
        self.put(ws, r + 2, 2, f"={T('a.as_of')}", DATE)
        r += 4

        self.header_row(ws, r, ["A/R aging (days from invoice)", "Amount", "% of total"])
        r += 1
        b_first = r
        for b in bb.AGING_BUCKETS:
            self.put(ws, r, 1, b)
            self.put(ws, r, 2, f'=SUMIFS({R("F")},{R("H")},"{b}")', MONEY)
            r += 1
        self.put(ws, r, 1, "Total A/R per aging", font=F_BOLD)
        self.put(ws, r, 2, f"=SUM(B{b_first}:B{r - 1})", MONEY, font=F_BOLD, border=DOUBLE)
        self.reg["bbc.aging_total"] = f"BBC!$B${r}"
        for rr in range(b_first, r + 1):
            self.put(ws, rr, 3, f"=IF($B${r}=0,0,B{rr}/$B${r})", PCT)
        r += 2

        self.header_row(ws, r, ["Borrowing base calculation", "Amount", "Reference"])
        r += 1
        self.put(ws, r, 1, "Gross accounts receivable", font=F_BOLD)
        self.put(ws, r, 2, f"={T('ar.total')}", MONEY, font=F_BOLD)
        self.put(ws, r, 3, "AR Aging total", font=F_SUB)
        self.reg["bbc.gross"] = f"BBC!$B${r}"
        r += 1
        self.put(ws, r, 1, "Less ineligible accounts:", font=F_SECTION)
        r += 1
        i_first = r
        conc_row = None
        for cat in bb.INELIGIBLE_CATEGORIES:
            self.put(ws, r, 1, f"   {cat}")
            self.put(ws, r, 3, bb.CATEGORY_HELP[cat], font=F_SUB)
            if cat == bb.CONCENTRATION:
                conc_row = r
            else:
                self.put(ws, r, 2, f'=-SUMIFS({R("L")},{R("K")},"{cat}")', MONEY)
            self.reg[f"bbc.inel.{cat}"] = f"BBC!$B${r}"
            r += 1
        self.put(ws, r, 1, "Total ineligible accounts", font=F_BOLD)
        self.put(ws, r, 2, f"=SUM(B{i_first}:B{r - 1})", MONEY, font=F_BOLD, border=TOP)
        r += 1
        self.put(ws, r, 1, "Eligible accounts receivable", font=F_BOLD)
        self.put(ws, r, 2, f"=B{i_first - 2}+B{r - 1}", MONEY, font=F_BOLD, border=TOP)
        self.reg["bbc.eligible"] = f"BBC!$B${r}"
        elig = r
        r += 1
        lines = [
            ("Advance rate", f"={T('t.advance_rate')}", PCT, "bbc.adv"),
            ("Gross availability", f"=B{elig}*B{elig + 1}", MONEY, "bbc.gross_avail"),
            ("Less: availability block / reserves", f"=-{T('t.availability_block')}", MONEY, "bbc.reserve"),
            ("Borrowing base", f"=B{elig + 2}+B{elig + 3}", MONEY, "bbc.bb"),
            ("Revolving commitment", f"={T('t.commitment')}", MONEY, "bbc.commitment"),
            ("Maximum borrowing (lesser of commitment and borrowing base)", f"=MAX(0,MIN(B{elig + 4},B{elig + 5}))",
             MONEY, "bbc.max"),
            ("Less: revolver outstanding", f"=-{T('t.opening_loan')}", MONEY, "bbc.loan"),
            ("Availability / (over-advance)", f"=B{elig + 6}+B{elig + 7}", MONEY, "bbc.availability"),
        ]
        for label, f, fmt, key in lines:
            bold = key in ("bbc.bb", "bbc.availability")
            self.put(ws, r, 1, label, font=F_BOLD if bold else None, fill=FILL_KEY if key == "bbc.availability" else None)
            self.put(ws, r, 2, f, fmt, font=F_BOLD if bold else None,
                     border=DOUBLE if key == "bbc.availability" else None,
                     fill=FILL_KEY if key == "bbc.availability" else None)
            self.reg[key] = f"BBC!$B${r}"
            r += 1
        self.put(ws, r, 1, "Eligible % of gross A/R (used to project the borrowing base)")
        self.put(ws, r, 2, f"=IF(B{i_first - 2}=0,0,B{elig}/B{i_first - 2})", PCT)
        self.reg["bbc.elig_pct"] = f"BBC!$B${r}"
        r += 2

        self.header_row(ws, r, ["Concentration test", "Customer type", "Gross A/R", "Eligible pre-conc.",
                                "Share", "Limit", "Limit $", "Excess (ineligible)"])
        r += 1
        c_first = r
        pool = T("ar.eligible_pre_conc")
        for cc in self.bbc.concentration:
            self.put(ws, r, 1, cc["customer"])
            self.put(ws, r, 2, cc["customer_type"])
            self.put(ws, r, 3, f'=SUMIFS({R("F")},{R("A")},A{r})', MONEY)
            self.put(ws, r, 4, f'=SUMIFS({R("M")},{R("A")},A{r})', MONEY)
            self.put(ws, r, 5, f"=IF({pool}=0,0,D{r}/{pool})", PCT)
            self.put(ws, r, 6, f'=IF(B{r}="US Gov - Assigned",{T("t.gov_concentration_limit")},{T("t.concentration_limit")})', PCT)
            self.put(ws, r, 7, f"=F{r}*{pool}", MONEY)
            self.put(ws, r, 8, f"=MAX(0,D{r}-G{r})", MONEY)
            r += 1
        self.put(ws, r, 1, "Total", font=F_BOLD)
        for col in "CDH":
            self.put(ws, r, ord(col) - 64, f"=SUM({col}{c_first}:{col}{r - 1})", MONEY, font=F_BOLD, border=DOUBLE)
        self.put(ws, conc_row, 2, f"=-H{r}", MONEY)
        r += 2

        cert = ("The undersigned officer certifies that the information above is true, correct and complete, "
                "has been prepared in accordance with the Credit Agreement, and that the Borrower is in "
                "compliance with its terms as of the certificate date.")
        self.put(ws, r, 1, cert, align=Alignment(wrap_text=True, vertical="top"))
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        ws.row_dimensions[r].height = 45
        r += 2
        for lab in ("Signature: ______________________________", "Name / Title: ___________________________",
                    "Date: ___________________"):
            self.put(ws, r, 1, lab)
            r += 1

    def cf_sheet(self):
        ws = self.ws["13-Week CF"]
        self.title(ws, "13-Week Cash Flow Forecast")
        self.week_header(ws, 5)
        r = 9
        S = "13-Week CF"
        self.section(ws, r, "OPERATING RECEIPTS", 15)
        r += 1
        rec_first = r
        for key, label in fc.RECEIPT_LINES:
            self.weekly(S, r, key, label, values=[x[key] for x in self.rows], total="sum")
            r += 1
        self.weekly(S, r, "total_receipts", "Total operating receipts",
                    formula=lambda w: f"=SUM({L(2 + w)}{rec_first}:{L(2 + w)}{r - 1})", total="sum",
                    font=F_BOLD, border=TOP)
        r += 2
        self.section(ws, r, "OPERATING DISBURSEMENTS", 15)
        r += 1
        dis_first = r
        for key, label in fc.DISB_LINES:
            self.weekly(S, r, key, label, values=[-x[key] for x in self.rows], total="sum")
            r += 1
        self.weekly(S, r, "total_disbursements", "Total operating disbursements",
                    formula=lambda w: f"=SUM({L(2 + w)}{dis_first}:{L(2 + w)}{r - 1})", total="sum",
                    font=F_BOLD, border=TOP)
        r += 2
        self.weekly(S, r, "net_operating_cf", "Net operating cash flow",
                    formula=lambda w: f"={self.wk('total_receipts', w)}+{self.wk('total_disbursements', w)}",
                    total="sum", font=F_BOLD)
        r += 1
        self.weekly(S, r, "one_time", "Non-operating / one-time items", values=[x["one_time"] for x in self.rows],
                    total="sum")
        r += 1
        # financing rows are filled after the Revolver sheet exists (forward references are fine in Excel)
        self.cf_int_row, self.cf_fee_row = r, r + 1
        self.wrow["cf_interest"] = (S, r)
        self.wrow["cf_fee"] = (S, r + 1)
        r += 2
        self.wrow["net_cf_before_revolver"] = (S, r)
        r += 2
        self.wrow["beginning_cash"] = (S, r)
        self.wrow["draws"] = (S, r + 1)
        self.wrow["repayments"] = (S, r + 2)
        self.wrow["ending_cash"] = (S, r + 3)
        r += 5
        self.wrow["memo_start"] = (S, r)

    def cf_financing(self):
        S = "13-Week CF"
        ws = self.ws[S]
        self.weekly(S, self.cf_int_row, "cf_interest", "Interest paid",
                    formula=lambda w: f"=-{self.wk('interest_paid', w)}", total="sum")
        self.weekly(S, self.cf_fee_row, "cf_fee", "Unused line fee paid",
                    formula=lambda w: f"=-{self.wk('fee_paid', w)}", total="sum")
        r = self.wrow["net_cf_before_revolver"][1]
        self.weekly(S, r, "net_cf_before_revolver", "Net cash flow before revolver",
                    formula=lambda w: f"={self.wk('net_operating_cf', w)}+{self.wk('one_time', w)}"
                                      f"+{self.wk('cf_interest', w)}+{self.wk('cf_fee', w)}",
                    total="sum", font=F_BOLD, border=TOP)
        r = self.wrow["beginning_cash"][1]
        self.weekly(S, r, "beginning_cash", "Beginning cash",
                    formula=lambda w: f"={self.ref('a.opening_cash')}" if w == 0 else f"={self.wk('ending_cash', w - 1)}",
                    total="first")
        self.weekly(S, r + 1, "draws", "Revolver draws", formula=lambda w: f"={self.wk('draw', w)}", total="sum")
        self.weekly(S, r + 2, "repayments", "Revolver repayments", formula=lambda w: f"=-{self.wk('repay', w)}",
                    total="sum")
        self.weekly(S, r + 3, "ending_cash", "Ending cash",
                    formula=lambda w: f"={self.wk('beginning_cash', w)}+{self.wk('net_cf_before_revolver', w)}"
                                      f"+{self.wk('draws', w)}+{self.wk('repayments', w)}",
                    total="last", font=F_BOLD, border=DOUBLE, fill=FILL_KEY)
        r = self.wrow["memo_start"][1]
        self.section(ws, r, "MEMO: REVOLVER & LIQUIDITY", 15)
        for i, (name, label, src, tot) in enumerate([
                ("m_loan", "Revolver balance (end of week)", "loan_end", "last"),
                ("m_maxb", "Maximum borrowing (lesser of commitment / BB)", "max_borrowing", "last"),
                ("m_avail", "Availability", "availability", "min"),
                ("m_liq", "Liquidity (cash + availability)", "liquidity", "min")]):
            self.weekly(S, r + 1 + i, name, label, formula=lambda w, s=src: f"={self.wk(s, w)}", total=tot)
        self.put(ws, r + 5, 1, "13-Wk Total column shows the ending balance for balances and the minimum for "
                               "availability and liquidity.", font=F_SUB)

    def revolver_sheet(self):
        S = "Revolver"
        ws = self.ws[S]
        self.title(ws, "Revolver, Borrowing Base Projection & Availability")
        self.week_header(ws, 5)
        T = self.ref
        r = 9
        self.section(ws, r, "A/R ROLL-FORWARD", 15)
        r += 1
        # rows referenced before they are written (prior-week links)
        self.wrow["ar_end"] = (S, r + 4)
        self.wrow["loan_end"] = (S, r + 20)
        self.wrow["int_accr_end"] = (S, r + 27)
        self.wrow["fee_accr_end"] = (S, r + 31)
        self.weekly(S, r, "ar_beg", "Beginning A/R",
                    formula=lambda w: f"={T('bbc.gross')}" if w == 0 else f"={self.wk('ar_end', w - 1)}", total="first")
        self.weekly(S, r + 1, "billings", "+ Billings", values=[x["billings"] for x in self.rows], total="sum")
        self.weekly(S, r + 2, "collections", "- Collections",
                    formula=lambda w: "=-(" + "+".join(self.wk(k, w) for k, _ in fc.RECEIPT_LINES) + ")", total="sum")
        self.weekly(S, r + 3, "write_offs", "- Expected write-offs / dilution",
                    values=[-x["write_offs"] for x in self.rows], total="sum")
        self.weekly(S, r + 4, "ar_end", "Ending A/R",
                    formula=lambda w: f"=SUM({L(2 + w)}{r}:{L(2 + w)}{r + 3})", total="last", font=F_BOLD, border=TOP)
        r += 6
        self.section(ws, r, "PROJECTED BORROWING BASE", 15)
        r += 1
        self.weekly(S, r, "elig_pct", "Eligible % (held at as-of BBC)", formula=lambda w: f"={T('bbc.elig_pct')}",
                    fmt=PCT)
        self.weekly(S, r + 1, "eligible_ar", "Projected eligible A/R",
                    formula=lambda w: f"={self.wk('ar_end', w)}*{self.wk('elig_pct', w)}")
        self.weekly(S, r + 2, "borrowing_base", "Borrowing base (x advance rate, less reserves)",
                    formula=lambda w: f"={self.wk('eligible_ar', w)}*{T('t.advance_rate')}-{T('t.availability_block')}")
        self.weekly(S, r + 3, "max_borrowing", "Maximum borrowing (lesser of commitment / BB)",
                    formula=lambda w: f"=MAX(0,MIN({T('t.commitment')},{self.wk('borrowing_base', w)}))",
                    font=F_BOLD)
        r += 5
        self.section(ws, r, "REVOLVER", 15)
        r += 1
        assert self.wrow["loan_end"][1] == r + 7
        self.weekly(S, r, "loan_beg", "Beginning balance",
                    formula=lambda w: f"={T('t.opening_loan')}" if w == 0 else f"={self.wk('loan_end', w - 1)}",
                    total="first")
        self.weekly(S, r + 1, "cash_before", "Cash before revolver",
                    formula=lambda w: f"={self.wk('beginning_cash', w)}+{self.wk('net_cf_before_revolver', w)}")
        self.weekly(S, r + 2, "avail_pre", "Availability before draw",
                    formula=lambda w: f"=MAX(0,{self.wk('max_borrowing', w)}-{self.wk('loan_beg', w)})")
        self.weekly(S, r + 3, "overadvance", "Over-advance (mandatory repayment)",
                    formula=lambda w: f"=MAX(0,{self.wk('loan_beg', w)}-{self.wk('max_borrowing', w)})")
        self.weekly(S, r + 4, "draw", "Draw (to hold minimum operating cash)",
                    formula=lambda w: f"=IF({self.wk('cash_before', w)}<{T('a.min_cash')},"
                                      f"MIN({T('a.min_cash')}-{self.wk('cash_before', w)},{self.wk('avail_pre', w)}),0)",
                    total="sum")
        self.weekly(S, r + 5, "sweep", "Excess cash available to sweep",
                    formula=lambda w: f"=MAX(0,{self.wk('cash_before', w)}-({T('a.min_cash')}+{T('a.sweep_buffer')}))")
        self.weekly(S, r + 6, "repay", "Repayment",
                    formula=lambda w: f"=MIN({self.wk('loan_beg', w)},MAX({self.wk('overadvance', w)},"
                                      f"{self.wk('sweep', w)}),MAX(0,{self.wk('cash_before', w)}))", total="sum")
        self.weekly(S, r + 7, "loan_end", "Ending balance",
                    formula=lambda w: f"={self.wk('loan_beg', w)}+{self.wk('draw', w)}-{self.wk('repay', w)}",
                    total="last", font=F_BOLD, border=TOP, fill=FILL_KEY)
        r += 9
        self.section(ws, r, "INTEREST & FEES (paid monthly in arrears; accrue on beginning balance)", 15)
        r += 1
        flags = [x["pay_flag"] for x in self.rows]
        assert self.wrow["int_accr_end"][1] == r + 4 and self.wrow["fee_accr_end"][1] == r + 8
        self.weekly(S, r, "pay_flag", "Payment week (contains the 1st)", values=flags, fmt=INT)
        self.weekly(S, r + 1, "int_accr_beg", "Accrued interest, beginning",
                    formula=lambda w: f"={T('t.opening_int_accr')}" if w == 0 else f"={self.wk('int_accr_end', w - 1)}")
        self.weekly(S, r + 2, "interest_paid", "Interest paid",
                    formula=lambda w: f"=IF({self.wk('pay_flag', w)}=1,{self.wk('int_accr_beg', w)},0)", total="sum")
        self.weekly(S, r + 3, "int_accrual", "Interest accrual (beginning balance x rate x 7/360)",
                    formula=lambda w: f"={self.wk('loan_beg', w)}*{T('t.rate')}*7/360", total="sum")
        self.weekly(S, r + 4, "int_accr_end", "Accrued interest, ending",
                    formula=lambda w: f"={self.wk('int_accr_beg', w)}-{self.wk('interest_paid', w)}+{self.wk('int_accrual', w)}")
        self.weekly(S, r + 5, "fee_accr_beg", "Accrued unused fee, beginning",
                    formula=lambda w: f"={T('t.opening_fee_accr')}" if w == 0 else f"={self.wk('fee_accr_end', w - 1)}")
        self.weekly(S, r + 6, "fee_paid", "Unused fee paid",
                    formula=lambda w: f"=IF({self.wk('pay_flag', w)}=1,{self.wk('fee_accr_beg', w)},0)", total="sum")
        self.weekly(S, r + 7, "fee_accrual", "Unused fee accrual",
                    formula=lambda w: f"=MAX(0,{T('t.commitment')}-{self.wk('loan_beg', w)})*{T('t.unused_fee')}*7/360",
                    total="sum")
        self.weekly(S, r + 8, "fee_accr_end", "Accrued unused fee, ending",
                    formula=lambda w: f"={self.wk('fee_accr_beg', w)}-{self.wk('fee_paid', w)}+{self.wk('fee_accrual', w)}")
        r += 10
        self.section(ws, r, "AVAILABILITY & LIQUIDITY", 15)
        r += 1
        self.weekly(S, r, "cash_end", "Ending cash", formula=lambda w: f"={self.wk('ending_cash', w)}", total="last")
        self.weekly(S, r + 1, "availability", "Availability / (over-advance)",
                    formula=lambda w: f"={self.wk('max_borrowing', w)}-{self.wk('loan_end', w)}", total="min")
        self.weekly(S, r + 2, "liquidity", "Liquidity (cash + availability)",
                    formula=lambda w: f"={self.wk('cash_end', w)}+MAX(0,{self.wk('availability', w)})",
                    total="min", font=F_BOLD, border=DOUBLE, fill=FILL_KEY)

    def covenant_sheet(self):
        S = "Covenants"
        ws = self.ws[S]
        self.title(ws, "Covenant Compliance (tested weekly)")
        self.week_header(ws, 5, total=False)
        T = self.ref
        r = 9
        st = lambda cond: f'=IF({cond},"PASS","FAIL")'  # noqa: E731
        self.section(ws, r, "MINIMUM LIQUIDITY", 14)
        self.weekly(S, r + 1, "cv_liq", "Liquidity (cash + availability)", formula=lambda w: f"={self.wk('liquidity', w)}")
        self.weekly(S, r + 2, "cv_liq_req", "Required minimum", formula=lambda w: f"={T('t.min_liquidity')}")
        self.weekly(S, r + 3, "cv_liq_cush", "Cushion / (shortfall)",
                    formula=lambda w: f"={self.wk('cv_liq', w)}-{self.wk('cv_liq_req', w)}")
        self.weekly(S, r + 4, "cv_liq_st", "Status", formula=lambda w: st(f"{self.wk('cv_liq_cush', w)}>=0"),
                    fmt=None, font=F_BOLD)
        r += 6
        self.section(ws, r, "MINIMUM EXCESS AVAILABILITY", 14)
        self.weekly(S, r + 1, "cv_ea", "Availability", formula=lambda w: f"={self.wk('availability', w)}")
        self.weekly(S, r + 2, "cv_ea_req", "Required minimum", formula=lambda w: f"={T('t.min_excess_availability')}")
        self.weekly(S, r + 3, "cv_ea_cush", "Cushion / (shortfall)",
                    formula=lambda w: f"={self.wk('cv_ea', w)}-{self.wk('cv_ea_req', w)}")
        self.weekly(S, r + 4, "cv_ea_st", "Status", formula=lambda w: st(f"{self.wk('cv_ea_cush', w)}>=0"),
                    fmt=None, font=F_BOLD)
        r += 6
        self.section(ws, r, "NO OVER-ADVANCE / POSITIVE CASH", 14)
        self.weekly(S, r + 1, "cv_oa_st", "Revolver balance <= maximum borrowing",
                    formula=lambda w: st(f"{self.wk('loan_end', w)}<={self.wk('max_borrowing', w)}+0.005"), fmt=None)
        self.weekly(S, r + 2, "cv_cash_st", "Ending cash >= 0",
                    formula=lambda w: st(f"{self.wk('ending_cash', w)}>=0"), fmt=None)
        r += 4
        self.section(ws, r, "SPRINGING FIXED CHARGE COVERAGE (informational trigger)", 14)
        self.weekly(S, r + 1, "cv_fccr_lvl", "Trigger: availability below",
                    formula=lambda w: f"={T('t.fccr_trigger_pct')}*{T('t.commitment')}")
        self.weekly(S, r + 2, "cv_fccr_st", "FCCR test in effect?",
                    formula=lambda w: f'=IF({self.wk("availability", w)}<{self.wk("cv_fccr_lvl", w)},"TRIGGERED","No")',
                    fmt=None)
        r += 4
        self.weekly(S, r, "cv_overall", "OVERALL COVENANT STATUS",
                    formula=lambda w: f'=IF(COUNTIF({L(2 + w)}{self.wrow["cv_liq_st"][1]}:{L(2 + w)}{r - 3},"FAIL")=0,"PASS","FAIL")',
                    fmt=None, font=F_BOLD, fill=FILL_KEY)
        for row in (self.wrow[k][1] for k in ("cv_liq_st", "cv_ea_st", "cv_oa_st", "cv_cash_st", "cv_fccr_st", "cv_overall")):
            for w in range(fc.WEEKS):
                ws.cell(row=row, column=2 + w).alignment = Alignment(horizontal="center")
        rng = f"B{r}:{L(1 + fc.WEEKS)}{r}"
        self.put(ws, r + 2, 1, "Weeks failing any covenant", font=F_BOLD)
        self.put(ws, r + 2, 2, f'=COUNTIF({rng},"FAIL")', INT, font=F_BOLD)
        self.reg["cv.fail_weeks"] = f"Covenants!$B${r + 2}"
        frow = self.wrow["cv_fccr_st"][1]
        self.put(ws, r + 3, 1, "Weeks with springing FCCR triggered", font=F_BOLD)
        self.put(ws, r + 3, 2, f'=COUNTIF(B{frow}:{L(1 + fc.WEEKS)}{frow},"TRIGGERED")', INT, font=F_BOLD)
        self.reg["cv.fccr_weeks"] = f"Covenants!$B${r + 3}"

    def variance_sheet(self):
        S = "Variance"
        ws = self.ws[S]
        self.title(ws, "Budget-to-Actual Variance Report")
        v = self.var
        ws.column_dimensions["A"].width = 30
        for col in "BCDEFGHIJK":
            ws.column_dimensions[col].width = 14
        if not v["available"]:
            self.put(ws, 5, 1, "No variance_history.csv supplied.")
            return
        # raw data block (columns M:Q)
        dc = 13
        self.header_row(ws, 5, ["Week ending", "Line", "Forecast", "Actual", "In window", "Prior wk"], start_col=dc)
        for i, wdt in enumerate([12, 14, 12, 12, 10, 9]):
            ws.column_dimensions[L(dc + i)].width = wdt
        first = 6
        hist = sorted(self.c.variance, key=lambda x: (x.week_ending, x.line))
        last = first + len(hist) - 1
        self.put(ws, 5, 10, "Prior week ending", font=F_BOLD)
        self.put(ws, 5, 11, f"=MAX(M{first}:M{last})", DATE, font=F_BOLD)
        self.put(ws, 6, 10, "Window start", font=F_BOLD)
        self.put(ws, 6, 11, f"=K5-7*({var.TEST_WINDOW}-1)", DATE, font=F_BOLD)
        for i, h in enumerate(hist):
            r = first + i
            self.put(ws, r, dc, _d(h.week_ending), DATE, font=F_INPUT)
            self.put(ws, r, dc + 1, h.line, font=F_INPUT)
            self.put(ws, r, dc + 2, h.forecast, MONEY, font=F_INPUT)
            self.put(ws, r, dc + 3, h.actual, MONEY, font=F_INPUT)
            self.put(ws, r, dc + 4, f"=IF(M{r}>=$K$6,1,0)", INT)
            self.put(ws, r, dc + 5, f"=IF(M{r}=$K$5,1,0)", INT)
        rg = lambda col: f"${col}${first}:${col}${last}"  # noqa: E731

        r = 8
        self.header_row(ws, r, ["Line", "Prior wk forecast", "Prior wk actual", "Variance fav/(unfav)", "Actual % of fcst",
                                f"{var.TEST_WINDOW}-wk forecast", f"{var.TEST_WINDOW}-wk actual",
                                "Variance fav/(unfav)", "Actual % of fcst"])
        ws.row_dimensions[r].height = 32
        r += 1
        rows_by_kind = {"receipt": [], "disb": [], "financing": []}
        for key, label, kind in var.VARIANCE_LINES:
            sign = "" if kind == "receipt" else "-"
            self.put(ws, r, 1, label)
            self.put(ws, r, 2, f'=SUMIFS({rg("O")},{rg("N")},"{key}",{rg("R")},1)', MONEY)
            self.put(ws, r, 3, f'=SUMIFS({rg("P")},{rg("N")},"{key}",{rg("R")},1)', MONEY)
            self.put(ws, r, 4, f"={sign}(C{r}-B{r})", MONEY)
            self.put(ws, r, 5, f'=IF(B{r}=0,"",C{r}/B{r})', PCT)
            self.put(ws, r, 6, f'=SUMIFS({rg("O")},{rg("N")},"{key}",{rg("Q")},1)', MONEY)
            self.put(ws, r, 7, f'=SUMIFS({rg("P")},{rg("N")},"{key}",{rg("Q")},1)', MONEY)
            self.put(ws, r, 8, f"={sign}(G{r}-F{r})", MONEY)
            self.put(ws, r, 9, f'=IF(F{r}=0,"",G{r}/F{r})', PCT)
            rows_by_kind[kind].append(r)
            if kind == "receipt":
                r += 1
                self.put(ws, r, 1, "Operating disbursements:", font=F_SECTION)
            r += 1
        d1, d2 = rows_by_kind["disb"][0], rows_by_kind["disb"][-1]
        self.put(ws, r, 1, "Total operating disbursements", font=F_BOLD)
        for col in "BCDFGH":
            self.put(ws, r, ord(col) - 64, f"=SUM({col}{d1}:{col}{d2})", MONEY, font=F_BOLD, border=TOP)
        self.put(ws, r, 5, f'=IF(B{r}=0,"",C{r}/B{r})', PCT, font=F_BOLD, border=TOP)
        self.put(ws, r, 9, f'=IF(F{r}=0,"",G{r}/F{r})', PCT, font=F_BOLD, border=TOP)
        tot_dis = r
        rec = rows_by_kind["receipt"][0]
        r += 3
        t = self.ref
        self.header_row(ws, r, ["Permitted variance test", "Forecast", "Actual", "Actual % of fcst", "Limit", "Status"])
        r += 1
        self.put(ws, r, 1, f"Receipts (rolling {var.TEST_WINDOW} wks)")
        self.put(ws, r, 2, f"=F{rec}", MONEY)
        self.put(ws, r, 3, f"=G{rec}", MONEY)
        self.put(ws, r, 4, f'=IF(B{r}=0,"",C{r}/B{r})', PCT)
        self.put(ws, r, 5, f"={t('t.variance_receipts_min')}", PCT)
        self.put(ws, r, 6, f'=IF(B{r}=0,"PASS",IF(D{r}>=E{r},"PASS","FAIL"))', font=F_BOLD)
        self.reg["var.rec_status"] = f"Variance!$F${r}"
        self.reg["var.rec_ratio"] = f"Variance!$D${r}"
        r += 1
        self.put(ws, r, 1, f"Operating disbursements (rolling {var.TEST_WINDOW} wks)")
        self.put(ws, r, 2, f"=F{tot_dis}", MONEY)
        self.put(ws, r, 3, f"=G{tot_dis}", MONEY)
        self.put(ws, r, 4, f'=IF(B{r}=0,"",C{r}/B{r})', PCT)
        self.put(ws, r, 5, f"={t('t.variance_disb_max')}", PCT)
        self.put(ws, r, 6, f'=IF(B{r}=0,"PASS",IF(D{r}<=E{r},"PASS","FAIL"))', font=F_BOLD)
        self.reg["var.dis_status"] = f"Variance!$F${r}"
        self.reg["var.dis_ratio"] = f"Variance!$D${r}"
        r += 2
        self.put(ws, r, 1, "Favorable = receipts above forecast or disbursements below forecast.", font=F_SUB)

    def ap_sheet(self):
        ws = self.ws["AP Aging"]
        self.title(ws, "A/P Aging (supports vendor disbursements)")
        self.header_row(ws, 5, ["Vendor", "Bill #", "Bill date", "Due date", "Open balance", "Days past due", "Status"])
        for col, wdt in zip("ABCDEFG", [22, 12, 12, 12, 14, 12, 12]):
            ws.column_dimensions[col].width = wdt
        ws.freeze_panes = "A6"
        r = 6
        for b in sorted(self.c.bills, key=lambda x: x.due_date):
            self.put(ws, r, 1, b.vendor, font=F_INPUT)
            self.put(ws, r, 2, b.num, font=F_INPUT)
            self.put(ws, r, 3, _d(b.bill_date), DATE, font=F_INPUT)
            self.put(ws, r, 4, _d(b.due_date), DATE, font=F_INPUT)
            self.put(ws, r, 5, b.amount, MONEY2, font=F_INPUT)
            self.put(ws, r, 6, f"=MAX(0,{self.ref('a.as_of')}-D{r})", INT)
            self.put(ws, r, 7, f'=IF(F{r}=0,"Current",IF(F{r}<=30,"1-30 past due","31+ past due"))')
            r += 1
        self.put(ws, r, 1, "Total", font=F_BOLD)
        self.put(ws, r, 5, f"=SUM(E6:E{r - 1})", MONEY2, font=F_BOLD, border=DOUBLE)

    def sensitivity_sheet(self, scenarios: dict[str, fc.Scenario] | None):
        ws = self.ws["Sensitivities"]
        self.title(ws, "Sensitivity Analysis", sub=f"{self.c.name}  |  Engine output for each scenario "
                   "(values, not live formulas; regenerate the package to refresh)")
        hdr = ["Scenario", "Ending cash (wk 13)", "Lowest cash", "Low wk", "Peak revolver", "Lowest availability",
               "Low wk", "Lowest liquidity", "Low wk", "Covenants", "First breach wk", "FCCR trigger wks", "Detail"]
        self.header_row(ws, 5, hdr)
        ws.row_dimensions[5].height = 32
        for col, wdt in zip("ABCDEFGHIJKLM", [44, 14, 14, 7, 14, 14, 7, 14, 7, 11, 10, 14, 60]):
            ws.column_dimensions[col].width = wdt
        r = 6
        for x in fc.compare(self.c, scenarios):
            vals = [x["label"], x["ending_cash"], x["lowest_cash"], x["lowest_cash_week"], x["peak_loan"],
                    x["lowest_availability"], x["lowest_availability_week"], x["lowest_liquidity"],
                    x["lowest_liquidity_week"], "PASS" if x["all_covenants_pass"] else "FAIL",
                    x["first_breach_week"] or "-", ", ".join(map(str, x["fccr_trigger_weeks"])) or "-",
                    x["breach_detail"]]
            fmts = [None, MONEY, MONEY, INT, MONEY, MONEY, INT, MONEY, INT, None, None, None, None]
            for i, (vv, fm) in enumerate(zip(vals, fmts)):
                self.put(ws, r, 1 + i, vv, fm, font=F_BOLD if (i == 9 and vv == "FAIL") else None)
            r += 1

    def summary_sheet(self):
        ws = self.ws["Summary"]
        self.title(ws, "Weekly Lender Reporting Package - Summary & Certification")
        ws.column_dimensions["A"].width = 52
        ws.column_dimensions["B"].width = 20
        ws.column_dimensions["C"].width = 60
        T = self.ref
        cf_end = self.wrow["ending_cash"]
        rng = lambda name: f"'{self.wrow[name][0]}'!B{self.wrow[name][1]}:{L(1 + fc.WEEKS)}{self.wrow[name][1]}"  # noqa: E731
        r = 5
        items = [
            ("SECTION", "Borrowing base (as of certificate date)"),
            ("Gross A/R", f"={T('bbc.gross')}", MONEY, "BBC"),
            ("Eligible A/R", f"={T('bbc.eligible')}", MONEY, "BBC"),
            ("Borrowing base", f"={T('bbc.bb')}", MONEY, "BBC: eligible x advance rate, less reserves"),
            ("Revolver outstanding", f"={T('t.opening_loan')}", MONEY, "Terms"),
            ("Availability / (over-advance)", f"={T('bbc.availability')}", MONEY, "BBC"),
            ("SECTION", "13-week outlook"),
            ("Opening cash", f"={T('a.opening_cash')}", MONEY, "Bank balances"),
            ("Total operating receipts", f"={T('total_receipts.total')}", MONEY, "13-Week CF"),
            ("Total operating disbursements", f"={T('total_disbursements.total')}", MONEY, "13-Week CF"),
            ("Ending cash (week 13)", f"={T('ending_cash.total')}", MONEY, "13-Week CF"),
            ("Lowest weekly ending cash", f"=MIN({rng('ending_cash')})", MONEY, "13-Week CF"),
            ("Peak revolver balance", f"=MAX({rng('loan_end')})", MONEY, "Revolver"),
            ("Lowest availability", f"=MIN({rng('availability')})", MONEY, "Revolver"),
            ("Lowest liquidity", f"=MIN({rng('liquidity')})", MONEY, "Revolver"),
            ("SECTION", "Compliance"),
            ("Weeks failing a financial covenant", f"={T('cv.fail_weeks')}", INT, "Covenants"),
            ("Covenant status, all 13 weeks", f'=IF({T("cv.fail_weeks")}=0,"PASS","FAIL")', None, "Covenants"),
            ("Weeks with springing FCCR triggered", f"={T('cv.fccr_weeks')}", INT, "Covenants (informational)"),
            ("Permitted variance - receipts", f"={T('var.rec_status')}", None, "Variance"),
            ("Permitted variance - disbursements", f"={T('var.dis_status')}", None, "Variance"),
            ("SECTION", "Integrity checks (all should be 0)"),
            ("A/R aging buckets less gross A/R", f"=ROUND({T('bbc.aging_total')}-{T('bbc.gross')},2)", MONEY2, "BBC"),
            ("Opening cash less week-1 beginning cash",
             f"=ROUND({T('a.opening_cash')}-'13-Week CF'!B{self.wrow['beginning_cash'][1]},2)", MONEY2, "13-Week CF"),
            ("Weeks with an over-advance", f'=COUNTIF({rng("cv_oa_st")},"FAIL")', INT, "Covenants"),
        ]
        for it in items:
            if it[0] == "SECTION":
                r += 1
                self.section(ws, r, it[1], 3)
                r += 1
                continue
            label, f, fmt, src = it
            key = label in ("Covenant status, all 13 weeks", "Availability / (over-advance)", "Lowest liquidity")
            self.put(ws, r, 1, label, font=F_BOLD if key else None, fill=FILL_KEY if key else None)
            self.put(ws, r, 2, f, fmt, font=F_BOLD if key else None, fill=FILL_KEY if key else None,
                     align=Alignment(horizontal="right"))
            self.put(ws, r, 3, src, font=F_SUB)
            self.reg[f"sum.{label}"] = f"Summary!$B${r}"
            r += 1
        r += 1
        cert = ("Certification: The undersigned, a duly authorized officer of the Borrower, certifies that the "
                "accompanying 13-week cash flow forecast, borrowing base certificate and variance report were prepared "
                "in good faith on the basis of assumptions believed to be reasonable when made, and that, except as "
                "disclosed herein, no Default or Event of Default has occurred and is continuing.")
        self.put(ws, r, 1, cert, align=Alignment(wrap_text=True, vertical="top"))
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        ws.row_dimensions[r].height = 60
        r += 2
        for lab in ("Signature: ______________________________", "Name / Title: ___________________________",
                    "Date: ___________________"):
            self.put(ws, r, 1, lab)
            r += 1

    def build(self, scenarios=None) -> Workbook:
        self.terms_sheet()
        self.ar_sheet()
        self.bbc_sheet()
        self.cf_sheet()
        self.revolver_sheet()
        self.cf_financing()
        self.covenant_sheet()
        self.variance_sheet()
        self.ap_sheet()
        self.sensitivity_sheet(scenarios)
        self.summary_sheet()
        self.wb.active = 0
        return self.wb


def export(company: Company, scenario: fc.Scenario | None = None, path: Path | None = None,
           scenarios: dict[str, fc.Scenario] | None = None):
    """Build the package. Returns (workbook, registry, builder). Saves to `path` if given."""
    b = _Builder(company, scenario or fc.presets(company)["base"])
    wb = b.build(scenarios)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)
    return wb, b.reg, b


def to_bytes(company: Company, scenario: fc.Scenario | None = None,
             scenarios: dict[str, fc.Scenario] | None = None) -> bytes:
    wb, _, _ = export(company, scenario, scenarios=scenarios)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
