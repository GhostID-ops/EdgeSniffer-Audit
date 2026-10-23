import { PublicKey } from "@solana/web3.js";
import { readConnection, warmWalletMode } from "./config.js";
import { WRAPPED_SOL_MINT } from "./tokenRegistry.js";
import { TOKEN_PROGRAM_ID, deriveAta } from "./pumpfun/constants.js";

const ACCOUNT_EXISTS_TTL_MS = 20_000;
const accountExistsCache = new Map<string, { exists: boolean; cachedAt: number }>();

export function noteAccountExists(pubkey: PublicKey, exists: boolean): void {
  accountExistsCache.set(pubkey.toBase58(), { exists, cachedAt: Date.now() });
}

export function knownAccountExists(pubkey: PublicKey): boolean {
  const cached = accountExistsCache.get(pubkey.toBase58());
  return cached?.exists === true;
}

export async function accountExistsCached(pubkey: PublicKey): Promise<boolean> {
  if (!warmWalletMode) return false;
  const key = pubkey.toBase58();
  const cached = accountExistsCache.get(key);
  const now = Date.now();
  if (cached && now - cached.cachedAt < ACCOUNT_EXISTS_TTL_MS) return cached.exists;
  const account = await readConnection.getAccountInfo(pubkey, "processed");
  const exists = account != null;
  accountExistsCache.set(key, { exists, cachedAt: now });
  return exists;
}

export async function warmWalletAccounts(user: PublicKey): Promise<void> {
  if (!warmWalletMode) return;
  const wsolAta = deriveAta(user, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID);
  await accountExistsCached(wsolAta).catch(() => false);
}
