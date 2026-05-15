# Contributing to QuantOrchestrator

## Development setup

1. Install dependencies:
   - `pip install -r requirements.lock` (preferred, deterministic)
   - or `pip install -r requirements.txt`
2. Run tests before opening a PR:
   - `pytest tests/python/`

## Add a new exchange (adapter pattern)

1. Implement an adapter under `exchanges/<venue>/adapters/` following `shared/python/exchange.py` contracts.
2. Add venue identifier to `SUPPORTED_VENUES` in `shared/python/config.py`.
3. Register adapter construction in `shared/python/exchange.py` (`create_exchange`).
4. Add tests in `tests/python/test_adapters.py` and/or `tests/python/test_config_exchange.py`.

## Add a new bot (copy template pattern)

1. Copy `exchanges/hyperliquid/bots/template/` to a new bot directory.
2. Update `config.yaml`:
   - set `strategy.strategy_class` to your strategy class path.
   - set bot-specific strategy parameters.
3. Implement the strategy class in `strategy.py`.
4. Ensure `__main__.py` loads config from the local bot directory and instantiates via `config.create_strategy_engine()`.
5. Add strategy tests under `tests/python/`.

## Add a new strategy indicator

1. Compute indicator in `compute_indicators()` and add it to the dataframe column.
2. The orchestrator builds `Bar.indicators` from computed columns.
3. Read values in strategy logic via `bar.indicator("name")` or `bar.indicators["name"]`.
4. Add/adjust tests for entry/exit behavior.

## Testing requirements

- Minimum requirement: `pytest tests/python/` passes locally.
- For strategy changes, include tests for:
  - entry conditions
  - exit conditions
  - sizing/stop edge cases

## Commit conventions

- Use Conventional Commits:
  - `feat:` new behavior
  - `fix:` bug fix
  - `refactor:` non-functional restructuring
  - `test:` test-only changes
  - `docs:` documentation

## Code style guidelines

- Keep code readable and explicit over clever shortcuts.
- Preserve existing architecture boundaries (`shared/python/*` abstractions, exchange adapters, per-bot strategy modules).
- Prefer typed dataclasses and small pure methods for strategy logic.
- Keep changes focused: strategy-specific code stays in strategy modules, shared behavior stays in shared modules.
