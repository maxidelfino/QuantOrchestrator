# Monorepo Restructure — Multi-Exchange, Polyglot Architecture

**Date:** 2026-05-14
**Author:** QuantOrchestrator + sdd-explore
**Status:** Approved

## Problem

QuantOrchestrator currently has:
- Python bots for Hyperliquid in `bots/shared/` (ambiguous naming)
- A TypeScript MM bot in a separate repo (`mm_bot_01/`)
- Exchange adapters monolithically embedded in `exchange.py` (421 lines)
- Backtest results scattered across 3 locations
- No clear path for adding new exchanges or languages

## Goal

Restructure into a monorepo where:
1. Each exchange has its own adapter directory under `exchanges/<lang>/`
2. Bot instances live under `bots/<lang>/` with a `core/` framework
3. New contributors can add exchanges or bots by copying templates
4. Backtest scripts and results are consolidated by venue

## Current Issues Found

| # | Issue | File | Fix |
|---|---|---|---|
| 1 | `exchange.py` monolithic (421 lines) | `bots/shared/exchange.py` | Split into venue.py + individual adapters |
| 2 | `risk.py` references non-existent `max_position_pct` | `bots/shared/risk.py` | Add field to RiskConfig or fix reference |
| 3 | Bot names hardcoded in config loader | `bots/shared/config.py` | Plugin registry or dynamic loading |
| 4 | Two `.env` conventions | root + mm_bot_01 | Namespace by exchange |
| 5 | Backtest results in 3 places | docs/, bots/*/backtests/, archive/ | Consolidate to `backtest-results/` |

## Target Structure

```
QuantOrchestrator/
├── .env / .env.example                    # Secrets only, namespaced per exchange
├── pyproject.toml / requirements.txt
├── package.json                           # NEW: workspace root for TS
│
├── exchanges/                             # NEW: adapters by exchange + language
│   ├── python/
│   │   ├── __init__.py
│   │   ├── venue.py                       # Venue enum + factory (split from exchange.py)
│   │   ├── binance_futures.py             # Binance adapter
│   │   ├── hyperliquid_perps.py           # Hyperliquid adapter
│   │   └── _extended.py                   # Planned (prefix = not implemented)
│   └── typescript/
│       ├── index.ts
│       ├── zerone.ts                      # 01 Exchange SDK (from mm_bot_01/src/sdk/)
│       └── binance-feed.ts               # Binance WS price feed
│
├── bots/                                  # Bot instances, language-scoped
│   ├── python/
│   │   ├── core/                          # WAS: bots/shared/ — renamed for clarity
│   │   │   ├── bot.py                     # TradingBot orchestrator
│   │   │   ├── config.py                  # Config loader (env + yaml)
│   │   │   ├── risk.py                    # RiskManager
│   │   │   ├── state.py                   # StateManager (SQLite)
│   │   │   └── strategy.py                # Signal, Bar, Position types
│   │   ├── btc_trend_4h/                  # v40 strategy
│   │   ├── btc_momentum_1h/               # v48b strategy
│   │   └── template/                      # NEW: copy to create new bot
│   └── typescript/
│       ├── core/                          # Shared TS types, logger
│       ├── mm_bot_01/                     # Migrated MM bot
│       │   ├── bots/                      # mm, utbot, sqzbot, bbrevbot
│       │   ├── cli/
│       │   ├── backtest/
│       │   ├── pricing/                   # Candle aggregator (exchange-agnostic)
│       │   ├── package.json
│       │   └── tsconfig.json
│       └── template/                      # NEW: copy for new TS bot
│
├── scripts/
│   └── backtests/
│       └── hyperliquid/                   # Venue-specific backtest scripts
│
├── backtest-results/                      # NEW: consolidated results
│   └── hyperliquid/                       # Moved from docs/backtests/
│
├── strategies/                            # Pine Script (unchanged)
├── docs/                                  # Documentation (reorganized)
├── tests/
│   ├── python/
│   └── typescript/
└── archive/                               # Historical (unchanged)
```

## Migration Phases

### Phase 1: Python reorganization (low risk)
- Create new directory structure
- Split `exchange.py` into venue.py + individual adapters
- Move `bots/shared/` → `bots/python/core/`
- Move bot instances → `bots/python/<bot>/`
- Organize backtest scripts by venue
- Move tests → `tests/python/`
- Create template from btc_trend_4h
- Fix risk.py bug (max_position_pct)
- Update all imports

### Phase 2: TypeScript integration
- Create `exchanges/typescript/` with 01 SDK
- Create `bots/typescript/` structure
- Migrate mm_bot_01 into monorepo
- Create TS template
- Update TypeScript imports

### Phase 3: Consolidation
- Move `docs/backtests/` → `backtest-results/`
- Unify `.env` conventions (namespace per exchange)
- Update `scripts/start-bot.sh`
- Update root README.md
- Update .gitignore for new paths

### Phase 4: Cleanup
- Remove old `bots/shared/`, `bots/btc_trend_4h/`, `bots/btc_momentum_1h/`
- Remove old `mm_bot_01/` directory
- Verify all bots still compile and run
- Run tests

## Constraints

- Each phase must leave the repo in a working state
- No behavior changes — only structural
- All imports must be updated
- Tests must pass after each phase
- `.env` format preserved (secrets only)
