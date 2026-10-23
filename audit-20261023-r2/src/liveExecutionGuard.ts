import path from "node:path";
import { LAMPORTS_PER_SOL } from "@solana/web3.js";
import {
  copyBuyPercentage,
  jupiterFallbackEnabled,
  maxBuyLamports,
  minBuyBehavior,
  minBuyLamports,
  rustOnlyBuys,
  rustOnlySells,
  shadowExecutionMode,
  simulationMode,
  trackedWallets,
} from "./config.js";

/**
 * Hard live-execution safety guard.
 *
 * Root cause it defends against: FinalBot-cya was launched (via
 * `finalbot-cya.service` = `npm run bot`, or a manual `npm run bot`) from a working
 * directory whose `.env` carried MIN_BUY_SOL=MAX_BUY_SOL=0.1, COPY_BUY_PERCENTAGE=0,
 * MIN_BUY_BEHAVIOR=floor and SHADOW_EXECUTION_MODE=false. Nothing in the startup path
 * required an explicit acknowledgement of live mode or a sane size cap, so it silently
 * fired fixed 0.1 SOL live bets.
 *
 * This function runs at the single startBot() chokepoint (reached by copytrade.ts, the
 * dashboard /api/start, and demoInstance.ts via web.ts), prints the loaded env path and
 * the resolved buy sizing, and - only when execution is actually live - refuses to start
 * unless the operator has explicitly confirmed live mode and kept sizing under a cap.
 *
 * It throws (rather than process.exit) so the dashboard surfaces the reason as a 400 and
 * stays up, while copytrade.ts's own catch turns the throw into a non-zero exit. No
 * network calls - pure synchronous config validation, safe on the hot path.
 */

const DEFAULT_MAX_ALLOWED_BUY_SOL = 0.01;

function isYes(value: string | undefined): boolean {
  const v = (value ?? "").trim().toLowerCase();
  return v === "yes" || v === "true" || v === "1" || v === "on";
}

/** Best-effort reconstruction of the .env path that `import "dotenv/config"` loaded. */
function loadedEnvPath(): string {
  const custom = process.env.DOTENV_CONFIG_PATH;
  return custom ? path.resolve(custom) : path.resolve(process.cwd(), ".env");
}

export type LiveExecutionSummary = {
  live: boolean;
  envPath: string;
  cwd: string;
  minBuySol: number;
  maxBuySol: number;
  copyBuyPercentage: number;
  minBuyBehavior: string;
  capSol: number;
  trackedWallets: number;
  sessionId: string;
};

export function resolveLiveExecutionSummary(): LiveExecutionSummary {
  const capRaw = parseFloat(process.env.COPYTRADE_MAX_ALLOWED_BUY_SOL ?? "");
  const capSol = Number.isFinite(capRaw) && capRaw > 0 ? capRaw : DEFAULT_MAX_ALLOWED_BUY_SOL;
  const sessionId = (
    process.env.LIVE_SESSION_ID ??
    process.env.LIVE_PROFILE_ID ??
    process.env.COPYTRADE_LIVE_SESSION ??
    ""
  ).trim();
  return {
    live: !shadowExecutionMode && !simulationMode,
    envPath: loadedEnvPath(),
    cwd: process.cwd(),
    minBuySol: minBuyLamports / LAMPORTS_PER_SOL,
    maxBuySol: maxBuyLamports / LAMPORTS_PER_SOL,
    copyBuyPercentage,
    minBuyBehavior,
    capSol,
    trackedWallets: trackedWallets.length,
    sessionId,
  };
}

/**
 * Validate execution safety and print the startup banner. Throws with an actionable
 * message when live execution is requested without the required explicit acknowledgements.
 */
