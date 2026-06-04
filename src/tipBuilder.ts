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

export type TipSelection = "all" | "primary" | "fallback" | "none";

export type TipInstructionSet = {
  label: TipSelection;
  instructions: TransactionInstruction[];
};

function randomAccount(accounts: readonly string[]): PublicKey {
  return new PublicKey(accounts[Math.floor(Math.random() * accounts.length)]!);
}

function routeMultiplier(route?: string): number {
  if (!route) return 1;
  return executionTipMultipliers.get(route) ?? executionTipMultipliers.get(route.split("-")[0] ?? "") ?? 1;
}

function routeTipLamports(route?: string): number {
  return Math.max(0, Math.round(tipLamports * routeMultiplier(route)));
}

function jitoTip(user: PublicKey, route?: string): TransactionInstruction {
  return SystemProgram.transfer({
    fromPubkey: user,
    toPubkey: randomAccount(JITO_TIP_ACCOUNTS),
    lamports: routeTipLamports(route),
  });
}

function heliusTip(user: PublicKey, route?: string): TransactionInstruction {
  const routeLamports = routeTipLamports(route);
  const heliusLamports = useJitoDirect
    ? HELIUS_TIP_MIN_LAMPORTS
    : Math.max(routeLamports, HELIUS_TIP_MIN_LAMPORTS);
  return SystemProgram.transfer({
    fromPubkey: user,
    toPubkey: randomAccount(HELIUS_TIP_ACCOUNTS),
    lamports: heliusLamports,
  });
}

/**
 * Build tip transfer instructions for the active sender strategy. Default "all" preserves the
 * old behavior: Jito tip when Jito direct is enabled, plus a Helius-compatible tip when Helius
 * may be used as the primary or fallback sender.
 */
export function buildTipInstructions(
  user: PublicKey,
  options: { selection?: TipSelection; route?: string } = {}
): TransactionInstruction[] {
  const selection = options.selection ?? "all";
  if (selection === "none") return [];

  if (selection === "primary") {
    return [useJitoDirect ? jitoTip(user, options.route) : heliusTip(user, options.route)];
  }

  if (selection === "fallback") {
    return [
      useJitoDirect && useJitoFallback
        ? heliusTip(user, options.route)
        : useJitoDirect
          ? jitoTip(user, options.route)
          : heliusTip(user, options.route),
    ];
  }

  const ixs: TransactionInstruction[] = [];
  if (useJitoDirect) ixs.push(jitoTip(user, options.route));
  if (!useJitoDirect || useJitoFallback) ixs.push(heliusTip(user, options.route));
  return ixs;
}

export function buildTipInstructionSets(user: PublicKey, options: { route?: string } = {}): TipInstructionSet[] {
  const sets: TipInstructionSet[] = [
    { label: "all", instructions: buildTipInstructions(user, { selection: "all", route: options.route }) },
    { label: "primary", instructions: buildTipInstructions(user, { selection: "primary", route: options.route }) },
    { label: "fallback", instructions: buildTipInstructions(user, { selection: "fallback", route: options.route }) },
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
