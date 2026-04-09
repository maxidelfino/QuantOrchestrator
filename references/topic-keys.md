# Engram Topic Keys

## Recommended Families

- `strategy/{name}` — strategy rationale, variants, assumptions
- `backtest/{name}` — historical validation results and caveats
- `risk/{name}` — risk budgets, sizing, controls, limits
- `execution/{venue-or-bot}` — venue constraints, order lifecycle, routing decisions
- `market-structure/{topic}` — DEX, arb, MEV, sniping, microstructure research
- `prediction/{market}` — prediction market analysis and probabilistic assumptions
- `bot/{name}` — overall bot architecture, status and conventions
- `review/{scope}` — judgment-day summaries or other review outcomes
- `decision/{topic}` — architectural or workflow decisions

## SDD Keys

- `sdd-init/{project}`
- `sdd/{change-name}/explore`
- `sdd/{change-name}/proposal`
- `sdd/{change-name}/spec`
- `sdd/{change-name}/design`
- `sdd/{change-name}/tasks`
- `sdd/{change-name}/apply-progress`
- `sdd/{change-name}/verify-report`
- `sdd/{change-name}/archive-report`

## Rule of Thumb

If the knowledge should survive the current thread, give it a stable topic key.
