import "dotenv/config";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";

function readEnvNumber(name: string): number {
  const raw = process.env[name];
  if (!raw) throw new Error(`${name} is not set in environment`);
  const n = parseInt(raw, 10);
  if (!Number.isFinite(n)) throw new Error(`${name} is not a number`);
  return n;
}

function readEnvString(name: string): string {
  const raw = process.env[name];
  if (!raw) throw new Error(`${name} is not set in environment`);
  return raw;
}

async function main(): Promise<void> {
  const apiId = readEnvNumber("TELEGRAM_API_ID");
  const apiHash = readEnvString("TELEGRAM_API_HASH");

  const session = new StringSession("");
  const client = new TelegramClient(session, apiId, apiHash, {
    connectionRetries: 5,
    useWSS: true,
  });

  const rl = readline.createInterface({ input, output });

  await client.start({
    phoneNumber: () => rl.question("Phone number (with country code, e.g. +49…): "),
    password: () => rl.question("2FA password (if set, else press Enter): "),
    phoneCode: () => rl.question("Login code from Telegram: "),
    onError: (err) => console.error("Login error:", err),
  });

  rl.close();

  const sessionString = client.session.save() as unknown as string;
  console.log("\n=== TELEGRAM_USER_SESSION ===");
  console.log(sessionString);
  console.log("=============================");
  console.log("\nPaste this into .env as TELEGRAM_USER_SESSION=<string>");
  console.log("Treat it like a password — full account access.");

  await client.disconnect();
  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
