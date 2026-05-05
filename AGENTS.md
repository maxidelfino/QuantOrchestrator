# QuantOrchestrator Team

This repository defines a specialized agent team for algorithmic trading and quantitative strategy development.

## Available Agents

### Primary Agents

| Agent | Purpose |
|-------|---------|
| `QuantOrchestrator` | Pure orchestrator for trading bot design. Coordinates subagents, synthesizes results, challenges weak decisions. Never executes directly — always delegates. |
| `PromptEngineer` | Production-grade prompt engineer. Turns vague requests into copy-paste-ready prompts for OpenCode and LLM workflows. NEVER executes tasks — only returns prompts. |

### Subagents

| Agent | Purpose |
|-------|---------|
| `strategy-architect` | Designs strategies, edges and hypothesis-driven trading systems |
| `backtesting-engineer` | Builds and evaluates historical backtests |
| `execution-engineer` | Designs execution engines, venue adapters and order lifecycle logic |
| `risk-engineer` | Defines risk rules, position sizing and capital protection controls |
| `market-structure-researcher` | Researches DEX microstructure, arbitrage, MEV and sniping constraints |
| `prediction-market-quant` | Designs and evaluates strategies for prediction markets |

## Project Structure

- `prompts/` — Agent prompt definitions
- `references/` — Trading domain references and checklists
- `docs/` — Documentation and research papers

## Global Dependencies

This project assumes a global OpenCode + gentle-ai stack with:
- Engram MCP (memory persistence)
- Context7 MCP (documentation lookups)
- SDD agents (`sdd-*`) for structured development workflows
- `judgment-day` skill for adversarial reviews

## Available Skills

Skills are loaded automatically based on context. Primary agents and subagents should reference this list to know which skills are available in this project.

### Trading & Quantitative Skills

| Skill | Trigger | Used By |
|-------|---------|---------|
| `algorithmic-trading` | building trading systems, backtesting, execution algorithms, microstructure analysis | QuantOrchestrator, strategy-architect, execution-engineer |
| `backtesting-trading-strategies` | backtest strategy, historical performance, simulate trades, optimize parameters | QuantOrchestrator, backtesting-engineer, strategy-architect |
| `risk-management` | sizing positions, stop-loss and capital protection decisions | QuantOrchestrator, risk-engineer, strategy-architect |
| `technical-analysis` | technical analysis, indicators, chart patterns, support/resistance, trend-following | QuantOrchestrator, strategy-architect, backtesting-engineer |
| `trading-expert` | expert trading systems, quant analysis, execution and market data | All trading subagents |

### Development & Workflow Skills

| Skill | Trigger | Used By |
|-------|---------|---------|
| `brainstorming` | before any creative work: creating features, adding/modifying behavior | QuantOrchestrator, PromptEngineer |
| `work-unit-commits` | implementing change slices, commits, PR splitting with tests/docs together | QuantOrchestrator, execution-engineer |
| `chained-pr` | chained/stacked PR planning and 400-line review-budget enforcement | QuantOrchestrator |
| `issue-creation` | creating GitHub issues (bug/feature) with workflow labels | QuantOrchestrator, PromptEngineer |
| `branch-pr` | creating pull requests and validating issue-first workflow | QuantOrchestrator |
| `judgment-day` | dual adversarial review: "judgment day" / "dual review" | QuantOrchestrator |
| `skill-creator` | creating new reusable agent skills and SKILL.md packages | PromptEngineer |
| `find-skills` | find/discover/install capabilities as reusable skills | QuantOrchestrator, PromptEngineer |
| `comment-writer` | drafting PR/issue/review/slack comments | All agents |
| `cognitive-doc-design` | writing guides/readmes/rfcs/onboarding/architecture docs | QuantOrchestrator, PromptEngineer |

### Technology-Specific Skills (when applicable)

| Skill | Trigger | Used By |
|-------|---------|---------|
| `typescript` | TypeScript types/interfaces/generics strict patterns | execution-engineer, backtesting-engineer |
| `go-testing` | writing Go tests, table-driven tests, Bubbletea teatest | execution-engineer |
| `react-native` | React Native (Expo/navigation/native modules/platform code) | execution-engineer |
| `hexagonal-architecture-react-native-kotlin-swift` | hexagonal architecture for RN + Kotlin/Swift native layers | execution-engineer |

### SEO Skills (when applicable)

| Skill | Trigger | Used By |
|-------|---------|---------|
| `seo` | SEO/audit/schema/CWV/E-E-A-T/AI Overviews/GEO | PromptEngineer (on request) |
| `seo-audit` | full SEO audit / full site health check | PromptEngineer (on request) |
| `seo-content` | content quality / E-E-A-T / readability / thin content | PromptEngineer (on request) |
| `seo-technical` | technical SEO, crawl/index/security/CWV/JS rendering | PromptEngineer (on request) |

### Skill Registry

Full registry with compact rules available at `.atl/skill-registry.md`. The orchestrator reads this registry once per session and injects matching compact rules into sub-agent prompts automatically.
