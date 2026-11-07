module.exports = {
  apps: [
    {
      name: 'moon-rabbit',
      // Launch the venv python directly (not `uv run`): PM2 measures the pid it spawns, and with
      // the `uv` wrapper that was a 2 MB process, so memory was invisible and max_memory_restart
      // could never fire. Direct launch means deploys must run `uv sync --frozen` themselves.
      script: '.venv/bin/python3',
      args: 'main.py --discord --twitch moon_robot --log runtime/merged',
      cwd: '/var/moon-rabbit',
      autorestart: true,
      // Safety net: a healthy process is ~150 MB. Restart if something leaks again.
      max_memory_restart: '400M',
      // PM2 sends SIGINT then SIGKILL after kill_timeout (default 1.6 s). main.shutdown() waits up to
      // 10 s for the clients to close (it saves Twitch tokens), so give it room.
      kill_timeout: 15000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    {
      name: 'moon-rabbit-backup',
      script: './runtime/pg_backup.sh',
      cwd: '/var/moon-rabbit',
      cron_restart: '2 5 * * *',
      autorestart: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    }
  ]
};
