"""Lender package review app.

    .venv/bin/streamlit run app.py

Review the 13-week cash flow, borrowing base, revolver, covenants and variance;
flex the scenario and the facility terms; then export the Excel lender package.
The export is tied out cell-by-cell against the engine before it can be downloaded.
"""
from __future__ import annotations

import tempfile
from dataclasses import fields, replace
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from runway import borrowing_base as bb
from runway import data, excel_export as xe, forecast as fc, tieout, variance

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Lender 13-Week Package", page_icon=":bank:", layout="wide")

# reference categorical palette (dataviz skill), slots in fixed order
BLUE, ORANGE, MUTED = "#2a78d6", "#eb6834", "#898781"


# ---------------- formatting ----------------
def money(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, str):
        return v
    if abs(v) < 0.5:
        return "-"
    return f"({abs(v):,.0f})" if v < 0 else f"{v:,.0f}"


def pct(v) -> str:
    return "" if v is None or v == "" else f"{v:.1%}"


def status_style(v):
    if v in ("FAIL", "TRIGGERED"):
        return "color: #d03b3b; font-weight: 600"
    if v == "PASS":
        return "color: #0ca30c; font-weight: 600"
    return ""


def week_cols(rows):
    return [f"Wk {r['week']} ({date.fromisoformat(r['week_start']):%m/%d})" for r in rows]


def weekly_table(rows, spec, total=True) -> pd.DataFrame:
    """spec: list of (label, fn(row) -> value, total_kind) ; total_kind in sum|first|last|min|None."""
    cols = week_cols(rows)
    data_rows, idx = [], []
    for label, fn, tk in spec:
        vals = [fn(r) for r in rows]
        if total:
            agg = {"sum": sum, "first": lambda v: v[0], "last": lambda v: v[-1], "min": min, None: lambda v: None}
            vals.append(agg[tk](vals) if all(isinstance(x, (int, float)) for x in vals) else None)
        data_rows.append(vals)
        idx.append(label)
    return pd.DataFrame(data_rows, index=idx, columns=cols + (["13-Wk Total"] if total else []))


def show(df: pd.DataFrame, fmt=money, height=None, bold_rows=()):
    # columns mixing numbers and text (e.g. covenant PASS/FAIL rows) go to Arrow as display strings
    if callable(fmt) and any(df[c].dropna().map(type).nunique() > 1 for c in df.columns):
        df, fmt = df.map(lambda v: "" if v is None else fmt(v)), (lambda v: v)
    sty = df.style.format(fmt).map(status_style)
    if bold_rows:
        sty = sty.apply(lambda r: ["font-weight: 700" if r.name in bold_rows else "" for _ in r], axis=1)
    st.dataframe(sty, width="stretch", **({"height": height} if height else {}))


# ---------------- inputs ----------------
st.sidebar.title("Lender package")
data_dir = Path(st.sidebar.text_input("Data folder", str(ROOT / "data" / "sample")))
try:
    company0 = data.load_csvs(data_dir)
except data.InputError as e:
    st.error(f"Input problem: {e}")
    st.stop()

presets = fc.presets(company0)
labels = {k: v.label for k, v in presets.items()}


def _reset_levers():
    p = presets[st.session_state.preset]
    st.session_state.delay = p.collections_delay_days
    st.session_state.billings = int(round(p.billings_change_pct * 100))
    st.session_state.opex = int(round(p.opex_change_pct * 100))
    st.session_state.baddebt = float(p.bad_debt_multiplier)


if "preset" not in st.session_state:
    st.session_state.preset = "base"
    _reset_levers()

st.sidebar.selectbox("Scenario", list(presets), format_func=labels.get, key="preset", on_change=_reset_levers)
with st.sidebar.expander("Scenario levers", expanded=False):
    st.slider("Collections delay (days)", 0, 60, key="delay")
    st.slider("Billings change (%)", -50, 30, key="billings")
    st.slider("Recurring opex & vendor run-rate change (%)", -30, 30, key="opex")
    st.slider("Bad-debt multiplier", 0.0, 4.0, step=0.25, key="baddebt")
    st.caption("One-time items (week 1-13; negative = outflow)")
    one_time = st.data_editor(pd.DataFrame({"week": pd.Series([], dtype="int"), "amount": pd.Series([], dtype="float"),
                                            "label": pd.Series([], dtype="str")}),
                              num_rows="dynamic", key="one_time", width="stretch")

