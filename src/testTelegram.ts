import { telegram } from "./telegram.js";

async function testTelegram() {
  console.log("🧪 Testing Telegram integration...");
  
  // Test connection
  const connected = await telegram.testConnection();
  if (connected) {
    console.log("✅ Telegram connection successful!");
  } else {
    console.log("❌ Telegram connection failed. Check your bot token and chat ID.");
    return;
  }

  // Test trade notification
  await telegram.notifyTrade({
    type: "buy",
    symbol: "TEST",
    amount: "1,000,000 TEST",
    solAmount: "0.1 SOL",
    sourceAmount: "2,500,000 TEST",
    sourceSolAmount: "0.25 SOL",
    marketCap: "$50K",
    isSimulated: true,
    wallet: "abc…",
    tokenAddress: "7xKj9...",
    txSignature: "test123"
  });

  // Test PnL notification
  await telegram.notifyPnlReport({
    totalTrades: 5,
    totalBuys: 3,
    totalSells: 2,
    realizedPnl: "+0.025 SOL",
    unrealizedPnl: "+0.010 SOL",
    totalPnl: "+0.035 SOL",
    winRate: "60.0%",
    avgWin: "+0.020 SOL",
    avgLoss: "-0.005 SOL",
    openPositions: 1
  });

  console.log("🎉 Telegram test complete! Check your Telegram for messages.");
}

// Run test if this file is executed directly
if (import.meta.url === `file://${process.argv[1]}`) {
  testTelegram().catch(console.error);
}
