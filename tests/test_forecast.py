from dataclasses import replace
from pathlib import Path

import openpyxl
import pytest

from runway import borrowing_base as bb, data, excel_export as xe, forecast as fc, tieout, variance

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


@pytest.fixture
def co():
    return data.load_csvs(SAMPLE)


def inv(customer, num, age, amount, ctype="Commercial", as_of="2026-11-16"):
    from datetime import date, timedelta
    d = date.fromisoformat(as_of) - timedelta(days=age)
    return data.Invoice(customer, num, d.isoformat(), (d + timedelta(days=30)).isoformat(), amount, ctype, 0, 0.0)


# ---------------- cash flow & revolver ----------------

def test_cash_roll_forward_ties(co):
    rows = fc.build(co)["rows"]
    assert rows[0]["beginning_cash"] == co.opening_cash
    for prev, cur in zip(rows, rows[1:]):
        assert cur["beginning_cash"] == prev["ending_cash"]
        assert cur["loan_beginning"] == prev["loan_ending"]
    for r in rows:
        flows = (r["total_receipts"] - r["total_disbursements"] + r["one_time"] - r["interest_paid"]
                 - r["unused_fee_paid"] + r["draws"] - r["repayments"])
        assert r["beginning_cash"] + flows == pytest.approx(r["ending_cash"])


@pytest.mark.parametrize("name", ["base", "slow_collections", "billings_down_20", "lose_largest_customer", "downside"])
def test_revolver_never_exceeds_limits(co, name):
    for r in fc.build(co, fc.presets(co)[name])["rows"]:
        assert r["draws"] <= r["availability_before_draw"] + 1e-6
        assert r["loan_ending"] <= r["max_borrowing"] + 1e-6 or r["overadvance"] > 0
        assert r["max_borrowing"] <= co.terms.commitment
        assert r["loan_ending"] >= 0 and r["draws"] >= 0 and r["repayments"] >= 0


def test_draws_hold_min_cash_when_availability_allows(co):
    for r in fc.build(co, fc.presets(co)["downside"])["rows"]:
        if r["draws"] > 0 and r["draws"] < r["availability_before_draw"]:
            assert r["ending_cash"] == pytest.approx(co.min_cash_policy)


def test_ar_roll_forward(co):
    rows = fc.build(co)["rows"]
    assert rows[0]["ar_beginning"] == pytest.approx(bb.compute(co).gross_ar)
    for r in rows:
        assert r["ar_beginning"] + r["billings"] - r["collections"] - r["write_offs"] == pytest.approx(r["ar_ending"])


def test_interest_and_fee_arithmetic(co):
    t = co.terms
    rows = fc.build(co)["rows"]
    first_pay = next(r for r in rows if r["pay_flag"])
    # opening accrual + weekly accruals on beginning balances through the week before payment
    expected_int = t.opening_loan * t.rate * t.days_accrued / 360 + sum(
        r["loan_beginning"] * t.rate * 7 / 360 for r in rows[:first_pay["week"] - 1])
    expected_fee = max(0, t.commitment - t.opening_loan) * t.unused_fee * t.days_accrued / 360 + sum(
        max(0, t.commitment - r["loan_beginning"]) * t.unused_fee * 7 / 360 for r in rows[:first_pay["week"] - 1])
    assert first_pay["interest_paid"] == pytest.approx(expected_int)
    assert first_pay["unused_fee_paid"] == pytest.approx(expected_fee)
    assert sum(r["interest_paid"] for r in rows if not r["pay_flag"]) == 0


def test_scenario_directions(co):
    p = fc.presets(co)
    base = fc.build(co, p["base"])["summary"]
    for name in ("slow_collections", "billings_down_20", "lose_largest_customer", "downside"):
        s = fc.build(co, p[name])["summary"]
        assert s["lowest_liquidity"] < base["lowest_liquidity"], name
    res = {r["scenario"]: r["lowest_liquidity"] for r in fc.compare(co)}
    assert min(res, key=res.get) in ("downside", "lose_largest_customer")


def test_sample_calibration_story(co):
    """The sample terms are calibrated so base passes and the downside breaches; keep it that way."""
    p = fc.presets(co)
    assert fc.build(co, p["base"])["summary"]["all_covenants_pass"]
    assert not fc.build(co, p["downside"])["summary"]["all_covenants_pass"]


def test_one_time_outflow_hits_week(co):
    base = fc.build(co)["rows"][4]["cash_before_revolver"]
    sc = fc.Scenario(one_time_items=[(5, -250_000, "equipment")])
    assert fc.build(co, sc)["rows"][4]["cash_before_revolver"] == pytest.approx(base - 250_000)


# ---------------- borrowing base ----------------

def _company_with(co, invoices, **term_overrides):
    return replace(co, invoices=invoices, terms=replace(co.terms, **term_overrides))