base_sc = presets[st.session_state.preset]
items = [(int(r.week), float(r.amount), str(r.label or "")) for r in one_time.itertuples()
         if pd.notna(r.week) and pd.notna(r.amount)]
sc = replace(base_sc, collections_delay_days=st.session_state.delay, billings_change_pct=st.session_state.billings / 100,
             opex_change_pct=st.session_state.opex / 100, bad_debt_multiplier=st.session_state.baddebt,
             one_time_items=items)
if (sc.collections_delay_days, round(sc.billings_change_pct, 4), sc.opex_change_pct, sc.bad_debt_multiplier, items) != (
        base_sc.collections_delay_days, round(base_sc.billings_change_pct, 4), base_sc.opex_change_pct,
        base_sc.bad_debt_multiplier, []):
    sc = replace(sc, name=f"{base_sc.name}_adjusted", label=f"{base_sc.label} - adjusted")

with st.sidebar.expander("Facility terms", expanded=False):
    st.caption("Edits apply to this session. Save writes them to facility_terms.csv.")
    tdf = pd.DataFrame([{"Term": f.name, "Value": str(getattr(company0.terms, f.name))}
                        for f in fields(data.FacilityTerms)])
    edited = st.data_editor(tdf, disabled=["Term"], hide_index=True, key="terms", width="stretch", height=420)
    try:
        kw = {f.name: data._coerce(f, edited.loc[edited.Term == f.name, "Value"].iloc[0]) for f in fields(data.FacilityTerms)}
        terms = data.FacilityTerms(**kw)
    except (ValueError, IndexError) as e:
        st.error(f"Bad term value: {e}")
        terms = company0.terms
    if st.button("Save terms to CSV", disabled=terms == company0.terms):
        data.save_terms(terms, data_dir)
        st.success("Saved.")

company = replace(company0, terms=terms)
res = fc.build(company, sc)
rows, s, bbc = res["rows"], res["summary"], res["bbc"]
var = variance.report(company)

# ---------------- header ----------------
st.title(f"{company.name} - 13-Week Cash Flow & Borrowing Base")
st.caption(f"Prepared for **{terms.lender_name}** · As of **{date.fromisoformat(company.as_of):%m/%d/%Y}** · "
           f"Scenario: **{sc.label}** · Data: `{data_dir}`")
if terms.is_draft:
    st.warning(f"Facility status is **{terms.facility_status}**: these are illustrative terms, not an executed "
               "credit agreement. The export carries a DRAFT banner until facility_status = EXECUTED.")

k = st.columns(5)
k[0].metric("Availability today", money(bbc.availability), help="Borrowing base certificate as of the as-of date")
k[1].metric(f"Lowest liquidity (wk {s['lowest_liquidity_week']})", money(s["lowest_liquidity"]),
            help=f"Cash + availability. Covenant minimum {money(terms.min_liquidity)}")
k[2].metric("Peak revolver", money(s["peak_loan"]), help=f"Commitment {money(terms.commitment)}")
k[3].metric("Ending cash (wk 13)", money(s["ending_cash"]))
k[4].metric("Covenants", "PASS" if s["all_covenants_pass"] else f"FAIL wk {s['first_breach_week']}",
            delta=None if s["all_covenants_pass"] else f"{len({w for w, _ in s['breaches']})} weeks failing",
            delta_color="inverse")

tabs = st.tabs(["Summary", "13-Week Cash Flow", "Borrowing Base", "Revolver", "Covenants", "Variance",
                "Sensitivities", "Checks & Export"])

