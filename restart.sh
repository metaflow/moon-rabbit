#!/bin/bash
export PATH="$PATH:/root/.local/bin"
echo "restarting"
pkill -f /root/.local/share/virtualenvs/moon-rabbit*
sleep 5s
cd /var/moon-rabbit
echo $(date) > runtime/restart_date.txt
nohup pipenv run python3 main.py --discord --log runtime/discord > runtime/discord_stdout 2>&1 &
nohup pipenv run python3 main.py --twitch moon_robot --log runtime/moon_robot > runtime/twitch_moon_robot_stdout 2>&1 &