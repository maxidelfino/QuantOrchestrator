# Trigger Routing

Use this file to map natural-language user intent to the right command, subagent and skill set.

## Top-Level Intent Triggers

| User intent / phrasing | Implicit command | Route to | Prioritized skills |
|---|---|---|---|
| "quiero crear un bot", "armemos un bot", "build a bot", "new bot" | `/bot-new <name-or-idea>` | `sdd-orchestrator` after initial framing; may also involve `strategy-architect`, `execution-engineer`, `risk-engineer` | `brainstorming`, `algorithmic-trading`, `trading-strategies`, `trading-expert` |
| "quiero una estrategia", "evaluá esta idea", "strategy idea" | `/strategy <idea>` | `strategy-architect` | `brainstorming`, `algorithmic-trading`, `trading-strategies`, `trading-expert` |
| "probemos esta estrategia", "hacé un backtest", "backtest this" | `/backtest <strategy>` | `backtesting-engineer` | `backtesting-trading-strategies`, `algorithmic-trading` |
| "cómo ejecutamos en Binance/Bybit/Hyperliquid", "execution", "adapter" | `/execution <venue>` | `execution-engineer` | `algorithmic-trading`, `crypto-trading-bots`, `trading-futures` |
| "hay arbitraje?", "arb", "spread entre venues" | `/arb <pair/venue>` | `market-structure-researcher` | `crypto-trading-bots`, `algorithmic-trading`, `trading-expert` |
| "quiero hacer MEV", "sniping", "sandwich protection" | `/mev <idea>` | `market-structure-researcher` | `crypto-trading-bots`, `algorithmic-trading`, `trading-expert` |
| "Polymarket", "Kalshi", "prediction market" | `/prediction <market>` | `prediction-market-quant` | `algorithmic-trading`, `trading-strategies`, `trading-expert` |
| "cómo manejamos el riesgo", "position sizing", "risk" | `/risk <strategy>` | `risk-engineer` | `risk-management`, `algorithmic-trading` |
| "review", "juzgalo", "judgment day", "audit this code" | `/judge <scope>` | `judgment-day` | `judgment-day` |

## Routing Rule

If the user does NOT type the slash command explicitly, but the intent is clear, treat it as the corresponding command internally and say so in the orchestration summary.

Example:

- User: "Quiero crear un bot market maker en SOL"
- Internal interpretation: `/bot-new market-maker-sol`

## Subagent Reminder Rule

When delegating, include a short reminder when relevant:

- "Este pedido equivale a `/strategy`"
- "Este pedido equivale a `/backtest`"
- "Priorizá estas skills: ..."

This keeps routing explicit without forcing the user to memorize commands.
