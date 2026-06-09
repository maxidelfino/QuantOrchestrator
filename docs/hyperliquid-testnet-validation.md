# v40 BTC Bot — Hyperliquid Testnet Validation

This bot now supports `BOT_EXCHANGE=hyperliquid` in a **testnet-first** mode.

## Safety model

- Default is `BOT_TESTNET=true`.
- v40 logic is unchanged: BTC-only, 4h execution, daily EMA200 regime filter, ATR(14)
  × 3 stop model, 2% risk.
- Risk manager and kill switches remain active.
- Native stop orders on Hyperliquid are intentionally disabled via ccxt in this MVP.
  The bot uses a local stop trigger (`fetch_ticker` each poll) and exits reduce-only.
- Startup reconciliation is fail-closed: if venue has an open BTC position but local state
  has none, the bot stops and requires manual reconciliation.

## Required env vars (Hyperliquid)

Set in `.env`:

```bash
BOT_EXCHANGE=hyperliquid
BOT_SYMBOL=BTC/USDC:USDC
BOT_TESTNET=true

HYPERLIQUID_WALLET_ADDRESS=0x...
HYPERLIQUID_PRIVATE_KEY=0x...
# optional:
HYPERLIQUID_BASE_URL=https://api.hyperliquid-testnet.xyz
```

## Validation run commands

```bash
# 1) Install deps
python3 -m pip install -r requirements.txt

# 2) Prepare env
cp .env.example .env
# edit .env with Hyperliquid testnet credentials

# 3) Run tests first
pytest -q

# 4) Check state before trading
python3 -m bot.main --status

# 5) Start bot (testnet)
python3 -m bot.main --log-level INFO
```

## Caveats

- Because native stop is disabled in this MVP for safety/consistency, stop execution quality
  depends on poll frequency (`BOT_POLL_INTERVAL`) and ticker availability.
- Use small size and testnet wallet only.
