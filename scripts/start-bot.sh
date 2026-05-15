#!/usr/bin/env bash
# Launch trading bots in a persistent tmux session
# Survives terminal close and SSH disconnect
# Does NOT survive reboot (needs systemd/launchd for that)

# ── Choose your bot ──────────────────────────────────────────
# v40  — BTC trend-following 4h
# BOT_MODULE="bots.python.btc_trend_4h"
# SESSION="v40-btc-trend"

# v48b — BTC momentum 1h
BOT_MODULE="bots.python.btc_momentum_1h"
SESSION="v48b-btc-momentum"

# shared utilities (both bots)
# BOT_MODULE="bots.python.core"
# SESSION="bots-shared"
# ─────────────────────────────────────────────────────────────

# Kill existing session if any (optional — comment out to keep)
tmux kill-session -t "$SESSION" 2>/dev/null

# Create new detached session
tmux new-session -d -s "$SESSION" -n bot

# Send commands to the session
tmux send-keys -t "$SESSION" "cd $(pwd)" C-m
tmux send-keys -t "$SESSION" "python3 -m ${BOT_MODULE} --log-level INFO" C-m

echo "Bot started in tmux session: $SESSION"
echo ""
echo "Attach:   tmux attach -t $SESSION"
echo "Detach:   Ctrl+B, D"
echo "Kill:     tmux kill-session -t $SESSION"
echo "Logs:     tail -f bot.log"
