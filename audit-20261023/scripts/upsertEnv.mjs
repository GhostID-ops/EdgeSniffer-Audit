import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const root = process.cwd();
const envPath = path.join(root, ".env");
const backupDir = path.join(root, ".restore-backups");

function usage() {
  console.error("Usage: node scripts/upsertEnv.mjs [--no-backup] KEY=value [KEY=value ...]");
  console.error("       node scripts/upsertEnv.mjs --from-file .env.preset [--no-backup]");
  process.exit(1);
}

function parseAssignmentsFromFile(filePath) {
  const values = [];
  const text = readFileSync(path.resolve(root, filePath), "utf8");
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    values.push([line.slice(0, eq).trim(), line.slice(eq + 1)]);
  }
  return values;
}

function parseArgs(argv) {
  let backup = true;
  let fromFile = "";
  const assignments = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--no-backup") {
      backup = false;
      continue;
    }
    if (arg === "--from-file") {
      fromFile = argv[i + 1] ?? "";
      i += 1;
      continue;
    }
    const eq = arg.indexOf("=");
    if (eq <= 0) usage();
    assignments.push([arg.slice(0, eq), arg.slice(eq + 1)]);
  }
  if (fromFile) assignments.push(...parseAssignmentsFromFile(fromFile));
  if (assignments.length === 0) usage();
  return { backup, assignments };
}

if (!existsSync(envPath)) {
  throw new Error("Missing .env in the current directory.");
}

const { backup, assignments } = parseArgs(process.argv.slice(2));
const updates = new Map();
for (const [key, value] of assignments) {
  if (!key || key.includes("\n") || key.includes("=")) usage();
  updates.set(key, value);
}

const lines = readFileSync(envPath, "utf8").split(/\r?\n/);
const seen = new Set();
const output = [];
let changed = 0;
let removedDuplicates = 0;

for (const rawLine of lines) {
  const trimmed = rawLine.trim();
  if (!trimmed || trimmed.startsWith("#")) {
    output.push(rawLine);
    continue;
  }
  const eq = rawLine.indexOf("=");
  if (eq <= 0) {
    output.push(rawLine);
    continue;
  }
  const key = rawLine.slice(0, eq).trim();
  if (!updates.has(key)) {
    output.push(rawLine);
    continue;
  }
  if (seen.has(key)) {
    removedDuplicates += 1;
    continue;
  }
  seen.add(key);
  const next = `${key}=${updates.get(key)}`;
  if (rawLine !== next) changed += 1;
  output.push(next);
}

let added = 0;
for (const [key, value] of updates.entries()) {
  if (seen.has(key)) continue;
  output.push(`${key}=${value}`);
  added += 1;
}

if (backup) {
  mkdirSync(backupDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  copyFileSync(envPath, path.join(backupDir, `.env.before-upsert-${stamp}`));
}

writeFileSync(envPath, output.join("\n").replace(/\n*$/, "\n"));
console.log(`Updated .env (${changed} changed, ${added} added, ${removedDuplicates} duplicate entries removed).`);
