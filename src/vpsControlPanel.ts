import { execFile } from "node:child_process";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const PORT = Math.max(1, Math.min(65535, parseInt(process.env.VPS_CONTROL_PORT ?? "8799", 10)));

type InstanceId = "main" | "4bdk" | "5qn" | "cya";

type InstanceConfig = {
  id: InstanceId;
  label: string;
  service: string;
  dir: string;
  envPath: string;
  apiPort: number;
};

const INSTANCES: Record<InstanceId, InstanceConfig> = {
  main: {
    id: "main",
    label: "Main",
    service: "finalbot",
    dir: "/root/bot/FinalBot",
    envPath: "/root/bot/FinalBot/.env",
    apiPort: 3100,
  },
  "4bdk": {
    id: "4bdk",
    label: "4BdK",
    service: "finalbot-4bdk",
    dir: "/root/bot/FinalBot-4bdk",
    envPath: "/root/bot/FinalBot-4bdk/.env",
    apiPort: 3101,
  },
  "5qn": {
    id: "5qn",
    label: "5qn",
    service: "finalbot-5qn",
    dir: "/root/bot/FinalBot-5qn",
    envPath: "/root/bot/FinalBot-5qn/.env",
    apiPort: 3102,
  },
  cya: {
    id: "cya",
    label: "Cya",
    service: "finalbot-cya",
    dir: "/root/bot/FinalBot-cya",
    envPath: "/root/bot/FinalBot-cya/.env",
    apiPort: 3103,
  },
};

