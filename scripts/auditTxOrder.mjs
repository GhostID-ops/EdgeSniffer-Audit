import fs from "node:fs";
import path from "node:path";
import dotenv from "dotenv";

dotenv.config({ path: process.env.INSTANCE_ENV_PATH || ".env" });

function arg(name, fallback = "") {
  const prefix = `--${name}=`;
  const found = process.argv.find((value) => value.startsWith(prefix));
  return found ? found.slice(prefix.length) : fallback;
}

function readJsonl(file) {
  if (!file || !fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf8")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function num(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function detailNum(row, key) {
  return num(row?.details?.[key]);
}

function short(value) {
  return typeof value === "string" && value.length > 8 ? `${value.slice(0, 8)}...` : value || "-";
}

function rpcUrl() {
  if (process.env.RPC_URL) return process.env.RPC_URL;
  if (process.env.SECURE_RPC_URL) return process.env.SECURE_RPC_URL;
  if (process.env.HELIUS_RPC_URL) return process.env.HELIUS_RPC_URL;
  if (process.env.HELIUS_API_KEY) return `https://mainnet.helius-rpc.com/?api-key=${process.env.HELIUS_API_KEY}`;
  return "https://api.mainnet-beta.solana.com";
}

async function rpc(method, params) {
  const res = await fetch(rpcUrl(), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const data = await res.json();
  if (data.error) throw new Error(`${method} failed: ${data.error.message || JSON.stringify(data.error)}`);
  return data.result;
}

const blockCache = new Map();
async function blockSignatures(slot) {
  if (slot == null) return [];
  if (blockCache.has(slot)) return blockCache.get(slot);
  const block = await rpc("getBlock", [
    slot,
    {
      encoding: "json",
      maxSupportedTransactionVersion: 0,
      rewards: false,
      transactionDetails: "signatures",
    },
  ]);
  const signatures = Array.isArray(block?.signatures)
    ? block.signatures
    : Array.isArray(block?.transactions)
      ? block.transactions.map((tx) => tx?.transaction?.signatures?.[0]).filter(Boolean)
      : [];
  blockCache.set(slot, signatures);
  return signatures;
}

function comparePosition(aSlot, aIndex, bSlot, bIndex) {
  if (aSlot == null || aIndex == null || bSlot == null || bIndex == null) return null;
  if (aSlot !== bSlot) return aSlot - bSlot;
  return aIndex - bIndex;
}

const telemetryPath = arg("telemetry", process.env.EXECUTION_TELEMETRY_PATH || path.join("data", "execution-telemetry.jsonl"));
const sinceRaw = arg("since", "today");
const limit = Number(arg("limit", "25")) || 25;
const now = new Date();
const since =
  sinceRaw === "today"
    ? new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
    : Number.isFinite(Date.parse(sinceRaw))
      ? Date.parse(sinceRaw)
      : 0;

const rows = readJsonl(telemetryPath).filter((row) => (num(row.tsMs) ?? Date.parse(row.ts ?? "")) >= since);
const groups = new Map();
for (const row of rows) {
  if (!row.sourceSignature) continue;
  const group = groups.get(row.sourceSignature) || [];
  group.push(row);
  groups.set(row.sourceSignature, group);
}

const chains = [];
for (const [sourceSignature, group] of groups) {
  const sorted = [...group].sort((a, b) => (num(a.tsMs) ?? 0) - (num(b.tsMs) ?? 0));
  const source = sorted.find((row) => row.kind === "source-detected");
  const broadcast = sorted.find((row) => (row.kind === "broadcast" || row.kind === "stage:broadcast") && row.success === true);
  const confirm = sorted.find((row) => row.kind === "stage:confirm");
  if (!source || !broadcast || (source.side ?? broadcast.side) !== "buy") continue;
  const sourceSlot = detailNum(source, "sourceSlot");
  const sourceTransactionIndex = detailNum(source, "sourceTransactionIndex");
  const landingSlot = detailNum(confirm ?? broadcast, "landingSlot") ?? detailNum(broadcast, "landingSlot");
  chains.push({
    sourceSignature,
    mirrorSignature: broadcast.signature,
    mint: source.mint ?? broadcast.mint ?? "unknown",
    route: broadcast.route ?? source.route ?? "unknown",
    detector: source.detector ?? broadcast.detector ?? "unknown",
    sourceSlot,
    sourceTransactionIndex,
    landingSlot,
    sourceSeenAt: num(source.tsMs) ?? 0,
  });
}

const byMint = new Map();
for (const chain of chains) {
  const list = byMint.get(chain.mint) || [];
  list.push(chain);
  byMint.set(chain.mint, list);
}
for (const list of byMint.values()) {
  list.sort((a, b) => (a.sourceSlot ?? 0) - (b.sourceSlot ?? 0) || (a.sourceTransactionIndex ?? 0) - (b.sourceTransactionIndex ?? 0));
}

const latest = chains.slice(-limit).reverse();
const output = [
  `# Transaction Order Audit`,
  ``,
  `Telemetry: ${telemetryPath}`,
  `Window: ${sinceRaw}`,
  ``,
  `| Source Tx | Mint | Route | Detector | Source slot:index | Our slot:index | Relation | Next source buy | Between first/next? |`,
  `|---|---|---|---|---:|---:|---|---:|---|`,
];

for (const chain of latest) {
  let mirrorIndex = null;
  if (chain.landingSlot != null && chain.mirrorSignature) {
    const sigs = await blockSignatures(chain.landingSlot).catch(() => []);
    const idx = sigs.indexOf(chain.mirrorSignature);
    if (idx >= 0) mirrorIndex = idx;
  }

  let relation = "unknown";
  const diff = comparePosition(chain.landingSlot, mirrorIndex, chain.sourceSlot, chain.sourceTransactionIndex);
  if (diff != null) {
    relation = diff === 0 ? "same index" : diff > 0 ? `after source (+${diff} tx)` : `before source (${diff} tx)`;
  } else if (chain.landingSlot != null && chain.sourceSlot != null) {
    relation = chain.landingSlot === chain.sourceSlot ? "same slot, tx index unknown" : `+${chain.landingSlot - chain.sourceSlot} slot(s)`;
  }

  const mintSources = byMint.get(chain.mint) || [];
  const currentIdx = mintSources.findIndex((item) => item.sourceSignature === chain.sourceSignature);
  const nextSource = currentIdx >= 0 ? mintSources[currentIdx + 1] : null;
  let between = "-";
  if (nextSource && chain.landingSlot != null && mirrorIndex != null) {
    const afterCurrent = comparePosition(chain.landingSlot, mirrorIndex, chain.sourceSlot, chain.sourceTransactionIndex);
    const beforeNext = comparePosition(chain.landingSlot, mirrorIndex, nextSource.sourceSlot, nextSource.sourceTransactionIndex);
    between = afterCurrent != null && beforeNext != null && afterCurrent > 0 && beforeNext < 0 ? "yes" : "no";
  }

  output.push(
    `| \`${short(chain.sourceSignature)}\` | \`${short(chain.mint)}\` | \`${chain.route}\` | \`${chain.detector}\` | ` +
      `${chain.sourceSlot ?? "-"}:${chain.sourceTransactionIndex ?? "-"} | ${chain.landingSlot ?? "-"}:${mirrorIndex ?? "-"} | ` +
      `${relation} | ${nextSource ? `${nextSource.sourceSlot ?? "-"}:${nextSource.sourceTransactionIndex ?? "-"}` : "-"} | ${between} |`
  );
}

console.log(output.join("\n"));
