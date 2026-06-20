// Test PnL calculation with your example scenario

import assert from "node:assert/strict";
import { getTotals, recordBuy, recordSell, resetPositions } from "./pnl.js";
import { TokenMeta } from "./tokenRegistry.js";

function testPnlCalculation() {
  console.log("🧮 Testing PnL Calculation");
  console.log("=".repeat(40));
  
  resetPositions();
  
  const tokenMeta: TokenMeta = {
    mint: "test",
    symbol: "DEGENS",
    name: "DEGENS",
    decimals: 6,
    isSol: false
  };
  
  // Scenario: Buy at 0.073518 SOL per token, sell at different price
  console.log("\n📊 Test Scenario:");
  console.log("-".repeat(30));
  
  // Buy 1M tokens at 0.073518 SOL each = 73.518 SOL
  const buySol = 73.518 * 1_000_000_000; // 73.518 SOL in lamports
  const tokensBought = 1_000_000; // 1M tokens
  
  console.log(`🟢 Buy: ${tokensBought / 1_000_000}M tokens at ${(buySol / 1_000_000_000).toFixed(4)} SOL each`);
  console.log(`   Total spent: ${(buySol / 1_000_000_000).toFixed(4)} SOL`);
  
  const buyResult = recordBuy(tokenMeta, tokensBought, buySol);
  console.log(`   Avg cost per token: ${((buyResult.avgCostLamportsPerTokenRaw || 0) / 1_000_000_000).toFixed(6)} SOL`);
  
  // Sell at 25.7% loss (price should be lower)
  const lossPct = -25.7;
  const sellPricePerToken = (buySol / tokensBought) * (1 + lossPct / 100);
  const sellSol = sellPricePerToken * tokensBought;
  
  console.log(`\n🔴 Sell: ${tokensBought / 1_000_000}M tokens at ${(sellPricePerToken / 1_000_000_000).toFixed(6)} SOL each`);
  console.log(`   Total received: ${(sellSol / 1_000_000_000).toFixed(4)} SOL`);
  console.log(`   Expected PnL: ${lossPct}% (${((sellSol - buySol) / 1_000_000_000).toFixed(4)} SOL)`);
  
  const sellResult = recordSell(tokenMeta, tokensBought, sellSol);
  console.log(`   Actual PnL: ${((sellResult.realizedPnlLamports) / 1_000_000_000).toFixed(4)} SOL`);
  
  // Now test a 20x gain scenario
  console.log("\n🚀 Testing 20x Gain Scenario:");
  console.log("-".repeat(30));
  
  resetPositions();
  
  // Buy 1 SOL worth of tokens
  const smallBuySol = 1_000_000_000; // 1 SOL
  const smallTokens = 10_000_000; // 10M tokens at 0.1 SOL each
  
  console.log(`🟢 Buy: ${smallTokens / 1_000_000}M tokens at 0.1 SOL each`);
  console.log(`   Total spent: 1.0000 SOL`);
  
  recordBuy(tokenMeta, smallTokens, smallBuySol);
  
  // 20x gain - tokens worth 20 SOL
  const gain20xSol = 20_000_000_000; // 20 SOL
  
  console.log(`🔴 Sell: ${smallTokens / 1_000_000}M tokens at 2.0 SOL each`);
  console.log(`   Total received: 20.0000 SOL`);
  console.log(`   Expected PnL: +19.0000 SOL (1900% gain)`);
  
  const gainResult = recordSell(tokenMeta, smallTokens, gain20xSol);
  console.log(`   Actual PnL: ${(gainResult.realizedPnlLamports / 1_000_000_000).toFixed(4)} SOL`);
  
  const actualPct = (gainResult.realizedPnlLamports / smallBuySol) * 100;
  console.log(`   Actual %: ${actualPct.toFixed(1)}%`);

  console.log("\nOversell accounting guard:");
  console.log("-".repeat(30));
  resetPositions();
  recordBuy(tokenMeta, 100, 1_000_000_000);
  assert.equal(getTotals().openCostBasisLamports, 1_000_000_000);
  const oversell = recordSell(tokenMeta, 150, 1_500_000_000);
  assert.equal(oversell.soldRawCosted, 100);
  assert.equal(oversell.soldRawUncosted, 50);
  assert.equal(oversell.realizedPnlLamports, 500_000_000);
  assert.equal(getTotals().realizedPnlLamports, 500_000_000);
  assert.equal(getTotals().openCostBasisLamports, 0);
  console.log("   Oversold raw is tracked as uncosted instead of fabricating extra cost basis.");
  
  console.log(`\n✅ PnL calculation working correctly: ${gainResult.realizedPnlLamports === 19_000_000_000 ? 'YES' : 'NO'}`);
}

testPnlCalculation();
