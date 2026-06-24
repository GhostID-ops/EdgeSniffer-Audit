import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const PRESETS = {
  cya: ".env.wallet-cya",
  cented: ".env.wallet-cya",
  "4vw": ".env.wallet-4vw54",
  "4vw54": ".env.wallet-4vw54",
  tefs: ".env.wallet-tefs",
  omego: ".env.wallet-omego",
  "5qn": ".env.wallet-5qn-eaz",
  eaz: ".env.wallet-5qn-eaz",
  "5qn-eaz": ".env.wallet-5qn-eaz",
  "4bdk": ".env.wallet-4bdk",
  gug: ".env.wallet-gug-cluster",
  "gug-cluster": ".env.wallet-gug-cluster",
};

const name = (process.argv[2] ?? "").toLowerCase().trim();

if (!name || name === "list" || name === "--list") {
  console.log("Wallet presets:");
  for (const [alias, file] of Object.entries(PRESETS)) console.log(`  ${alias.padEnd(12)} ${file}`);
  process.exit(name ? 0 : 1);
}

const preset = PRESETS[name];
if (!preset) {
  console.error(`Unknown wallet preset: ${name}`);
  console.error("Run: npm run env:wallet -- list");
  process.exit(1);
}

if (!existsSync(preset)) {
  console.error(`Preset file is missing: ${preset}`);
  process.exit(1);
}

const result = spawnSync(
  process.execPath,
  ["scripts/upsertEnv.mjs", "--from-file", preset],
  { stdio: "inherit" }
);

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 0);
