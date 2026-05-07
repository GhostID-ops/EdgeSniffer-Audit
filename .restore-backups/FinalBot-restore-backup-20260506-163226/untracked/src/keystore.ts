import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createCipheriv, createDecipheriv, randomBytes, scryptSync } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Keypair } from "@solana/web3.js";
import bs58 from "bs58";
import { getPrivateKeyBase58 } from "./config.js";

const here = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(here, "..", ".env");
const KEYSTORE_VERSION = 1;
const KDF = "scrypt";
const CIPHER = "aes-256-gcm";

type KeystorePayload = {
  version: number;
  kdf: typeof KDF;
  cipher: typeof CIPHER;
  createdAt: string;
  publicKey: string;
  salt: string;
  iv: string;
  tag: string;
  ciphertext: string;
};

export type KeystoreBackupResult = {
  publicKey: string;
  backup: string;
};

function assertMasterSecret(masterSecret: string): void {
  if (!masterSecret || masterSecret.length < 8) {
    throw new Error("Master secret must be at least 8 characters.");
  }
}

function keyFromSecret(masterSecret: string, salt: Buffer): Buffer {
  return scryptSync(masterSecret, salt, 32);
}

function keypairFromPrivateKey(privateKeyBase58: string): Keypair {
  try {
    return Keypair.fromSecretKey(bs58.decode(privateKeyBase58));
  } catch {
    throw new Error("Decrypted PRIVATE_KEY_BASE58 is not a valid Solana secret key.");
  }
}

function parseBackup(value: unknown): KeystorePayload {
  const raw = typeof value === "string" ? value : JSON.stringify(value);
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("Backup must be valid keystore JSON.");
  }

  const payload = parsed as Partial<KeystorePayload>;
  if (
    payload.version !== KEYSTORE_VERSION ||
    payload.kdf !== KDF ||
    payload.cipher !== CIPHER ||
    typeof payload.salt !== "string" ||
    typeof payload.iv !== "string" ||
    typeof payload.tag !== "string" ||
    typeof payload.ciphertext !== "string"
  ) {
    throw new Error("Backup format is not supported.");
  }
  return payload as KeystorePayload;
}

function upsertEnvValue(name: string, value: string): void {
  const escaped = value.replace(/\r?\n/g, "");
  const line = `${name}=${escaped}`;
  const current = existsSync(envPath) ? readFileSync(envPath, "utf8") : "";
  const rows = current.split(/\r?\n/);
  let replaced = false;
  const next = rows.map((row) => {
    if (row.startsWith(`${name}=`)) {
      replaced = true;
      return line;
    }
    return row;
  });

  if (!replaced) {
    if (next.length && next[next.length - 1] !== "") next.push("");
    next.push(line);
  }

  writeFileSync(envPath, next.join("\n"), "utf8");
  process.env[name] = escaped;
}

export function createKeystoreBackup(masterSecret: string): KeystoreBackupResult {
  assertMasterSecret(masterSecret);
  const privateKeyBase58 = getPrivateKeyBase58();
  const keypair = keypairFromPrivateKey(privateKeyBase58);
  const salt = randomBytes(16);
  const iv = randomBytes(12);
  const key = keyFromSecret(masterSecret, salt);
  const cipher = createCipheriv(CIPHER, key, iv);
  const ciphertext = Buffer.concat([
    cipher.update(privateKeyBase58, "utf8"),
    cipher.final(),
  ]);
  const payload: KeystorePayload = {
    version: KEYSTORE_VERSION,
    kdf: KDF,
    cipher: CIPHER,
    createdAt: new Date().toISOString(),
    publicKey: keypair.publicKey.toBase58(),
    salt: salt.toString("base64"),
    iv: iv.toString("base64"),
    tag: cipher.getAuthTag().toString("base64"),
    ciphertext: ciphertext.toString("base64"),
  };

  return {
    publicKey: payload.publicKey,
    backup: JSON.stringify(payload, null, 2),
  };
}

export function restoreKeystoreBackup(
  masterSecret: string,
  backup: unknown
): { publicKey: string } {
  assertMasterSecret(masterSecret);
  const payload = parseBackup(backup);
  const salt = Buffer.from(payload.salt, "base64");
  const iv = Buffer.from(payload.iv, "base64");
  const tag = Buffer.from(payload.tag, "base64");
  const ciphertext = Buffer.from(payload.ciphertext, "base64");
  const key = keyFromSecret(masterSecret, salt);
  const decipher = createDecipheriv(CIPHER, key, iv);
  decipher.setAuthTag(tag);

  let privateKeyBase58: string;
  try {
    privateKeyBase58 = Buffer.concat([
      decipher.update(ciphertext),
      decipher.final(),
    ]).toString("utf8");
  } catch {
    throw new Error("Backup could not be decrypted. Check the master secret.");
  }

  const keypair = keypairFromPrivateKey(privateKeyBase58);
  upsertEnvValue("PRIVATE_KEY_BASE58", privateKeyBase58);
  return { publicKey: keypair.publicKey.toBase58() };
}
