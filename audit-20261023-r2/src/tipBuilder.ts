import {
  PublicKey,
  SystemProgram,
  type TransactionInstruction,
} from "@solana/web3.js";
import {
  JITO_TIP_ACCOUNTS,
  HELIUS_TIP_ACCOUNTS,
  HELIUS_TIP_MIN_LAMPORTS,
  executionTipMultipliers,
  tipLamports,
  useJitoDirect,
  useJitoFallback,
} from "./config.js";
import { getDynamicJitoTipLamports } from "./jitoTipOracle.js";

export type TipSelection = "all" | "primary" | "fallback" | "none";

export type TipInstructionSet = {
  label: TipSelection;
  instructions: TransactionInstruction[];
};

const DEFAULT_ROUTE_TIP_MULTIPLIERS = new Map<string, number>([
  ["pumpfun-source-replay-buy", 1.3],
  ["pumpfun-direct-buy", 1.2],
  ["pumpswap-direct-buy", 1.2],
  ["pumpfun-direct-sell", 1.0],
  ["pumpswap-direct-sell", 1.0],
  ["jupiter", 0.9],
]);

function randomAccount(accounts: readonly string[]): PublicKey {
  return new PublicKey(accounts[Math.floor(Math.random() * accounts.length)]!);
}

function routeMultiplier(route?: string): number {
  if (!route) return 1;
  const candidates = [
    route,
    route.replace(/-v\d+$/i, ""),
    route.includes("buy") ? "buy" : "",
    route.includes("sell") ? "sell" : "",
    route.split("-")[0] ?? "",
  ].filter(Boolean);
  for (const candidate of candidates) {
    const multiplier = executionTipMultipliers.get(candidate);
    if (multiplier != null) return multiplier;
  }
  for (const candidate of candidates) {
    const multiplier = DEFAULT_ROUTE_TIP_MULTIPLIERS.get(candidate);
    if (multiplier != null) return multiplier;
  }
  return 1;
}

function routeTipLamports(route?: string, baseLamports = tipLamports): number {
  return Math.max(0, Math.round(baseLamports * routeMultiplier(route)));
}

function isBuyRoute(route?: string): boolean {
  return (route ?? "").toLowerCase().includes("buy");
}

function isSellRoute(route?: string): boolean {
  return (route ?? "").toLowerCase().includes("sell");
}

function explicitNonNegativeLamports(...names: string[]): number | null {
  for (const name of names) {
    const raw = process.env[name];
    if (raw == null || raw.trim() === "") continue;
    const value = Number(raw);
    if (Number.isFinite(value)) return Math.max(0, Math.round(value));
  }
  return null;
}

function jitoTip(user: PublicKey, route?: string, overrideLamports?: number): TransactionInstruction | null {
  const lamports = overrideLamports == null
    ? routeTipLamports(route, getDynamicJitoTipLamports())
    : Math.max(0, Math.round(overrideLamports));
  if (lamports <= 0) return null;
  return SystemProgram.transfer({
    fromPubkey: user,
    toPubkey: randomAccount(JITO_TIP_ACCOUNTS),
    lamports,
  });
}

function heliusTip(user: PublicKey, route?: string, overrideLamports?: number): TransactionInstruction | null {
  if (overrideLamports != null) {
    const lamports = Math.max(0, Math.round(overrideLamports));
    if (lamports <= 0) return null;
    return SystemProgram.transfer({
      fromPubkey: user,
      toPubkey: randomAccount(HELIUS_TIP_ACCOUNTS),
      lamports,
    });
  }
  const routeLamports = routeTipLamports(route);
  if (routeLamports <= 0) return null;
  const heliusLamports = Math.max(routeLamports, HELIUS_TIP_MIN_LAMPORTS);
  return SystemProgram.transfer({
    fromPubkey: user,
    toPubkey: randomAccount(HELIUS_TIP_ACCOUNTS),
    lamports: heliusLamports,
  });
}

