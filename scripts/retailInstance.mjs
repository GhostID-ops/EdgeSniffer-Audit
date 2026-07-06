#!/usr/bin/env node
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";

function arg(name, fallback = "") {
  const flag = `--${name}`;
  const idx = process.argv.indexOf(flag);
  if (idx >= 0 && process.argv[idx + 1]) return process.argv[idx + 1];
  const pref = process.argv.find((x) => x.startsWith(`${flag}=`));
  return pref ? pref.slice(flag.length + 1) : fallback;
}

function has(name) {
  return process.argv.includes(`--${name}`);
}

function safeId(value) {
  const id = String(value || "").trim().toLowerCase().replace(/[^a-z0-9_.-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  if (!id) throw new Error("Missing --id customer id");
  return id;
}

function run(cmd, args, opts = {}) {
  return execFileSync(cmd, args, { stdio: "inherit", ...opts });
}

const action = process.argv[2] && !process.argv[2].startsWith("--") ? process.argv[2] : "plan";
const repo = path.resolve(arg("repo", process.cwd()));
const id = safeId(arg("id", "friend"));
const port = Number(arg("port", "3001"));
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("--port must be 1-65535");
const root = path.resolve(arg("root", "/root/bot/finalbot-instances"));
const instanceDir = path.join(root, id);
const service = `finalbot-${id}`;
const nodeEnv = arg("node-env", "production");
const user = arg("service-user", "root");
const domain = arg("domain", "");
const publicPath = arg("path", `/${id}`);

const serviceText = `[Unit]
Description=FinalBot retail instance ${id}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${user}
WorkingDirectory=${repo}
Environment=NODE_ENV=${nodeEnv}
Environment=INSTANCE_ID=${id}
Environment=INSTANCE_ROOT=${instanceDir}
Environment=DATA_DIR=${path.join(instanceDir, "data")}
Environment=LOG_DIR=${path.join(instanceDir, "logs")}
ExecStart=/usr/bin/env npm run friend:demo -- ${instanceDir} ${port}
Restart=always
RestartSec=3
KillSignal=SIGTERM
TimeoutStopSec=45

[Install]
WantedBy=multi-user.target
`;

const nginxText = `# Optional nginx location for ${id}. Put inside your HTTPS server block.
location ${publicPath}/ {
  proxy_pass http://127.0.0.1:${port}/;
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
}
`;

function ensureDirs() {
  mkdirSync(path.join(instanceDir, "data"), { recursive: true });
  mkdirSync(path.join(instanceDir, "logs"), { recursive: true });
}

function writePlanFiles() {
  ensureDirs();
  writeFileSync(path.join(instanceDir, `${service}.service`), serviceText);
  writeFileSync(path.join(instanceDir, `${service}.nginx.conf`), nginxText);
  const readme = [`# ${service}`, "", `Instance dir: ${instanceDir}`, `Port: ${port}`, `Service: ${service}`, "", "## Install systemd", "", "```bash", `sudo cp ${path.join(instanceDir, `${service}.service`)} /etc/systemd/system/${service}.service`, "sudo systemctl daemon-reload", `sudo systemctl enable --now ${service}`, "```", "", "## Check", "", "```bash", `sudo systemctl status ${service} --no-pager`, `journalctl -u ${service} -n 120 --no-pager`, `curl -I http://127.0.0.1:${port}/friend-simple`, "```", "", domain ? `Public URL target: https://${domain}${publicPath}/friend-simple` : `Tunnel/local URL target: http://127.0.0.1:${port}/friend-simple`, ""].join("\n");
  writeFileSync(path.join(instanceDir, "README.md"), readme);
}

if (action === "plan" || action === "create") {
  writePlanFiles();
  console.log(`Wrote instance plan: ${instanceDir}`);
  console.log(`Service file: ${path.join(instanceDir, `${service}.service`)}`);
  console.log(`Nginx snippet: ${path.join(instanceDir, `${service}.nginx.conf`)}`);
  if (action === "plan") process.exit(0);
}

if (action === "install") {
  writePlanFiles();
  run("cp", [path.join(instanceDir, `${service}.service`), `/etc/systemd/system/${service}.service`]);
  run("systemctl", ["daemon-reload"]);
  run("systemctl", ["enable", "--now", service]);
  console.log(`Installed and started ${service}`);
} else if (action === "start") {
  run("systemctl", ["start", service]);
} else if (action === "stop") {
  run("systemctl", ["stop", service]);
} else if (action === "restart") {
  run("systemctl", ["restart", service]);
} else if (action === "status") {
  run("systemctl", ["status", service, "--no-pager"]);
} else if (action === "logs") {
  run("journalctl", ["-u", service, "-n", arg("lines", "160"), "--no-pager"]);
} else if (action !== "create") {
  throw new Error(`Unknown action: ${action}`);
}