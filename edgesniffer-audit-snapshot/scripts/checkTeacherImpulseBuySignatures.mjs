#!/usr/bin/env node
import fs from "node:fs";

const envFile = process.env.ENV_FILE || "/root/bot/FinalBot-cya/.env";
const selectedFile = process.env.TEACHER_IMPULSE_SELECTED_EVENT_OUTPUT
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-mc5000-selected-events.jsonl";
const auditFile = process.env.RUST_SELECTED_CANDIDATE_AUDIT_FILE
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-mc5000-dust/rust-selected-candidate-audit.jsonl";
const policy = process.env.REPORT_POLICY || "teacher_impulse_mc5000_tp20_sl20_landing_calibration_v2";
const limit = clampInt(process.env.SIGNATURE_CHECK_LIMIT, 1, 100, 20);

loadEnvFile(envFile);
const rpcUrl = process.env.SECURE_RPC_URL || process.env.RPC_URL || process.env.SOLANA_RPC_URL;
if (!rpcUrl) throw new Error("Missing SECURE_RPC_URL/RPC_URL/SOLANA_RPC_URL");

const selected = new Set(readJsonl(selectedFile, 50_000)
  .filter((row) => row.policy === policy)
  .map((row) => row.key)
  .filter(Boolean));
const buys = readJsonl(auditFile, 50_000)
  .filter((row) => row.kind === "rustSelectedCandidateBuyResult")
  .filter((row) => selected.has(row.key))
  .filter((row) => row.signature)
  .slice(-limit);

const statuses = await getSignatureStatuses(buys.map((row) => row.signature));
for (let i = 0; i < buys.length; i++) {
  const buy = buys[i];
  const status = statuses[i];
  console.log(JSON.stringify({
    mint: buy.mint,
    signature: buy.signature,
    senderPath: buy.senderPath,
    selectedToBuyResultMs: buy.selectedToBuyResultMs,
    broadcastMs: buy.broadcastMs,
    confirmationStatus: status?.confirmationStatus ?? null,
    err: status?.err ?? null,
    slot: status?.slot ?? null,
  }));
}

async function getSignatureStatuses(signatures) {
  if (signatures.length === 0) return [];
  const body = {
    jsonrpc: "2.0",
    id: 1,
    method: "getSignatureStatuses",
    params: [signatures, { searchTransactionHistory: true }],
  };
  const res = await fetch(rpcUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`RPC status ${res.status}`);
  const json = await res.json();
  if (json.error) throw new Error(JSON.stringify(json.error));
  return json.result?.value || [];
}

function readJsonl(file, tailLines) {
  if (!fs.existsSync(file)) return [];
  const text = fs.readFileSync(file, "utf8");
  return text
    .trim()
    .split(/\n/)
    .slice(-tailLines)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function loadEnvFile(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, "utf8").split(/\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx <= 0) continue;
    const key = trimmed.slice(0, idx).trim();
    let value = trimmed.slice(idx + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}

function clampInt(value, min, max, fallback) {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}