export function getJitoTipLamportsForRoute(route?: string): number {
  const dynamic = routeTipLamports(route, getDynamicJitoTipLamports());
  if (isBuyRoute(route)) {
    const buyFloor = explicitNonNegativeLamports("BUY_JITO_TIP_LAMPORTS");
    return Math.max(dynamic, buyFloor ?? 0);
  }
  if (isSellRoute(route)) {
    return explicitNonNegativeLamports("SELL_JITO_TIP_LAMPORTS") ?? 0;
  }
  return dynamic;
}

export function getHeliusTipLamportsForRoute(route?: string): number {
  if (isSellRoute(route)) {
    return explicitNonNegativeLamports("SELL_HELIUS_TIP_LAMPORTS") ?? 0;
  }
  if (isBuyRoute(route)) {
    const explicitBuy = explicitNonNegativeLamports(
      "BUY_HELIUS_TIP_LAMPORTS",
      "RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS",
      "HELIUS_TIP_LAMPORTS"
    );
    if (explicitBuy != null) return explicitBuy;
  }
  const explicit = explicitNonNegativeLamports("HELIUS_TIP_LAMPORTS");
  if (explicit != null) return explicit;
  const routeLamports = routeTipLamports(route);
  if (routeLamports <= 0) return 0;
  return Math.max(routeLamports, HELIUS_TIP_MIN_LAMPORTS);
}

/**
 * Build tip transfer instructions for the active sender strategy. Default "all" preserves the
 * old behavior: Jito tip when Jito direct is enabled, plus a Helius-compatible tip when Helius
 * may be used as the primary or fallback sender.
 */
export function buildTipInstructions(
  user: PublicKey,
  options: { selection?: TipSelection; route?: string; jitoTipLamports?: number; heliusTipLamports?: number } = {}
): TransactionInstruction[] {
  const selection = options.selection ?? "all";
  if (selection === "none") return [];

  if (selection === "primary") {
    const ix = useJitoDirect
      ? jitoTip(user, options.route, options.jitoTipLamports)
      : heliusTip(user, options.route, options.heliusTipLamports);
    return ix ? [ix] : [];
  }

  if (selection === "fallback") {
    const ix = useJitoDirect && useJitoFallback
      ? heliusTip(user, options.route, options.heliusTipLamports)
      : useJitoDirect
        ? jitoTip(user, options.route, options.jitoTipLamports)
        : heliusTip(user, options.route, options.heliusTipLamports);
    return ix ? [ix] : [];
  }

  const ixs: TransactionInstruction[] = [];
  const jitoIx = useJitoDirect ? jitoTip(user, options.route, options.jitoTipLamports) : null;
  const heliusIx = !useJitoDirect || useJitoFallback ? heliusTip(user, options.route, options.heliusTipLamports) : null;
  if (jitoIx) ixs.push(jitoIx);
  if (heliusIx) ixs.push(heliusIx);
  return ixs;
}

export function buildTipInstructionSets(
  user: PublicKey,
  options: { route?: string; jitoTipLamports?: number; heliusTipLamports?: number } = {}
): TipInstructionSet[] {
  const sets: TipInstructionSet[] = [
    { label: "all", instructions: buildTipInstructions(user, { selection: "all", route: options.route, jitoTipLamports: options.jitoTipLamports, heliusTipLamports: options.heliusTipLamports }) },
    { label: "primary", instructions: buildTipInstructions(user, { selection: "primary", route: options.route, jitoTipLamports: options.jitoTipLamports, heliusTipLamports: options.heliusTipLamports }) },
    { label: "fallback", instructions: buildTipInstructions(user, { selection: "fallback", route: options.route, jitoTipLamports: options.jitoTipLamports, heliusTipLamports: options.heliusTipLamports }) },
  ];

  const seen = new Set<string>();
  return sets.filter((set) => {
    const key = `${set.instructions.length}:${set.instructions
      .map((ix) => `${ix.programId.toBase58()}:${ix.keys.map((k) => k.pubkey.toBase58()).join(",")}`)
      .join("|")}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
