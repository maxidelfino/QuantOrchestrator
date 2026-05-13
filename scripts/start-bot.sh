#!/usr/bin/env bash
# Launch v40 bot in a persistent tmux session
# Survives terminal close and SSH disconnect
# Does NOT survive reboot (needs systemd/launchd for that)

SESSION="v40-hyperliquid"

# Kill existing session if any (optional — comment out to keep)
tmux kill-session -t "$SESSION" 2>/dev/null

# Create new detached session
tmux new-session -d -s "$SESSION" -n bot

# Send commands to the session
tmux send-keys -t "$SESSION" "cd $(pwd)" C-m
tmux send-keys -t "$SESSION" "python3 -m bot.main --log-level INFO" C-m

echo "Bot started in tmux session: $SESSION"
echo ""
echo "Attach:   tmux attach -t $SESSION"
echo "Detach:   Ctrl+B, D"
echo "Kill:     tmux kill-session -t $SESSION"
echo "Logs:     tail -f bot.log"
