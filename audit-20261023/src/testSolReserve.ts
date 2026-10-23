// Test SOL Reserve System
import { hasEnoughSOLForTrade, getAvailableSOL, getBalanceInfo, getSOLReserveAmount } from "./solReserve.js";
import { solReserveLamports } from "./config.js";
import { LAMPORTS_PER_SOL } from "@solana/web3.js";

function testSolReserveSystem() {
  console.log("🧪 Testing SOL Reserve System");
  console.log("=".repeat(40));
  
  const reserveAmount = getSOLReserveAmount();
  console.log(`⛽ SOL Reserve: ${reserveAmount} SOL (${solReserveLamports} lamports)`);
  
  // Test scenarios
  const scenarios = [
    {
      name: "High Balance (5 SOL)",
      balance: 5 * LAMPORTS_PER_SOL,
      tradeAmount: 1 * LAMPORTS_PER_SOL,
      expected: true
    },
    {
      name: "Exactly Reserve (0.1 SOL)",
      balance: 0.1 * LAMPORTS_PER_SOL,
      tradeAmount: 0.01 * LAMPORTS_PER_SOL,
      expected: false
    },
    {
      name: "Below Reserve (0.05 SOL)",
      balance: 0.05 * LAMPORTS_PER_SOL,
      tradeAmount: 0.01 * LAMPORTS_PER_SOL,
      expected: false
    },
    {
      name: "Just Above Reserve (0.15 SOL)",
      balance: 0.15 * LAMPORTS_PER_SOL,
      tradeAmount: 0.04 * LAMPORTS_PER_SOL,
      expected: true
    },
    {
      name: "Trade Would Exhaust Reserve (0.12 SOL)",
      balance: 0.12 * LAMPORTS_PER_SOL,
      tradeAmount: 0.05 * LAMPORTS_PER_SOL,
      expected: false
    }
  ];
  
  console.log("\n📊 Test Scenarios:");
  console.log("-".repeat(40));
  
  for (const scenario of scenarios) {
    const hasEnough = hasEnoughSOLForTrade(scenario.tradeAmount, scenario.balance);
    const balanceInfo = getBalanceInfo(scenario.balance);
    const available = getAvailableSOL(scenario.balance);
    
    console.log(`\n${scenario.name}:`);
    console.log(`   Balance: ${balanceInfo.total} SOL`);
    console.log(`   Available: ${balanceInfo.available} SOL`);
    console.log(`   Trade Amount: ${(scenario.tradeAmount / LAMPORTS_PER_SOL).toFixed(4)} SOL`);
    console.log(`   Expected: ${scenario.expected ? '✅ Allow' : '❌ Block'}`);
    console.log(`   Result: ${hasEnough ? '✅ Allow' : '❌ Block'}`);
    console.log(`   Status: ${hasEnough === scenario.expected ? '✅ PASS' : '❌ FAIL'}`);
  }
  
  // Test edge cases
  console.log("\n🔍 Edge Cases:");
  console.log("-".repeat(40));
  
  // Zero balance
  const zeroBalance = 0;
  console.log(`\nZero Balance:`);
  console.log(`   Balance: 0 SOL`);
  console.log(`   Available: ${getAvailableSOL(zeroBalance)} SOL`);
  console.log(`   Has Enough: ${hasEnoughSOLForTrade(0.01 * LAMPORTS_PER_SOL, zeroBalance) ? 'Yes' : 'No'}`);
  
  // Very large trade
  const largeBalance = 10 * LAMPORTS_PER_SOL;
  const largeTrade = 5 * LAMPORTS_PER_SOL;
  console.log(`\nLarge Trade:`);
  console.log(`   Balance: 10 SOL`);
  console.log(`   Available: ${getAvailableSOL(largeBalance)} SOL`);
  console.log(`   Trade Amount: 5 SOL`);
  console.log(`   Has Enough: ${hasEnoughSOLForTrade(largeTrade, largeBalance) ? 'Yes' : 'No'}`);
  
  console.log("\n✅ SOL Reserve System Test Complete!");
  console.log("\n💡 Key Benefits:");
  console.log("   • Prevents wallet from going to 0 SOL");
  console.log("   • Always keeps gas fees available");
  console.log("   • Fast cached balance checks");
  console.log("   • Configurable reserve amount");
  console.log("   • Telegram notifications for low balance");
}

testSolReserveSystem();
