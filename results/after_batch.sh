#!/bin/zsh
cd ~/Projects/jev-chess
while pgrep -f "jevchess.experiment ladder" >/dev/null; do sleep 15; done
echo "ladder 1 done $(date)"
WORKERS=6 .venv/bin/python -m jevchess.experiment ladder > results/ladder2.log 2>&1
echo "ladder 2 done $(date)"
