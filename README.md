# Cash & Runway Forecaster (CFO Agent)

A Claude-powered agent that answers CFO questions about cash using a driver-based 13-week forecast.
All data is **synthetic** (`runway/data.py`), so the repo is safe to share.

> "What happens to our cash if customers pay 30 days slower and we hire 10 people in week 4?"

The agent translates the question into scenario levers, calls the forecast engine as a tool, and
answers with the low point, runway, and any breach of the minimum cash policy. Numbers always come
from the tool, never from the model.

## Quick start

```bash
pip install -r requirements.txt
python -m runway.cli forecast                 # 13-week base case
python -m runway.cli compare                  # all preset scenarios side by side
python -m runway.cli ar                       # top collection risks
pytest                                        # forecast math tests (no API key needed)

export ANTHROPIC_API_KEY=sk-ant-...           # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
python -m runway.cli ask "Can we afford 12 hires in week 3 if collections slip 2 weeks?"
python -m runway.cli chat                     # multi-turn
```

## How it works

| File | Role |
|---|---|
| `runway/data.py` | Synthetic company: open AR, AP bills, payroll, recurring opex, billings run-rate |
| `runway/forecast.py` | Weekly cash engine + scenario levers (collections delay, billings %, opex %, hires, bad debt, one-time items) |
| `runway/agent.py` | Claude tool-use loop (`run_scenario`, `compare_presets`, `ar_risk`) with guardrails |
| `runway/cli.py` | CLI entry point |

## Demo script (hackathon)

1. `compare` - show downside breaching the $3.0M policy.
2. `ask "Which customers are the biggest collection risk and what is it worth?"`
3. `ask "We want to hire 12 people. What's the max we can hire without breaching min cash?"`
4. Swap synthetic data for a QuickBooks/Ramp export (next step: connect via MCP).

## Roadmap

- Pull live AR/AP/bank balances via QuickBooks and Ramp MCP connectors
- Monte Carlo on collection timing for a probability of breach
- Weekly scheduled run that posts the forecast to Slack