const ENV_KEYS = [
  "SIMULATION_MODE",
  "SHADOW_EXECUTION_MODE",
  "BOT_CONTROL_API_ENABLED",
  "WEB_PORT",
  "TRACKED_WALLETS",
  "MIRROR_SELLS",
  "MIRROR_SELL_MODE",
  "MIRROR_SELL_PROPORTIONAL_MIN_PCT",
  "MIRROR_SELL_PROPORTIONAL_MAX_PCT",
  "MIRROR_SELL_PROPORTIONAL_FALLBACK",
  "HYBRID_EXIT_ENABLED",
  "HYBRID_EXIT_TP_PCT",
  "HYBRID_EXIT_TP_SELL_PCT",
  "HYBRID_EXIT_SOURCE_EXIT_REMAINING",
  "BUY_ONCE_PER_TOKEN",
  "MAX_OPEN_POSITIONS",
  "MIN_BUY_SOL",
  "MAX_BUY_SOL",
  "MAX_SOL_PER_TOKEN",
  "MAX_SESSION_DEPLOYED_SOL",
  "STOP_LOSS_PCT",
  "MIN_HOLD_BEFORE_STOP_LOSS_MS",
  "TRAILING_STEPS",
  "SKIP_SOURCE_PREEXISTING_MINTS",
  "SOURCE_PREEXISTING_SCAN_TX_LIMIT",
  "MAX_ENTRY_PRICE_IMPACT_PCT",
  "MAX_SOURCE_DRIFT_PCT",
  "PREBUY_MORALIS_FILTER_ENABLED",
  "LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED",
  "LOCAL_PUMPFUN_SOL_PRICE_USD",
  "LOCAL_PUMPFUN_MIN_MARKET_CAP_SOL",
  "LOCAL_PUMPFUN_MAX_MARKET_CAP_SOL",
  "PREBUY_MIN_MARKET_CAP_USD",
  "PREBUY_MAX_MARKET_CAP_USD",
  "PREBUY_MIN_LIQUIDITY_USD",
  "PREBUY_MIN_VOLUME_USD",
  "PREBUY_MIN_MOMENTUM_PCT",
  "PREBUY_MORALIS_WINDOW_MS",
  "PREBUY_MORALIS_TIMEOUT_MS",
  "PREBUY_FILTER_FAIL_OPEN",
  "BLOCK_PUMPFUN_MAYHEM",
  "PUMPFUN_MAYHEM_STRICT",
  "SHYFT_GRPC_ENABLED",
  "SHYFT_GRPC_URL",
  "SHYFT_GRPC_COMMITMENT",
  "SHYFT_RABBITSTREAM_ENABLED",
  "SHYFT_RABBITSTREAM_URL",
  "RUST_SIDECAR_ENABLED",
  "RUST_SIDECAR_USE_CARGO",
  "RUST_EXECUTOR_ENABLED",
  "RUST_EXECUTOR_TIMEOUT_MS",
  "RUST_HOT_SNIPER_ENABLED",
  "RUST_HOT_SNIPER_PRIMARY",
  "RUST_HOT_SNIPER_PRIMARY_FALLBACK_ENABLED",
  "RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS",
  "RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS",
  "RUST_HOT_SNIPER_JITO_TIP_LAMPORTS",
  "RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS",
  "JITO_SHREDSTREAM_ENABLED",
  "JITO_SHREDSTREAM_GRPC_URL",
  "USE_JITO_DIRECT",
  "USE_JITO_FALLBACK",
  "JITO_SEND_MODE",
  "BROADCAST_RACE_ENABLED",
  "EXECUTION_MULTI_SEND_ENABLED",
  "SENDER_RPC_FALLBACK_ENABLED",
  "JITO_REGION",
  "JITO_REGIONS",
  "JITO_REGION_RACE_ENABLED",
  "JITO_CONNECTION_WARMUP_ENABLED",
  "JITO_CONNECTION_WARMUP_MS",
  "JITO_TIP_ORACLE_ENABLED",
  "JITO_TIP_ORACLE_PERCENTILE",
  "JITO_TIP_ORACLE_REFRESH_MS",
  "JITO_TIP_ORACLE_MIN_LAMPORTS",
  "JITO_TIP_ORACLE_MAX_LAMPORTS",
  "JITO_TIP_FLOOR_URL",
  "TIP_LAMPORTS",
  "PRIORITY_FEE_MICRO_LAMPORTS",
  "DYNAMIC_PRIORITY_FEE_ENABLED",
  "DYNAMIC_PRIORITY_FEE_REFRESH_MS",
  "DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS",
  "EXECUTION_TIP_MULTIPLIERS",
  "FAST_MODE",
  "WARM_WALLET_MODE",
  "KEEP_WSOL_ATA_OPEN",
  "PUMPFUN_EXACT_SOL_IN_BUYS",
  "PUMPSWAP_DIRECT_BUY_ENABLED",
  "PUMPSWAP_DIRECT_PREFLIGHT",
] as const;

const ENV_KEY_SET = new Set<string>(ENV_KEYS);

