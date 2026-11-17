"""Claude-powered CFO agent: natural-language questions -> forecast tool calls."""
from __future__ import annotations

import json
import os

from . import forecast as fc
from .data import Company

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

SYSTEM = """You are a treasury & FP&A copilot for a CFO. You have a driver-based 13-week cash
forecast tool. Rules:
- Never invent numbers. Every figure must come from a tool result.
- Lead with the answer (cash low point, runway, policy breach), then the drivers, then a recommendation.
- Always say when a scenario breaches the minimum cash policy and in which week.
- Be concise: a CFO reads this between meetings. Use $ in millions with one decimal.
- State key assumptions (e.g., collections delay, hire cost) you applied for any custom scenario."""

TOOLS = [
    {
        "name": "run_scenario",
        "description": "Run the 13-week cash forecast with optional levers. Omit levers for the base case. "
                       "Returns weekly rows and a summary (lowest cash, runway, policy breach week).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Label for this scenario"},
                "collections_delay_days": {"type": "integer", "description": "Extra days customers take to pay"},
                "billings_change_pct": {"type": "number", "description": "Change in new billings, e.g. -0.2 = -20%"},
                "opex_change_pct": {"type": "number", "description": "Change in recurring opex, e.g. -0.1 = -10%"},
                "new_hires": {"type": "integer"},
                "hire_start_week": {"type": "integer"},
                "hire_monthly_cost": {"type": "number", "description": "Loaded monthly cost per hire (default 18000)"},
                "bad_debt_multiplier": {"type": "number", "description": "Scales non-collection risk, 2.0 = double"},
                "one_time_items": {
                    "type": "array",
                    "description": "One-time cash items. Negative amount = outflow.",
                    "items": {"type": "object", "properties": {
                        "week": {"type": "integer"}, "amount": {"type": "number"}, "label": {"type": "string"}},
                        "required": ["week", "amount"]},
                },
                "include_weekly_rows": {"type": "boolean", "description": "Include week-by-week rows (default false)"},
            },
        },
    },
    {
        "name": "compare_presets",
        "description": f"Compare preset scenarios side by side. Presets: {', '.join(fc.PRESETS)}.",
        "input_schema": {"type": "object", "properties": {
            "names": {"type": "array", "items": {"type": "string", "enum": list(fc.PRESETS)}}},
            "required": ["names"]},
    },
    {
        "name": "ar_risk",
        "description": "Open receivables ranked by expected loss; use for collections-risk questions.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def execute_tool(company: Company, name: str, args: dict) -> dict:
    if name == "run_scenario":
        sc = fc.Scenario(
            name=args.get("name", "custom"),
            collections_delay_days=args.get("collections_delay_days", 0),
            billings_change_pct=args.get("billings_change_pct", 0.0),
            opex_change_pct=args.get("opex_change_pct", 0.0),
            new_hires=args.get("new_hires", 0),
            hire_start_week=args.get("hire_start_week", 4),
            hire_monthly_cost=args.get("hire_monthly_cost", 18_000),
            bad_debt_multiplier=args.get("bad_debt_multiplier", 1.0),
            one_time_items=[(i["week"], i["amount"], i.get("label", "")) for i in args.get("one_time_items", [])],
        )
        res = fc.build(company, sc)
        if not args.get("include_weekly_rows"):
            res.pop("rows")
        return res
    if name == "compare_presets":
        return {"comparison": fc.compare(company, args["names"])}
    if name == "ar_risk":
        return {"open_ar": fc.ar_risk(company)[:10]}
    return {"error": f"unknown tool {name}"}


def ask(company: Company, question: str, history: list | None = None, verbose: bool = False) -> tuple[str, list]:
    """Run the tool-use loop. Returns (answer, updated message history)."""
    import anthropic  # lazy: forecast/CLI work without the SDK

    client = anthropic.Anthropic()
    messages = list(history or [])
    messages.append({"role": "user", "content": question})
    for _ in range(8):  # hard cap on tool rounds
        resp = client.messages.create(
            model=MODEL, max_tokens=1500, system=SYSTEM, tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text"), messages
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                if verbose:
                    print(f"  [tool] {block.name} {json.dumps(block.input)}")
                out = execute_tool(company, block.name, block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(out)})
        messages.append({"role": "user", "content": results})
    return "Stopped after too many tool rounds.", messages