# ---------------- Summary ----------------
with tabs[0]:
    st.subheader("Cash and liquidity against the covenant floor")
    cdf = pd.DataFrame([{"Week": r["week"], "Week of": date.fromisoformat(r["week_start"]).strftime("%m/%d"),
                         "Series": name, "Amount": r[key]}
                        for r in rows for name, key in (("Ending cash", "ending_cash"), ("Liquidity", "liquidity"))])
    base = alt.Chart(cdf).encode(
        x=alt.X("Week:O", axis=alt.Axis(labelAngle=0, title="Week")),
        y=alt.Y("Amount:Q", axis=alt.Axis(format="$,.2s", title=None, gridDash=[0]), scale=alt.Scale(zero=False)),
        color=alt.Color("Series:N", scale=alt.Scale(domain=["Ending cash", "Liquidity"], range=[BLUE, ORANGE]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["Week", "Week of", "Series", alt.Tooltip("Amount:Q", format="$,.0f")])
    lines = base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60, filled=True))
    floor = alt.Chart(pd.DataFrame({"y": [terms.min_liquidity], "label": [f"Min liquidity {money(terms.min_liquidity)}"]}))
    rule = floor.mark_rule(color=MUTED, strokeWidth=1).encode(y="y:Q")
    rule_lbl = floor.mark_text(align="left", dx=4, dy=-6, color=MUTED, fontSize=11).encode(
        y="y:Q", x=alt.value(0), text="label:N")
    st.altair_chart((lines + rule + rule_lbl).properties(height=320), use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**13-week totals**")
        show(pd.DataFrame({"Amount": [s["opening_cash"], s["total_receipts"], -s["total_disbursements"],
                                       -s["interest_and_fees"], s["total_draws"], -s["total_repayments"],
                                       s["ending_cash"]]},
                          index=["Opening cash", "Operating receipts", "Operating disbursements", "Interest & fees",
                                 "Revolver draws", "Revolver repayments", "Ending cash"]))
    with c2:
        st.markdown("**Compliance**")
        comp = [("Financial covenants (13 weeks)", "PASS" if s["all_covenants_pass"] else "FAIL")]
        comp += [(f"  wk {w}: {c}", "FAIL") for w, c in s["breaches"][:6]]
        comp.append(("Springing FCCR triggered", ", ".join(f"wk {w}" for w in s["fccr_trigger_weeks"]) or "No"))
        for t in var.get("tests", []):
            comp.append((f"Permitted variance - {t['test'].split(' ')[0].lower()}", "PASS" if t["pass"] else "FAIL"))
        show(pd.DataFrame({"Result": [c[1] for c in comp]}, index=[c[0] for c in comp]))

# ---------------- 13-week cash flow ----------------
with tabs[1]:
    spec = [(lab, (lambda r, k=k: r[k]), "sum") for k, lab in fc.RECEIPT_LINES]
    spec += [("Total operating receipts", lambda r: r["total_receipts"], "sum")]
    spec += [(lab, (lambda r, k=k: -r[k]), "sum") for k, lab in fc.DISB_LINES]
    spec += [("Total operating disbursements", lambda r: -r["total_disbursements"], "sum"),
             ("Net operating cash flow", lambda r: r["net_operating_cf"], "sum"),
             ("Non-operating / one-time", lambda r: r["one_time"], "sum"),
             ("Interest paid", lambda r: -r["interest_paid"], "sum"),
             ("Unused line fee paid", lambda r: -r["unused_fee_paid"], "sum"),
             ("Net cash flow before revolver", lambda r: r["net_cf_before_revolver"], "sum"),
             ("Beginning cash", lambda r: r["beginning_cash"], "first"),
             ("Revolver draws", lambda r: r["draws"], "sum"),
             ("Revolver repayments", lambda r: -r["repayments"], "sum"),
             ("Ending cash", lambda r: r["ending_cash"], "last"),
             ("Memo: revolver balance", lambda r: r["loan_ending"], "last"),
             ("Memo: availability", lambda r: r["availability"], "min"),
             ("Memo: liquidity", lambda r: r["liquidity"], "min")]
    show(weekly_table(rows, spec), height=820,
         bold_rows=("Total operating receipts", "Total operating disbursements", "Net operating cash flow",
                    "Net cash flow before revolver", "Ending cash"))
    st.caption("Disbursements shown negative. Totals column: ending balance for balances, minimum for availability "
               "and liquidity. Past-due A/R beyond its expected pay date is spread over weeks 1-4.")

# ---------------- Borrowing base ----------------
with tabs[2]:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("Certificate")
        wf = [("Gross A/R", bbc.gross_ar)] + [(f"  less {k}", -v) for k, v in bbc.ineligibles.items()] + [
            ("Eligible A/R", bbc.eligible_ar), (f"x advance rate {terms.advance_rate:.0%}", bbc.gross_availability),
            ("less availability block", -terms.availability_block), ("Borrowing base", bbc.borrowing_base),
            ("Commitment", terms.commitment), ("Maximum borrowing", bbc.max_borrowing),
            ("less revolver outstanding", -bbc.loan), ("Availability / (over-advance)", bbc.availability)]
        show(pd.DataFrame({"Amount": [v for _, v in wf]}, index=[k for k, _ in wf]), height=560,
             bold_rows=("Gross A/R", "Eligible A/R", "Borrowing base", "Availability / (over-advance)"))
        st.caption(f"Eligible = {bbc.eligible_pct:.1%} of gross A/R; the forecast holds this % to project the "
                   "borrowing base.")
    with c2:
        st.subheader("Ineligibles by reason")
        idf = pd.DataFrame([{"Reason": k, "Amount": v, "Order": i, "Why": bb.CATEGORY_HELP[k]}
                            for i, (k, v) in enumerate(bbc.ineligibles.items())])
        bar = alt.Chart(idf).mark_bar(color=BLUE, size=18, cornerRadiusEnd=4).encode(
            y=alt.Y("Reason:N", sort=alt.SortField("Order"), title=None),
            x=alt.X("Amount:Q", axis=alt.Axis(format="$,.2s", title=None, gridDash=[0]),
                    scale=alt.Scale(domain=[0, max(1.0, idf.Amount.max()) * 1.25])),  # room for value labels
            tooltip=["Reason", alt.Tooltip("Amount:Q", format="$,.0f"), "Why"])
        txt = bar.mark_text(align="left", dx=4, fontSize=11).encode(text=alt.Text("Amount:Q", format="$,.0f"),
                                                                    color=alt.value("#52514e"))
        st.altair_chart((bar + txt).properties(height=260), use_container_width=True)
        st.subheader("Aging")
        ag = bbc.aging_summary()
        show(pd.DataFrame({"Amount": list(ag.values()),
                           "% of total": [v / bbc.gross_ar for v in ag.values()]}, index=list(ag)),
             fmt={"Amount": money, "% of total": pct})

    st.subheader("Invoice detail")
    inv = pd.DataFrame(bbc.invoices)
    reasons = ["All", "Eligible"] + [k for k in bb.INELIGIBLE_CATEGORIES if k != bb.CONCENTRATION]
    pick = st.selectbox("Filter", reasons)
    if pick == "Eligible":
        inv = inv[inv.reason == ""]
    elif pick != "All":
        inv = inv[inv.reason == pick]
    inv = inv[["customer", "customer_type", "num", "invoice_date", "due_date", "amount", "age", "bucket", "reason",
               "ineligible", "eligible_pre_conc"]].rename(columns={
                   "customer": "Customer", "customer_type": "Type", "num": "Invoice", "invoice_date": "Invoice date",
                   "due_date": "Due", "amount": "Amount", "age": "Days", "bucket": "Bucket", "reason": "Ineligible reason",
                   "ineligible": "Ineligible", "eligible_pre_conc": "Eligible pre-conc."})
    st.dataframe(inv.style.format({"Amount": money, "Ineligible": money, "Eligible pre-conc.": money}),
                 width="stretch", hide_index=True)
    st.subheader("Concentration")
    cdf2 = pd.DataFrame(bbc.concentration).rename(columns={
        "customer": "Customer", "customer_type": "Type", "gross": "Gross A/R", "eligible_pre_conc": "Eligible pre-conc.",
        "share": "Share", "limit_pct": "Limit", "limit_amount": "Limit $", "excess": "Excess (ineligible)"})
    st.dataframe(cdf2.style.format({"Gross A/R": money, "Eligible pre-conc.": money, "Share": pct, "Limit": pct,
                                    "Limit $": money, "Excess (ineligible)": money}),
                 width="stretch", hide_index=True)

# ---------------- Revolver ----------------
with tabs[3]:
    rdf = pd.DataFrame([{"Week": r["week"], "Series": n, "Amount": r[k]}
                        for r in rows for n, k in (("Maximum borrowing", "max_borrowing"), ("Revolver balance", "loan_ending"))])
    ch = alt.Chart(rdf).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60, filled=True)).encode(
        x=alt.X("Week:O", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("Amount:Q", axis=alt.Axis(format="$,.2s", title=None, gridDash=[0])),
        color=alt.Color("Series:N", scale=alt.Scale(domain=["Maximum borrowing", "Revolver balance"], range=[BLUE, ORANGE]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["Week", "Series", alt.Tooltip("Amount:Q", format="$,.0f")])
    st.altair_chart(ch.properties(height=280), use_container_width=True)
    show(weekly_table(rows, [
        ("Beginning A/R", lambda r: r["ar_beginning"], "first"), ("+ Billings", lambda r: r["billings"], "sum"),
        ("- Collections", lambda r: -r["collections"], "sum"), ("- Write-offs", lambda r: -r["write_offs"], "sum"),
        ("Ending A/R", lambda r: r["ar_ending"], "last"), ("Projected eligible A/R", lambda r: r["eligible_ar"], "last"),
        ("Borrowing base", lambda r: r["borrowing_base"], "last"),
        ("Maximum borrowing", lambda r: r["max_borrowing"], "last"),
        ("Revolver - beginning", lambda r: r["loan_beginning"], "first"), ("Draws", lambda r: r["draws"], "sum"),
        ("Repayments", lambda r: -r["repayments"], "sum"), ("Revolver - ending", lambda r: r["loan_ending"], "last"),
        ("Interest paid", lambda r: -r["interest_paid"], "sum"), ("Unused fee paid", lambda r: -r["unused_fee_paid"], "sum"),
        ("Availability", lambda r: r["availability"], "min"), ("Liquidity", lambda r: r["liquidity"], "min")]),
        height=600, bold_rows=("Ending A/R", "Maximum borrowing", "Revolver - ending", "Liquidity"))
    st.caption(f"Draws hold minimum operating cash of {money(company.min_cash_policy)}; cash above "
               f"{money(company.min_cash_policy + company.sweep_buffer)} sweeps to repay. Interest at "
               f"SOFR {terms.sofr:.2%} + {terms.margin:.2%} and the {terms.unused_fee:.3%} unused fee accrue on the "
               "beginning balance and are paid in the week containing the 1st.")

# ---------------- Covenants ----------------
with tabs[4]:
    cov = lambda k, f: (lambda r: r["covenants"][k][f])  # noqa: E731
    pf = lambda b: "PASS" if b else "FAIL"  # noqa: E731
    show(weekly_table(rows, [
        ("Liquidity", cov("min_liquidity", "actual"), None), ("  minimum liquidity required", cov("min_liquidity", "required"), None),
        ("  liquidity cushion / (shortfall)", cov("min_liquidity", "cushion"), None),
        ("  Minimum Liquidity test", lambda r: pf(r["covenants"]["min_liquidity"]["pass"]), None),
        ("Availability", cov("min_excess_availability", "actual"), None),
        ("  minimum availability required", cov("min_excess_availability", "required"), None),
        ("  availability cushion / (shortfall)", cov("min_excess_availability", "cushion"), None),
        ("  Minimum Excess Availability test", lambda r: pf(r["covenants"]["min_excess_availability"]["pass"]), None),
        ("No over-advance", lambda r: pf(r["covenants"]["no_overadvance"]["pass"]), None),
        ("Cash positive", lambda r: pf(r["covenants"]["cash_positive"]["pass"]), None),
        ("Springing FCCR", lambda r: "TRIGGERED" if r["covenants"]["fccr_springing"]["triggered"] else "No", None),
    ], total=False), height=430)
    st.caption(f"Springing FCCR applies when availability < {terms.fccr_trigger_pct:.1%} of the commitment "
               f"({money(terms.fccr_trigger_pct * terms.commitment)}); it is a trigger, not a breach, and the FCCR "
               "itself needs trailing EBITDA outside this model.")

# ---------------- Variance ----------------
with tabs[5]:
    if not var["available"]:
        st.info("No variance_history.csv in the data folder.")
    else:
        st.caption(f"Prior week ending {var['prior_week']} · rolling window {var['window'][0]} to {var['window'][-1]} · "
                   "favorable = more receipts / fewer disbursements than forecast")
        vdf = pd.DataFrame([{"Line": x["label"], "Prior fcst": x["prior_forecast"], "Prior actual": x["prior_actual"],
                             "Fav/(unfav)": x["prior_fav"], "Prior %": x["prior_pct"], "Cum fcst": x["cum_forecast"],
                             "Cum actual": x["cum_actual"], "Cum fav/(unfav)": x["cum_fav"], "Cum %": x["cum_pct"]}
                            for x in var["lines"]]).set_index("Line")
        show(vdf, fmt={c: (pct if "%" in c else money) for c in vdf.columns}, height=420)
        tdf2 = pd.DataFrame([{"Test": t["test"], "Forecast": t["forecast"], "Actual": t["actual"], "Ratio": t["ratio"],
                              "Limit": t["limit"], "Status": "PASS" if t["pass"] else "FAIL"} for t in var["tests"]])
        st.dataframe(tdf2.style.format({"Forecast": money, "Actual": money, "Ratio": pct, "Limit": pct}).map(
            status_style, subset=["Status"]), width="stretch", hide_index=True)

# ---------------- Sensitivities ----------------
with tabs[6]:
    comp = fc.compare(company, presets)
    sdf = pd.DataFrame([{"Scenario": x["label"], "Ending cash": x["ending_cash"], "Lowest liquidity": x["lowest_liquidity"],
                         "Wk": x["lowest_liquidity_week"], "Peak revolver": x["peak_loan"],
                         "Lowest availability": x["lowest_availability"],
                         "Covenants": "PASS" if x["all_covenants_pass"] else "FAIL",
                         "First breach wk": str(x["first_breach_week"] or "-"),
                         "FCCR trigger wks": ", ".join(map(str, x["fccr_trigger_weeks"])) or "-",
                         "Detail": x["breach_detail"]} for x in comp])
    st.dataframe(sdf.style.format({c: money for c in ("Ending cash", "Lowest liquidity", "Peak revolver",
                                                       "Lowest availability")}).map(status_style, subset=["Covenants"]),
                 width="stretch", hide_index=True)
    st.caption("Presets use the facility terms above. Your scenario levers apply to the main views and the export.")

# ---------------- Checks & export ----------------
with tabs[7]:
    st.subheader("Engine checks")
    checks = []
    checks.append(("A/R aging buckets = gross A/R", abs(sum(bbc.aging_summary().values()) - bbc.gross_ar) < 0.01))
    checks.append(("BBC waterfall: gross - ineligibles = eligible",
                   abs(bbc.gross_ar - bbc.total_ineligible - bbc.eligible_ar) < 0.01))
    checks.append(("Week-1 beginning cash = bank balances", abs(rows[0]["beginning_cash"] - company.opening_cash) < 0.01))
    checks.append(("Cash rolls forward every week", all(abs(a["ending_cash"] - b["beginning_cash"]) < 0.01
                                                        for a, b in zip(rows, rows[1:]))))
    checks.append(("A/R rolls forward every week", all(abs(r["ar_beginning"] + r["billings"] - r["collections"]
                                                           - r["write_offs"] - r["ar_ending"]) < 0.01 for r in rows)))
    checks.append(("Draws never exceed availability", all(r["draws"] <= r["availability_before_draw"] + 0.01 for r in rows)))
    show(pd.DataFrame({"Result": ["PASS" if ok else "FAIL" for _, ok in checks]}, index=[c for c, _ in checks]))
    engine_ok = all(ok for _, ok in checks)

    st.subheader("Export")
    st.write("Builds the 10-tab workbook with live formulas, recalculates it independently, and ties every week's "
             "cash, revolver, borrowing base, availability, liquidity and covenant result back to the numbers above.")
    sig = (str(data_dir), sc, terms)
    if st.button("Build & verify lender package", type="primary", disabled=not engine_ok):
        with st.spinner("Building workbook and tying out formulas..."):
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "package.xlsx"
                _, _, builder = xe.export(company, sc, path=p, scenarios=presets)
                bad = tieout.tie(p, builder)
                st.session_state.pkg = {"sig": sig, "bytes": p.read_bytes(), "bad": bad}
    pkg = st.session_state.get("pkg")
    if pkg and pkg["sig"] == sig:
        if pkg["bad"]:
            st.error(f"Tie-out failed: {len(pkg['bad'])} differences. Download is disabled.")
            st.code("\n".join(pkg["bad"][:30]))
        else:
            st.success("Tie-out passed: workbook formulas agree with the engine in every week (0 differences).")
            fname = f"{date.fromisoformat(company.as_of):%Y-%m-%d} {company.name} Lender Package - {sc.label}.xlsx"
            st.download_button("Download lender package (.xlsx)", pkg["bytes"], file_name=fname.replace("/", "-"),
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    elif pkg:
        st.info("Inputs changed since the last build; rebuild to download.")
