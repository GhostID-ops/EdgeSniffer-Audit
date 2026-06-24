module.exports = {
  apps: [
    {
      name: process.env.PM2_BOT_NAME || "finalbot-cya",
      script: "npm",
      args: "run bot",
      cwd: process.env.PM2_BOT_CWD || process.cwd(),
      autorestart: true,
      max_restarts: 25,
      min_uptime: "10s",
      restart_delay: 1000,
      kill_timeout: 10000,
      env: {
        NODE_ENV: "production",
      },
    },
  ],
};
