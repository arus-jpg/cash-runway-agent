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

## Dashboard (GitHub Pages)

A static 13-week runway dashboard lives in `docs/`:

- **KPI tiles** — opening/week-13/lowest cash, probability of breaching the policy, runway, policy status.
- **Ending-cash chart** with a **Monte Carlo P10–P90 confidence band** (2,000 runs over collection
  timing/realization and billings size) around the expected line, plus the min-cash policy threshold.
- **Burn & efficiency** — net vs gross monthly burn, runway to the policy floor and to $0, and
  **burn multiple** (net burn ÷ net-new ARR).
- **Weekly sources & uses** — stacked inflows/outflows with a net-flow overlay.
- Weekly cash-flow table, scenario comparison, and top AR collection risks.

It reads `docs/data.json`, so regenerating the forecast refreshes the page:

```bash
py scripts/build_dashboard.py                      # writes docs/data.json from data/sample
py scripts/build_dashboard.py --data-dir data/private   # or from your real export
python -m http.server -d docs 8000                 # preview at http://localhost:8000
```

Publish by enabling **GitHub Pages → Deploy from branch → `main` / `/docs`**. The page also
opens directly from disk (it embeds an inline-data fallback).

## Data: dummy QuickBooks CSVs

The demo reads QuickBooks-style CSVs from `data/sample/` (all dummy). To run on real data, export the
same reports from QuickBooks into `data/private/` (gitignored) and run
`python -m runway.cli --data-dir data/private forecast`.

| File | QuickBooks source |
|---|---|
| `ar_aging_detail.csv` | Reports > A/R Aging Detail |
| `ap_aging_detail.csv` | Reports > A/P Aging Detail |
| `bank_balances.csv` | Balance Sheet, bank accounts |
| `payroll_register.csv` | Payroll history (biweekly) |
| `recurring_expenses.csv`, `customer_terms.csv`, `assumptions.csv` | Finance team inputs |

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
