import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { PublicKey, TransactionInstruction, type AccountMeta } from "@solana/web3.js";

export type IdlProgramName = "pump" | "pump_amm";

type IdlAccount = {
  name: string;
  writable?: boolean;
  signer?: boolean;
};

type IdlInstruction = {
  name: string;
  discriminator: number[];
  accounts: IdlAccount[];
};

type Idl = {
  address: string;
  instructions: IdlInstruction[];
};

const __dirname = dirname(fileURLToPath(import.meta.url));

const IDL_FILES: Record<IdlProgramName, string> = {
  pump: join(__dirname, "idl", "pump.json"),
  pump_amm: join(__dirname, "idl", "pump_amm.json"),
};

const idlCache = new Map<IdlProgramName, Idl>();

export function getNativeIdl(program: IdlProgramName): Idl {
  const cached = idlCache.get(program);
  if (cached) return cached;
  const parsed = JSON.parse(readFileSync(IDL_FILES[program], "utf8")) as Idl;
  idlCache.set(program, parsed);
  return parsed;
}

export function getNativeIdlInstruction(
  program: IdlProgramName,
  name: string
): IdlInstruction {
  const ix = getNativeIdl(program).instructions.find((item) => item.name === name);
  if (!ix) throw new Error(`IDL instruction not found: ${program}.${name}`);
  return ix;
}

export function getNativeIdlDiscriminator(program: IdlProgramName, name: string): Buffer {
  return Buffer.from(getNativeIdlInstruction(program, name).discriminator);
}

export function assertIdlAccountOrder(
  program: IdlProgramName,
  name: string,
  actualNames: string[]
): void {
  const expected = getNativeIdlInstruction(program, name).accounts.map((account) => account.name);
  if (expected.join("|") !== actualNames.join("|")) {
    throw new Error(
      `IDL account order mismatch for ${program}.${name}: expected ${expected.join("|")} got ${actualNames.join("|")}`
    );
  }
}

export function buildNativeIdlInstruction(args: {
  program: IdlProgramName;
  instruction: string;
  programId?: PublicKey;
  accounts: Record<string, PublicKey>;
  data: Buffer;
}): TransactionInstruction {
  const idl = getNativeIdl(args.program);
  const ix = getNativeIdlInstruction(args.program, args.instruction);
  const keys: AccountMeta[] = ix.accounts.map((account) => {
    const pubkey = args.accounts[account.name];
    if (!pubkey) {
      throw new Error(`Missing account ${account.name} for ${args.program}.${args.instruction}`);
    }
    return {
      pubkey,
      isSigner: account.signer === true,
      isWritable: account.writable === true,
    };
  });
  return new TransactionInstruction({
    programId: args.programId ?? new PublicKey(idl.address),
    keys,
    data: Buffer.concat([Buffer.from(ix.discriminator), args.data]),
  });
}

