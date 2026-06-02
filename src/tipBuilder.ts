import {
  PublicKey,
  SystemProgram,
  type TransactionInstruction,
} from "@solana/web3.js";
import {
  JITO_TIP_ACCOUNTS,
  HELIUS_TIP_ACCOUNTS,
  HELIUS_TIP_MIN_LAMPORTS,
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

function jitoTip(user: PublicKey): TransactionInstruction {
  return SystemProgram.transfer({
    fromPubkey: user,
    toPubkey: randomAccount(JITO_TIP_ACCOUNTS),
    lamports: tipLamports,
  });
}

function heliusTip(user: PublicKey): TransactionInstruction {
  const heliusLamports = useJitoDirect
    ? HELIUS_TIP_MIN_LAMPORTS
    : Math.max(tipLamports, HELIUS_TIP_MIN_LAMPORTS);
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
  options: { selection?: TipSelection } = {}
): TransactionInstruction[] {
  const selection = options.selection ?? "all";
  if (selection === "none") return [];

  if (selection === "primary") {
    return [useJitoDirect ? jitoTip(user) : heliusTip(user)];
  }

  if (selection === "fallback") {
    return [useJitoDirect && useJitoFallback ? heliusTip(user) : useJitoDirect ? jitoTip(user) : heliusTip(user)];
  }

  const ixs: TransactionInstruction[] = [];
  if (useJitoDirect) ixs.push(jitoTip(user));
  if (!useJitoDirect || useJitoFallback) ixs.push(heliusTip(user));
  return ixs;
}

export function buildTipInstructionSets(user: PublicKey): TipInstructionSet[] {
  const sets: TipInstructionSet[] = [
    { label: "all", instructions: buildTipInstructions(user, { selection: "all" }) },
    { label: "primary", instructions: buildTipInstructions(user, { selection: "primary" }) },
    { label: "fallback", instructions: buildTipInstructions(user, { selection: "fallback" }) },
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
