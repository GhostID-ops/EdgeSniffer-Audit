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

export function assertIdlAccountMetas(
  program: IdlProgramName,
  name: string,
  metas: AccountMeta[],
  options: { allowExtraAccounts?: boolean } = {}
): void {
  const expected = getNativeIdlInstruction(program, name).accounts;
  if (metas.length < expected.length || (!options.allowExtraAccounts && metas.length !== expected.length)) {
    throw new Error(
      `IDL account count mismatch for ${program}.${name}: expected ${
        options.allowExtraAccounts ? `at least ${expected.length}` : expected.length
      } got ${metas.length}`
    );
  }

  for (let i = 0; i < expected.length; i++) {
    const expectedAccount = expected[i]!;
    const actual = metas[i]!;
    if (actual.isSigner !== (expectedAccount.signer === true)) {
      throw new Error(
        `IDL signer mismatch for ${program}.${name}.${expectedAccount.name} at ${i}: ` +
          `expected ${expectedAccount.signer === true} got ${actual.isSigner}`
      );
    }
    if (actual.isWritable !== (expectedAccount.writable === true)) {
      throw new Error(
        `IDL writable mismatch for ${program}.${name}.${expectedAccount.name} at ${i}: ` +
          `expected ${expectedAccount.writable === true} got ${actual.isWritable}`
      );
    }
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

export function buildNativeIdlInstructionFromOrderedAccounts(args: {
  program: IdlProgramName;
  instruction: string;
  accounts: Array<PublicKey | AccountMeta>;
  data: Buffer;
  allowExtraAccounts?: boolean;
}): TransactionInstruction {
  const idl = getNativeIdl(args.program);
  const idlIx = getNativeIdlInstruction(args.program, args.instruction);
  if (args.accounts.length < idlIx.accounts.length) {
    throw new Error(
      `IDL account list too short for ${args.program}.${args.instruction}: expected ${idlIx.accounts.length} got ${args.accounts.length}`
    );
  }
  if (!args.allowExtraAccounts && args.accounts.length !== idlIx.accounts.length) {
    throw new Error(
      `IDL account count mismatch for ${args.program}.${args.instruction}: expected ${idlIx.accounts.length} got ${args.accounts.length}`
    );
  }

  const getPubkey = (entry: PublicKey | AccountMeta): PublicKey =>
    entry instanceof PublicKey ? entry : entry.pubkey;

  const keys: AccountMeta[] = idlIx.accounts.map((account, i) => ({
    pubkey: getPubkey(args.accounts[i]!),
    isSigner: account.signer === true,
    isWritable: account.writable === true,
  }));

  for (const extra of args.accounts.slice(idlIx.accounts.length)) {
    if (extra instanceof PublicKey) {
      keys.push({ pubkey: extra, isSigner: false, isWritable: false });
    } else {
      keys.push({ ...extra });
    }
  }

  return new TransactionInstruction({
    programId: new PublicKey(idl.address),
    keys,
    data: args.data,
  });
}

export function assertNativeIdlInstruction(args: {
  program: IdlProgramName;
  instruction: string;
  ix: TransactionInstruction;
  allowExtraAccounts?: boolean;
}): void {
  const idl = getNativeIdl(args.program);
  const idlIx = getNativeIdlInstruction(args.program, args.instruction);
  const programId = new PublicKey(idl.address);
  if (!args.ix.programId.equals(programId)) {
    throw new Error(
      `IDL program mismatch for ${args.program}.${args.instruction}: expected ${programId.toBase58()} got ${args.ix.programId.toBase58()}`
    );
  }

  const discriminator = Buffer.from(idlIx.discriminator);
  if (
    args.ix.data.length < discriminator.length ||
    !args.ix.data.subarray(0, discriminator.length).equals(discriminator)
  ) {
    throw new Error(`IDL discriminator mismatch for ${args.program}.${args.instruction}`);
  }

  if (args.ix.keys.length < idlIx.accounts.length) {
    throw new Error(
      `IDL account list too short for ${args.program}.${args.instruction}: expected ${idlIx.accounts.length} got ${args.ix.keys.length}`
    );
  }
  if (!args.allowExtraAccounts && args.ix.keys.length !== idlIx.accounts.length) {
    throw new Error(
      `IDL account count mismatch for ${args.program}.${args.instruction}: expected ${idlIx.accounts.length} got ${args.ix.keys.length}`
    );
  }

  for (let i = 0; i < idlIx.accounts.length; i++) {
    const expected = idlIx.accounts[i]!;
    const actual = args.ix.keys[i]!;
    const expectedSigner = expected.signer === true;
    const expectedWritable = expected.writable === true;
    if (actual.isSigner !== expectedSigner) {
      throw new Error(
        `IDL signer flag mismatch for ${args.program}.${args.instruction}.${expected.name} at index ${i}: expected ${expectedSigner} got ${actual.isSigner}`
      );
    }
    if (actual.isWritable !== expectedWritable) {
      throw new Error(
        `IDL writable flag mismatch for ${args.program}.${args.instruction}.${expected.name} at index ${i}: expected ${expectedWritable} got ${actual.isWritable}`
      );
    }
  }
}
