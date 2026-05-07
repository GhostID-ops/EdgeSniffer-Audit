import "dotenv/config";
import { Keypair, LAMPORTS_PER_SOL } from "@solana/web3.js";
import bs58 from "bs58";
import { getPrivateKeyBase58 } from "./config.js";
import { SnipeBot } from "./snipeBot.js";
import { recordSnipeBuy } from "./snipePositions.js";

const FAKE_MINT_A = "TestMintA1111111111111111111111111111111pump";
const FAKE_MINT_B = "TestMintB2222222222222222222222222222222pump";
const FAKE_SIGNATURE = "5".repeat(64);

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function main(): Promise<void> {
  const keypair = Keypair.fromSecretKey(bs58.decode(getPrivateKeyBase58()));

  const slippageBps = parseInt(
    process.env.SNIPE_SLIPPAGE_BPS ?? process.env.MIRROR_SLIPPAGE_BPS ?? "1500",
    10
  );

  const bot = new SnipeBot({
    keypair,
    slippageBps,
    statusFn: () => ({
      channelTitle: "TEST CHANNEL (not real)",
      buySol: 0.05,
      slippageBps,
      dryRun: true,
      minBoostAmount: 10,
      minTotalBoosts: 1,
      walletAddress: keypair.publicKey.toBase58(),
    }),
  });

  if (!bot.isEnabled()) {
    console.error(
      "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in .env — nothing to test."
    );
    process.exit(1);
  }

  // Seed two fake simulated positions so /positions and /sessionpnl have data.
  recordSnipeBuy(FAKE_MINT_A, 0.05 * LAMPORTS_PER_SOL, 1_234_567_890, "sim-buy", true);
  recordSnipeBuy(FAKE_MINT_B, 0.10 * LAMPORTS_PER_SOL, 9_876_543_210, "sim-buy", true);

  const steps = [
    {
      label: "header",
      run: () =>
        bot.send(
          "<b>=== SnipeBot test run ===</b>\n" +
            "Sending one of each notification. After this finishes, try " +
            "<code>/snipehelp</code>, <code>/snipestatus</code>, <code>/positions</code> " +
            "in this chat — fake positions are seeded so they'll respond.\n" +
            "<i>Note: /sell and /sellall would attempt real swaps. Don't run them on the test mints — they'll fail at Jupiter (no balance).</i>"
        ),
    },
    {
      label: "filtered",
      run: () => bot.notifyFiltered(FAKE_MINT_A, 5, 1),
    },
    {
      label: "dry_run",
      run: () => bot.notifyDryRun(FAKE_MINT_A, 0.05, "1234567890"),
    },
    {
      label: "buy_success",
      run: () =>
        bot.notifyBuy({
          mint: FAKE_MINT_A,
          spentLamports: 0.05 * LAMPORTS_PER_SOL,
          receivedRaw: "1234567890",
          signature: FAKE_SIGNATURE,
        }),
    },
    {
      label: "buy_failed",
      run: () =>
        bot.notifyBuyFailed(
          FAKE_MINT_B,
          "Jupiter quote failed: HTTP 400 No route found between SOL and mint (sample error)"
        ),
    },
  ];

  for (const step of steps) {
    console.log(`[test] sending: ${step.label}`);
    await step.run();
    await sleep(700);
  }

  console.log("");
  console.log("All notifications sent. Check your Telegram.");
  console.log(
    "Now starting the command poller — try /snipehelp, /snipestatus, /positions in your chat."
  );
  console.log("Press Ctrl+C to exit.");

  bot.start();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