export function assertLiveExecutionSafe(): void {
  const s = resolveLiveExecutionSummary();
  const mode = simulationMode ? "SIMULATION" : shadowExecutionMode ? "SHADOW (paper)" : "LIVE";

  console.log("--------------------------------------------------------------");
  console.log(" Copytrade execution guard");
  console.log(`   mode:            ${mode}`);
  console.log(`   cwd:             ${s.cwd}`);
  console.log(`   env file:        ${s.envPath}`);
  console.log(`   MIN_BUY_SOL:     ${s.minBuySol}`);
  console.log(`   MAX_BUY_SOL:     ${s.maxBuySol}`);
  console.log(`   COPY_BUY_%:      ${s.copyBuyPercentage}`);
  console.log(`   MIN_BUY_BEHAVIOR:${s.minBuyBehavior}`);
  console.log(`   allowed cap:     ${s.capSol} SOL (COPYTRADE_MAX_ALLOWED_BUY_SOL)`);
  console.log(`   tracked wallets: ${s.trackedWallets}`);
  console.log(`   jupiter fallback:${jupiterFallbackEnabled ? "ENABLED" : "disabled"}`);
  console.log(`   rust-only b/s:   ${rustOnlyBuys ? "buys" : "-"}/${rustOnlySells ? "sells" : "-"}`);
  console.log(`   live session id: ${s.sessionId || "(none)"}`);
  console.log("--------------------------------------------------------------");

  if (!s.live) {
    // Shadow / simulation: no money at risk, no confirmation required.
    return;
  }

  const allowLarge = isYes(process.env.COPYTRADE_ALLOW_LARGE_BUY);
  const failures: string[] = [];

  if (!isYes(process.env.CONFIRM_COPYTRADE_LIVE)) {
    failures.push(
      "live execution requires CONFIRM_COPYTRADE_LIVE=yes (explicit live acknowledgement is missing)"
    );
  }

  if ((s.minBuySol > s.capSol || s.maxBuySol > s.capSol) && !allowLarge) {
    failures.push(
      `buy sizing exceeds the allowed cap: MIN_BUY_SOL=${s.minBuySol}, MAX_BUY_SOL=${s.maxBuySol} > ` +
        `COPYTRADE_MAX_ALLOWED_BUY_SOL=${s.capSol}. Lower the sizes, or set COPYTRADE_ALLOW_LARGE_BUY=yes to override.`
    );
  }

  if (s.copyBuyPercentage === 0 && s.minBuySol > s.capSol && !allowLarge) {
    failures.push(
      `fixed-size live betting detected: COPY_BUY_PERCENTAGE=0 with MIN_BUY_SOL=${s.minBuySol} > cap ${s.capSol}. ` +
        `This is the exact 0.1 SOL fixed-bet failure mode. Set COPYTRADE_ALLOW_LARGE_BUY=yes only if this is intentional.`
    );
  }

  if (!s.sessionId) {
    failures.push(
      "SHADOW_EXECUTION_MODE=false but no named live session was provided. " +
        "Set LIVE_SESSION_ID (or LIVE_PROFILE_ID) so live runs are labelled and auditable."
    );
  }

  if (failures.length > 0) {
    const message = [
      "REFUSING TO START LIVE COPYTRADING - safety guard tripped:",
      ...failures.map((f, i) => `  ${i + 1}. ${f}`),
      "",
      `env file: ${s.envPath}`,
      "To run a genuine live session set, at minimum:",
      "  CONFIRM_COPYTRADE_LIVE=yes",
      "  LIVE_SESSION_ID=<name>",
      "  MIN_BUY_SOL / MAX_BUY_SOL <= COPYTRADE_MAX_ALLOWED_BUY_SOL (or COPYTRADE_ALLOW_LARGE_BUY=yes)",
    ].join("\n");
    throw new Error(message);
  }

  console.log(
    `Live-execution guard PASSED: session="${s.sessionId}", ` +
      `sizing ${s.minBuySol}-${s.maxBuySol} SOL within cap ${s.capSol}` +
      (allowLarge ? " (large-buy override active)" : "") +
      "."
  );
}