const SPEED_MODE_ENV: Record<string, string> = {
  PREBUY_MORALIS_FILTER_ENABLED: "false",
  LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED: "false",
  LOCAL_PUMPFUN_SOL_PRICE_USD: "",
  LOCAL_PUMPFUN_MIN_MARKET_CAP_SOL: "",
  LOCAL_PUMPFUN_MAX_MARKET_CAP_SOL: "",
  PREBUY_MIN_MARKET_CAP_USD: "",
  PREBUY_MAX_MARKET_CAP_USD: "",
  PREBUY_MIN_LIQUIDITY_USD: "",
  PREBUY_MIN_VOLUME_USD: "0",
  PREBUY_MIN_MOMENTUM_PCT: "",
  PREBUY_FILTER_FAIL_OPEN: "true",
  BLOCK_PUMPFUN_MAYHEM: "false",
  PUMPFUN_MAYHEM_STRICT: "false",
  USE_JITO_DIRECT: "true",
  USE_JITO_FALLBACK: "true",
  JITO_SEND_MODE: "race",
  BROADCAST_RACE_ENABLED: "true",
  EXECUTION_MULTI_SEND_ENABLED: "true",
  SENDER_RPC_FALLBACK_ENABLED: "true",
  JITO_REGION_RACE_ENABLED: "true",
  JITO_CONNECTION_WARMUP_ENABLED: "true",
  JITO_CONNECTION_WARMUP_MS: "15000",
  RUST_EXECUTOR_ENABLED: "true",
  RUST_EXECUTOR_TIMEOUT_MS: "1500",
  RUST_HOT_SNIPER_ENABLED: "true",
  RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS: "",
  RUST_HOT_SNIPER_JITO_TIP_LAMPORTS: "",
  RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS: "",
  JITO_TIP_ORACLE_ENABLED: "true",
  JITO_TIP_ORACLE_PERCENTILE: "p75",
  JITO_TIP_ORACLE_REFRESH_MS: "5000",
  JITO_TIP_ORACLE_MIN_LAMPORTS: "200000",
  JITO_TIP_ORACLE_MAX_LAMPORTS: "2000000",
  DYNAMIC_PRIORITY_FEE_ENABLED: "true",
  DYNAMIC_PRIORITY_FEE_REFRESH_MS: "5000",
  FAST_MODE: "true",
  WARM_WALLET_MODE: "true",
  KEEP_WSOL_ATA_OPEN: "true",
  PUMPFUN_EXACT_SOL_IN_BUYS: "true",
  PUMPSWAP_DIRECT_BUY_ENABLED: "true",
  PUMPSWAP_DIRECT_PREFLIGHT: "true",
};

function execCommand(command: string, args: string[], cwd?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(command, args, { cwd, timeout: 120_000 }, (error, stdout, stderr) => {
      const output = `${stdout ?? ""}${stderr ?? ""}`.trim();
      if (error) {
        reject(new Error(output || error.message));
        return;
      }
      resolve(output);
    });
  });
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(text),
  });
  res.end(text);
}

function sendHtml(res: ServerResponse, html: string): void {
  res.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Content-Length": Buffer.byteLength(html),
  });
  res.end(html);
}

function sendError(res: ServerResponse, status: number, error: string): void {
  sendJson(res, status, { error });
}

function parseInstance(id: string): InstanceConfig | null {
  return id === "main" || id === "4bdk" || id === "5qn" || id === "cya" ? INSTANCES[id] : null;
}

function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 80_000) {
        reject(new Error("Request body too large"));
        req.destroy();
      }
    });
    req.on("end", () => {
      if (!raw.trim()) return resolve({});
      try {
        const value = JSON.parse(raw);
        if (!value || typeof value !== "object" || Array.isArray(value)) {
          reject(new Error("Expected JSON object"));
          return;
        }
        resolve(value as Record<string, unknown>);
      } catch {
        reject(new Error("Invalid JSON"));
      }
    });
    req.on("error", reject);
  });
}

async function readEnv(envPath: string): Promise<Record<string, string>> {
  const text = await readFile(envPath, "utf8");
  const out: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx <= 0) continue;
    const key = trimmed.slice(0, idx).trim();
    if (!ENV_KEY_SET.has(key)) continue;
    out[key] = trimmed.slice(idx + 1);
  }
  return out;
}

async function patchEnv(envPath: string, updates: Record<string, string>): Promise<Record<string, string>> {
  await mkdir(path.dirname(envPath), { recursive: true });
  let text = await readFile(envPath, "utf8");
  const backup = `${envPath}.backup-control-${new Date().toISOString().replace(/[:.]/g, "-")}`;
  await writeFile(backup, text, "utf8");

  for (const [key, value] of Object.entries(updates)) {
    if (!ENV_KEY_SET.has(key)) continue;
    const safeValue = String(value).replace(/\r?\n/g, "").trim();
    const re = new RegExp(`^#?\\s*${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}=.*$`, "m");
    if (re.test(text)) text = text.replace(re, `${key}=${safeValue}`);
    else text += `\n${key}=${safeValue}`;
  }

  await writeFile(envPath, text, "utf8");
  return readEnv(envPath);
}

