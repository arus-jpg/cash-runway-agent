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


def test_agent_tool_dispatch_no_api_needed():
    c = co()
    out = agent.execute_tool(c, "run_scenario", {"collections_delay_days": 30, "new_hires": 5})
    assert "rows" not in out and out["summary"]["lowest_cash"] is not None
    assert len(agent.execute_tool(c, "ar_risk", {})["open_ar"]) == 10
    assert agent.execute_tool(c, "compare_presets", {"names": ["base", "downside"]})["comparison"]