def test_each_ineligible_rule(co):
    invs = [
        inv("Future Co", "F1", -3, 100),
        inv("Abroad", "X1", 10, 200, "Foreign"),
        inv("Sister", "S1", 10, 300, "Affiliate"),
        inv("Agency", "G1", 10, 400, "US Gov - Unassigned"),
        inv("Slow", "P1", 95, 500), inv("Slow", "P2", 20, 1500),        # 25% past 90 -> not cross-aged
        inv("Cross", "C1", 120, 600), inv("Cross", "C2", 10, 400),      # 60% past 90 -> cross-aged
        inv("Good", "OK1", 5, 1000),
    ]
    b = bb.compute(_company_with(co, invs, concentration_limit=1.0, gov_concentration_limit=1.0))
    reasons = {r["num"]: r["reason"] for r in b.invoices}
    assert reasons == {"F1": bb.FUTURE, "X1": bb.FOREIGN, "S1": bb.AFFILIATE, "G1": bb.GOV_NO_AOC,
                       "P1": bb.PAST_DUE, "P2": "", "C1": bb.PAST_DUE, "C2": bb.CROSS_AGED, "OK1": ""}
    assert b.ineligibles[bb.CROSS_AGED] == 400 and b.ineligibles[bb.PAST_DUE] == 1100
    assert b.gross_ar - b.total_ineligible == pytest.approx(b.eligible_ar) == 2500


def test_concentration_limit(co):
    invs = [inv("Big", "B1", 5, 6000), inv("A", "A1", 5, 2000), inv("B", "B2", 5, 2000),
            inv("Gov", "G1", 5, 4000, "US Gov - Assigned")]
    b = bb.compute(_company_with(co, invs, concentration_limit=0.25, gov_concentration_limit=0.50))
    pool = 14000
    by = {c["customer"]: c for c in b.concentration}
    assert by["Big"]["excess"] == pytest.approx(6000 - 0.25 * pool)
    assert by["Gov"]["excess"] == 0  # 28.6% < 50% government limit
    assert b.eligible_ar == pytest.approx(pool - (6000 - 0.25 * pool))


def test_bbc_waterfall_ties_to_aging(co):
    b = bb.compute(co)
    assert sum(b.aging_summary().values()) == pytest.approx(b.gross_ar)
    assert b.gross_ar - b.total_ineligible == pytest.approx(b.eligible_ar)
    assert b.borrowing_base == pytest.approx(b.eligible_ar * co.terms.advance_rate - co.terms.availability_block)


def test_unknown_customer_is_rejected(tmp_path, co):
    import shutil
    for f in SAMPLE.iterdir():
        shutil.copy(f, tmp_path / f.name)
    with open(tmp_path / "ar_aging_detail.csv", "a") as fh:
        fh.write("Mystery Corp,INV-9999,11/01/2026,12/01/2026,1000.00\n")
    with pytest.raises(data.InputError, match="Mystery Corp"):
        data.load_csvs(tmp_path)


# ---------------- variance ----------------

def test_variance_math(co):
    v = variance.report(co)
    coll = next(x for x in v["lines"] if x["line"] == "collections")
    assert coll["cum_forecast"] == sum(h.forecast for h in co.variance if h.line == "collections")
    assert coll["prior_fav"] == coll["prior_actual"] - coll["prior_forecast"]
    vend = next(x for x in v["lines"] if x["line"] == "vendors")
    assert vend["cum_fav"] == -(vend["cum_actual"] - vend["cum_forecast"])
    assert all(t["pass"] for t in v["tests"])


def test_variance_test_fails_when_receipts_short(co):
    short = [replace(h, actual=h.forecast * 0.5) if h.line == "collections" else h for h in co.variance]
    v = variance.report(replace(co, variance=short))
    assert not v["tests"][0]["pass"] and v["tests"][1]["pass"]


# ---------------- Excel package ----------------

@pytest.mark.parametrize("name", ["base", "downside"])
def test_workbook_ties_to_engine(co, tmp_path, name):
    p = tmp_path / f"{name}.xlsx"
    _, _, b = xe.export(co, fc.presets(co)[name], path=p)
    assert tieout.tie(p, b) == []


def test_workbook_is_live_negative_test(co, tmp_path):
    """Change an input in the saved file: the formulas must move, so the tie-out must break."""
    p = tmp_path / "pkg.xlsx"
    _, reg, b = xe.export(co, path=p)
    wb = openpyxl.load_workbook(p)
    wb["Terms"][reg["t.advance_rate"].split("!")[1].replace("$", "")] = 0.60
    p2 = tmp_path / "tampered.xlsx"
    wb.save(p2)
    bad = tieout.tie(p2, b)
    assert any("borrowing_base" in x for x in bad)
    assert any("liquidity" in x for x in bad)
