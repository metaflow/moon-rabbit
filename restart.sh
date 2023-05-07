#!/bin/bash
pkill -f /root/.local/share/virtualenvs/moon-rabbit-T0OZLLxu/bin/python3
sleep 5s
cd /var/moon-rabbit
echo $(date) > restart_date.txt
# nohup pipenv run python3 main.py --twitch bo_ted --log bo_ted > nohup1.out 2>&1 &
nohup pipenv run python3 main.py --discord --log_level INFO --log discord > nohup2.out 2>&1 &
nohup pipenv run python3 main.py --twitch moon_robot --log_level INFO --log moon_robot > nohup2.out 2>&1 &
# nohup pipenv run python3 main.py --twitch bo_ted > nohup2.out 2>&1 