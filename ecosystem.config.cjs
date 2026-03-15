module.exports = {
  apps: [
    {
      name: 'moon-rabbit',
      script: 'uv',
      args: 'run python3 main.py --discord --twitch moon_robot --log runtime/merged',
      cwd: '/var/moon-rabbit',
      autorestart: true,
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