async function serviceStatus(instance: InstanceConfig): Promise<Record<string, unknown>> {
  let active = "unknown";
  try {
    active = (await execCommand("systemctl", ["is-active", instance.service])).trim();
  } catch {
    active = "inactive";
  }
  let enabled = "unknown";
  try {
    enabled = (await execCommand("systemctl", ["is-enabled", instance.service])).trim();
  } catch {
    enabled = "disabled";
  }
  let logs = "";
  try {
    logs = await execCommand("journalctl", ["-u", instance.service, "-n", "40", "--no-pager"]);
  } catch (err) {
    logs = err instanceof Error ? err.message : String(err);
  }
  return {
    id: instance.id,
    label: instance.label,
    service: instance.service,
    dir: instance.dir,
    apiPort: instance.apiPort,
    active,
    enabled,
    env: await readEnv(instance.envPath).catch(() => ({})),
    logs,
  };
}

async function callBotApi(instance: InstanceConfig, apiPath: string): Promise<unknown> {
  const target = `http://127.0.0.1:${instance.apiPort}${apiPath}`;
  let response: Response;
  try {
    response = await fetch(target, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal: AbortSignal.timeout(60_000),
    });
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(
      `${instance.label} bot control API is unreachable at 127.0.0.1:${instance.apiPort}. ` +
        `Check ${instance.service} is running and .env has BOT_CONTROL_API_ENABLED=true and WEB_PORT=${instance.apiPort}. ` +
        `Details: ${detail}`
    );
  }
  const text = await response.text();
  let body: unknown = text;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    // keep text body
  }
  if (!response.ok) {
    const message =
      body && typeof body === "object" && "error" in body
        ? String((body as { error: unknown }).error)
        : text || response.statusText;
    throw new Error(message);
  }
  return body;
}

