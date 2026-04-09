# QuantOrchestrator model alignment and TradingView MCP design

## Goal

Align all QuantOrchestrator subagents to inherit the primary agent model and add TradingView MCP only where it improves chart research and idea validation.

## Decisions

### 1. Model policy

- Keep `model: "openai/gpt-5.4"` only on the primary `QuantOrchestrator` agent.
- Remove explicit `model` fields from QuantOrchestrator subagents so they inherit the primary/default model behavior.
- Leave prompt routing and tool permissions intact unless they must change for the MCP integration.

### 2. TradingView MCP scope

Enable TradingView MCP access only for:

- `strategy-architect`
- `backtesting-engineer`
- `market-structure-researcher`
- `risk-engineer`

Do not enable it yet for:

- `prediction-market-quant`
- `execution-engineer`
- `judgment-day`
- hidden judges
- SDD agents

### 3. Allowed use of TradingView MCP

TradingView MCP is allowed for:

- chart context and symbol/timeframe inspection
- indicator/state reading
- screenshots and visual review
- technical structure and setup validation
- research support while discussing strategy quality and risk framing

TradingView MCP is not evidence of:

- execution readiness
- backtest sufficiency
- real edge after costs
- automated trading capability

## Files to update

- `opencode.json`
  - remove subagent `model` properties
  - add TradingView MCP tools to the approved subagents only
- `prompts/strategy-architect.md`
- `prompts/backtesting-engineer.md`
- `prompts/market-structure-researcher.md`
- `prompts/risk-engineer.md`
  - add guidance that TradingView MCP is for research and validation support only

## Risks and guardrails

- TradingView MCP depends on local TradingView Desktop with CDP enabled on port `9222`.
- The MCP is a local chart-analysis bridge, not broker/execution infrastructure.
- Prompts should explicitly prevent overclaiming based on chart context alone.

## Success criteria

- All QuantOrchestrator subagents run without explicit per-agent model overrides.
- Only the selected four subagents receive TradingView MCP access.
- The selected prompts clearly describe the MCP as a research/validation tool, not proof of edge or execution support.
