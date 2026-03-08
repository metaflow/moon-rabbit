#!/bin/bash
export PATH="$PATH:/root/.local/bin"
echo "restarting"
pkill -f /var/moon-rabbit/.venv/bin/python3
sleep 5s
cd /var/moon-rabbit
echo $(date) > runtime/restart_date.txt
nohup uv run python3 main.py --discord --log runtime/discord > runtime/discord_stdout 2>&1 &
nohup uv run python3 main.py --twitch moon_robot --log runtime/moon_robot > runtime/twitch_moon_robot_stdout 2>&1 &