function html(): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FinalBot VPS Control</title>
<style>
  :root{color-scheme:dark;--bg:#08090b;--panel:#101318;--panel2:#151922;--line:#2b313a;--text:#f3f5f7;--muted:#9aa3af;--green:#42f59b;--red:#ff6472;--amber:#ffc857;--blue:#77b7ff}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;letter-spacing:0}
  header{height:58px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 18px;background:#0a0c10;position:sticky;top:0;z-index:2}
  h1{font-size:17px;margin:0}.wrap{padding:18px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
  .inst{border:1px solid var(--line);border-radius:8px;background:var(--panel);overflow:hidden}.head{display:flex;align-items:center;justify-content:space-between;padding:14px;border-bottom:1px solid var(--line)}
  .name{font-weight:800}.sub{color:var(--muted);font-size:12px;margin-top:3px}.pill{border:1px solid var(--line);border-radius:999px;padding:5px 9px;font-size:12px;color:var(--muted)}
  .pill.active{color:#03130a;background:var(--green);border-color:var(--green)}.pill.inactive{color:#190407;background:var(--red);border-color:var(--red)}
  .actions{display:flex;gap:8px;flex-wrap:wrap;padding:12px 14px;border-bottom:1px solid var(--line)}
  .actions.trade{background:#0b0d11}
  button{border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:8px;height:34px;padding:0 12px;cursor:pointer}button:hover{border-color:var(--blue)}button.danger:hover{border-color:var(--red)}
  .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:14px}
  label{display:grid;gap:5px;color:var(--muted);font-size:12px}input,textarea{width:100%;border:1px solid var(--line);background:#080a0e;color:var(--text);border-radius:8px;padding:9px;font:inherit;font-size:13px}
  textarea{min-height:70px;resize:vertical}.wide{grid-column:1/-1}.logs{border-top:1px solid var(--line);padding:12px 14px;background:#07080a}
  pre{margin:0;white-space:pre-wrap;word-break:break-word;color:#ccd3dc;font-size:12px;max-height:260px;overflow:auto}
  .toast{position:fixed;right:14px;bottom:14px;background:#101318;border:1px solid var(--line);border-radius:8px;padding:10px 12px;color:var(--muted);max-width:460px}
  @media(max-width:900px){.wrap{grid-template-columns:1fr}.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><h1>FinalBot VPS Control</h1><button onclick="load()">Refresh</button></header>
<main class="wrap" id="app"></main>
<div class="toast" id="toast" hidden></div>
<script>
const keys=${JSON.stringify(ENV_KEYS)};
const ids=["main","4bdk","5qn","cya"];
function toast(msg){const el=document.getElementById("toast");el.textContent=msg;el.hidden=false;setTimeout(()=>el.hidden=true,4500)}
async function api(url, opts){const res=await fetch(url,opts);const data=await res.json().catch(()=>({}));if(!res.ok)throw new Error(data.error||res.statusText);return data}
function field(id,key,val){const area=key==="TRAILING_STEPS"||key==="TRACKED_WALLETS"||key==="EXECUTION_TIP_MULTIPLIERS"||key.endsWith("_URL");return '<label class="'+(area?'wide':'')+'"><span>'+key+'</span>'+(area?'<textarea data-i="'+id+'" data-k="'+key+'">'+(val||'')+'</textarea>':'<input data-i="'+id+'" data-k="'+key+'" value="'+(val||'').replaceAll('"','&quot;')+'">')+'</label>'}
function card(s){const pill=s.active==="active"?"active":"inactive";return '<section class="inst"><div class="head"><div><div class="name">'+s.label+'</div><div class="sub">'+s.service+' · '+s.dir+' · API '+s.apiPort+'</div></div><div class="pill '+pill+'">'+s.active+'</div></div><div class="actions"><button onclick="act(\\''+s.id+'\\',\\'start\\')">Start</button><button class="danger" onclick="act(\\''+s.id+'\\',\\'stop\\')">Stop</button><button onclick="act(\\''+s.id+'\\',\\'restart\\')">Restart</button><button onclick="deploy(\\''+s.id+'\\')">Deploy</button><button onclick="speedMode(\\''+s.id+'\\')">Speed Mode</button><button onclick="save(\\''+s.id+'\\')">Save Env</button><button onclick="save(\\''+s.id+'\\',true)">Save + Restart</button></div><div class="actions trade"><button class="danger" onclick="botAction(\\''+s.id+'\\',\\'sell-all\\')">Sell All</button><button class="danger" onclick="botAction(\\''+s.id+'\\',\\'panic\\')">Panic</button><button onclick="botAction(\\''+s.id+'\\',\\'close-all-sim\\')">Close Sim</button><button onclick="botAction(\\''+s.id+'\\',\\'reset-session\\')">Reset Session</button></div><div class="grid">'+keys.map(k=>field(s.id,k,s.env?.[k]||"")).join("")+'</div><div class="logs"><pre>'+String(s.logs||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))+'</pre></div></section>'}
async function load(){try{const data=await api("/api/instances");document.getElementById("app").innerHTML=data.instances.map(card).join("")}catch(e){toast(e.message)}}
async function act(id,action){try{await api("/api/instances/"+id+"/"+action,{method:"POST"});toast(id+" "+action+" ok");await load()}catch(e){toast(e.message)}}
async function deploy(id){try{toast(id+" deploy running...");await api("/api/instances/"+id+"/deploy",{method:"POST"});toast(id+" deploy ok");await load()}catch(e){toast(e.message)}}
async function speedMode(id){if(!confirm(id+": apply low-latency speed mode env? Strategy/wallet/buy sizing will not be changed."))return;try{await api("/api/instances/"+id+"/speed-mode",{method:"POST"});toast(id+" speed mode applied");await load()}catch(e){toast(e.message)}}
async function botAction(id,action){const labels={"sell-all":"SELL ALL open positions","panic":"STOP BOT + SELL ALL","close-all-sim":"close all simulated positions","reset-session":"reset session state"};if(!confirm(id+": "+labels[action]+"?"))return;try{toast(id+" "+action+" running...");await api("/api/instances/"+id+"/bot-"+action,{method:"POST"});toast(id+" "+action+" ok");await load()}catch(e){toast(e.message)}}
async function save(id,restart=false){const updates={};document.querySelectorAll("[data-i='"+id+"']").forEach(el=>updates[el.dataset.k]=el.value);try{await api("/api/instances/"+id+"/env",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({updates,restart})});toast(id+" env saved"+(restart?" and restarted":""));await load()}catch(e){toast(e.message)}}
load();setInterval(load,10000);
</script>
</body>
</html>`;
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
  if (req.method === "GET" && url.pathname === "/") return sendHtml(res, html());

  if (req.method === "GET" && url.pathname === "/api/instances") {
    const instances = await Promise.all(Object.values(INSTANCES).map(serviceStatus));
    return sendJson(res, 200, { instances });
  }

  const match = url.pathname.match(/^\/api\/instances\/([^/]+)\/([^/]+)$/);
  if (!match) return sendError(res, 404, "Not found");
  const instance = parseInstance(match[1] ?? "");
  const action = match[2] ?? "";
  if (!instance) return sendError(res, 404, "Unknown instance");

  if (req.method === "POST" && ["start", "stop", "restart"].includes(action)) {
    await execCommand("systemctl", [action, instance.service]);
    return sendJson(res, 200, { ok: true, status: await serviceStatus(instance) });
  }

  if (req.method === "POST" && action === "env") {
    const body = await readJsonBody(req);
    const rawUpdates = body.updates;
    if (!rawUpdates || typeof rawUpdates !== "object" || Array.isArray(rawUpdates)) {
      return sendError(res, 400, "Missing updates");
    }
    const updates: Record<string, string> = {};
    for (const [key, value] of Object.entries(rawUpdates as Record<string, unknown>)) {
      if (ENV_KEY_SET.has(key)) updates[key] = String(value ?? "");
    }
    const env = await patchEnv(instance.envPath, updates);
    if (body.restart === true) await execCommand("systemctl", ["restart", instance.service]);
    return sendJson(res, 200, { ok: true, env, status: await serviceStatus(instance) });
  }

  if (req.method === "POST" && action.startsWith("bot-")) {
    const apiAction = action.slice("bot-".length);
    const paths: Record<string, string> = {
      "sell-all": "/api/actions/sell-all",
      panic: "/api/actions/panic",
      "close-all-sim": "/api/actions/close-all-sim",
      "reset-session": "/api/actions/reset-session",
    };
    const apiPath = paths[apiAction];
    if (!apiPath) return sendError(res, 404, "Unknown bot action");
    const result = await callBotApi(instance, apiPath);
    return sendJson(res, 200, { ok: true, result, status: await serviceStatus(instance) });
  }

  if (req.method === "POST" && action === "deploy") {
    const branch = (await execCommand("git", ["rev-parse", "--abbrev-ref", "HEAD"], instance.dir)).trim();
    await execCommand("git", ["pull", "origin", branch === "HEAD" ? "main" : branch], instance.dir);
    await execCommand("npm", ["install"], instance.dir);
    await execCommand("npm", ["run", "build"], instance.dir);
    await patchEnv(instance.envPath, {
      BOT_CONTROL_API_ENABLED: "true",
      WEB_PORT: String(instance.apiPort),
    });
    await execCommand("systemctl", ["restart", instance.service]);
    return sendJson(res, 200, { ok: true, status: await serviceStatus(instance) });
  }

  if (req.method === "POST" && action === "speed-mode") {
    const env = await patchEnv(instance.envPath, SPEED_MODE_ENV);
    return sendJson(res, 200, { ok: true, env, status: await serviceStatus(instance) });
  }

  return sendError(res, 404, "Not found");
}

const server = createServer((req, res) => {
  handle(req, res).catch((err) => sendError(res, 500, err instanceof Error ? err.message : String(err)));
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`VPS control panel: http://127.0.0.1:${PORT}`);
});
