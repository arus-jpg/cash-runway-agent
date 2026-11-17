from runway import data, forecast as fc, agent


def co():
    return data.generate()


def test_cash_roll_forward_ties():
    c = co()
    rows = fc.build(c)["rows"]
    assert rows[0]["beginning_cash"] == round(c.opening_cash)
    for prev, cur in zip(rows, rows[1:]):
        assert cur["beginning_cash"] == prev["ending_cash"]
    for r in rows:
        out = r["payroll"] + r["ap"] + r["opex"] + r["other_out"]
        assert abs(r["beginning_cash"] + r["receipts"] - out - r["ending_cash"]) <= 3  # rounding


def test_slower_collections_lowers_cash():
    c = co()
    base = fc.build(c)["summary"]["ending_cash_week13"]
    slow = fc.build(c, fc.PRESETS["slow_collections"])["summary"]["ending_cash_week13"]
    assert slow < base


def test_hiring_and_cost_cut_directions():
    c = co()
    base = fc.build(c)["summary"]["ending_cash_week13"]
    assert fc.build(c, fc.PRESETS["hiring_plan"])["summary"]["ending_cash_week13"] < base
    assert fc.build(c, fc.PRESETS["cost_cut_10"])["summary"]["ending_cash_week13"] > base


def test_downside_worst_of_all():
    res = {r["scenario"]: r["ending_cash_week13"] for r in fc.compare(co(), list(fc.PRESETS))}
    assert res["downside"] == min(res.values())


def test_one_time_outflow_hits_week():
    c = co()
    sc = fc.Scenario(one_time_items=[(5, -1_000_000, "tax payment")])
    base = fc.build(c)["rows"][5]["ending_cash"]
    assert fc.build(c, sc)["rows"][5]["ending_cash"] == base - 1_000_000


def test_csv_roundtrip(tmp_path):
    c = co()
    data.export_csvs(c, tmp_path)
    loaded = data.load_csvs(tmp_path)
    assert fc.build(c)["summary"]["ending_cash_week13"] == fc.build(loaded)["summary"]["ending_cash_week13"] \
        or abs(fc.build(c)["summary"]["ending_cash_week13"] - fc.build(loaded)["summary"]["ending_cash_week13"]) < 50_000
    assert len(loaded.invoices) == len(c.invoices) and len(loaded.bills) == len(c.bills)


def test_calibration_recovers_structure():
    c = co()
    cal = fc.calibrate(c, data.generate_history())
    assert cal.n_invoices > 100 and cal.n_weeks > 20
    assert cal.p90_days_late > cal.median_days_late      # right-skewed lateness
    assert cal.corr_billings_lateness < -0.3             # soft weeks pay slower
    assert 0.8 <= cal.realization_factor <= 1.25         # guard-railed
    assert cal.billings_cv > 0


def test_simulate_band_ordered_and_seeded():
    c = co()
    cal = fc.calibrate(c, data.generate_history())
    a = fc.simulate(c, calibration=cal, n=500, seed=1)
    b = fc.simulate(c, calibration=cal, n=500, seed=1)
    assert a == b                                         # seeded -> reproducible
    for i in range(fc.WEEKS):                             # P10 <= P50 <= P90 every week
        assert a["p10"][i] <= a["p50"][i] <= a["p90"][i]
    assert 0.0 <= a["prob_breach_policy"] <= 1.0
    # downside must be at least as likely to breach as base
    base = fc.simulate(c, fc.PRESETS["base"], calibration=cal, n=500, seed=1)
    down = fc.simulate(c, fc.PRESETS["downside"], calibration=cal, n=500, seed=1)
    assert down["prob_breach_policy"] >= base["prob_breach_policy"]


def test_history_csv_roundtrip(tmp_path):
    data.export_csvs(co(), tmp_path)
    h = data.load_history(tmp_path)
    assert h is not None and len(h.collections) > 100 and len(h.billings) > 20
    assert data.load_history(tmp_path / "nope") is None   # absent -> None, simulate falls back


def test_agent_tool_dispatch_no_api_needed():
    c = co()
    out = agent.execute_tool(c, "run_scenario", {"collections_delay_days": 30, "new_hires": 5})
    assert "rows" not in out and out["summary"]["lowest_cash"] is not None
    assert len(agent.execute_tool(c, "ar_risk", {})["open_ar"]) == 10
    assert agent.execute_tool(c, "compare_presets", {"names": ["base", "downside"]})["comparison"]
