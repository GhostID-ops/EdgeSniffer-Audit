import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const root = process.cwd();
const envPath = path.join(root, ".env");
const presetPath = path.join(root, ".env.performance");
const backupDir = path.join(root, ".restore-backups");

function parseEnvLines(text) {
  const values = new Map();
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    values.set(line.slice(0, eq).trim(), line.slice(eq + 1));
  }
  return values;
}

if (!existsSync(presetPath)) {
  throw new Error(`Missing preset file: ${presetPath}`);
}

if (!existsSync(envPath)) {
  throw new Error("Missing .env. Create it first with your private keys/API keys, then rerun this command.");
}

const preset = parseEnvLines(readFileSync(presetPath, "utf8"));
const envText = readFileSync(envPath, "utf8");
const envLines = envText.split(/\r?\n/);
const seen = new Set();
let updated = 0;

const merged = envLines.map((rawLine) => {
  const trimmed = rawLine.trim();
  if (!trimmed || trimmed.startsWith("#")) return rawLine;
  const eq = rawLine.indexOf("=");
  if (eq <= 0) return rawLine;
  const key = rawLine.slice(0, eq).trim();
  if (!preset.has(key)) return rawLine;
  seen.add(key);
  const next = `${key}=${preset.get(key)}`;
  if (rawLine !== next) updated += 1;
  return next;
});

let added = 0;
for (const [key, value] of preset.entries()) {
  if (seen.has(key)) continue;
  merged.push(`${key}=${value}`);
  added += 1;
}

mkdirSync(backupDir, { recursive: true });
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const backupPath = path.join(backupDir, `.env.before-performance-${stamp}`);
copyFileSync(envPath, backupPath);
writeFileSync(envPath, merged.join("\n").replace(/\n*$/, "\n"));

console.log(`Updated .env from .env.performance (${updated} changed, ${added} added).`);
console.log(`Backup saved to ${path.relative(root, backupPath)}`);
console.log("Private keys/API keys were preserved unless you explicitly put them in .env.performance.");
