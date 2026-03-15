module.exports = {
  apps: [
    {
      name: 'moon-rabbit-discord',
      script: 'uv',
      args: 'run python3 main.py --discord --log runtime/discord',
      cwd: '/var/moon-rabbit',
      autorestart: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    {
      name: 'moon-rabbit-twitch',
      script: 'uv',
      args: 'run python3 main.py --twitch moon_robot --log runtime/moon_robot',
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
