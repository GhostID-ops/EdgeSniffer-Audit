// PnL Calculation Debug Script
// This shows exactly how PnL is calculated for copytrading

import { recordBuy, recordSell, getPositions, resetPositions } from "./pnl.js";
import { TokenMeta } from "./tokenRegistry.js";

function formatSol(lamports: number): string {
  return (lamports / 1_000_000_000).toFixed(4);
}

function debugPnlScenario() {
  console.log("🔍 PnL Calculation Debug");
  console.log("=".repeat(50));
  
  resetPositions();
  
  // Example: Source wallet buys 3 SOL, copytrader buys 1 SOL
  const tokenMeta: TokenMeta = {
    mint: "7xKj9...",
    symbol: "TOKEN",
    name: "Token",
    decimals: 6,
    isSol: false
  };
  
  console.log("\n📊 SCENARIO: Source 3 SOL → Copytrader 1 SOL");
  console.log("-".repeat(50));
  
  // Copytrader buys 1 SOL worth of tokens
  const copytraderBuySol = 1_000_000_000; // 1 SOL
  const tokensReceived = 10_000_000; // 10M tokens (at 0.1 SOL per token)
  
  console.log(`🟢 COPYTRADER BUY: ${formatSol(copytraderBuySol)} SOL → ${tokensReceived / 1_000_000}M tokens`);
  
  const buyResult = recordBuy(tokenMeta, tokensReceived, copytraderBuySol);
  console.log(`📈 Position after buy:`);
  console.log(`   - Tokens held: ${buyResult.heldRaw / 1_000_000}M`);
  console.log(`   - SOL spent: ${formatSol(buyResult.totalSolSpentLamports)} SOL`);
  console.log(`   - Avg cost per token: ${formatSol(buyResult.avgCostLamportsPerTokenRaw || 0)} SOL`);
  
  // Token 20x: 10M tokens now worth 20 SOL
  const tokenValue20x = 20_000_000_000; // 20 SOL
  const sellTokens = 10_000_000; // Sell all tokens
  
  console.log(`\n🚀 TOKEN 20x: ${sellTokens / 1_000_000}M tokens now worth ${formatSol(tokenValue20x)} SOL`);
  
  // Copytrader sells all tokens
  console.log(`🔴 COPYTRADER SELL: ${sellTokens / 1_000_000}M tokens → ${formatSol(tokenValue20x)} SOL`);
  
  const sellResult = recordSell(tokenMeta, sellTokens, tokenValue20x);
  console.log(`📊 Position after sell:`);
  console.log(`   - Tokens held: ${sellResult.heldRaw / 1_000_000}M`);
  console.log(`   - SOL received: ${formatSol(sellResult.totalSolReceivedLamports)} SOL`);
  console.log(`   - SOL spent: ${formatSol(sellResult.totalSolSpentLamports)} SOL`);
  console.log(`   - Realized PnL: ${formatSol(sellResult.realizedPnlLamports)} SOL`);
  
  // Show the math
  console.log(`\n🧮 PnL CALCULATION:`);
  console.log(`   SOL Received: ${formatSol(sellResult.totalSolReceivedLamports)} SOL`);
  console.log(`   SOL Spent:    ${formatSol(sellResult.totalSolSpentLamports)} SOL`);
  console.log(`   Net PnL:      ${formatSol(sellResult.totalSolReceivedLamports - sellResult.totalSolSpentLamports)} SOL`);
  console.log(`   PnL %:        ${((sellResult.totalSolReceivedLamports - sellResult.totalSolSpentLamports) / sellResult.totalSolSpentLamports * 100).toFixed(1)}%`);
  
  // Show total portfolio PnL
  const positions = getPositions();
  const totalPnl = positions.reduce((sum, pos) => sum + pos.realizedPnlLamports, 0);
  
  console.log(`\n💰 PORTFOLIO SUMMARY:`);
  console.log(`   Total Realized PnL: ${formatSol(totalPnl)} SOL`);
  console.log(`   Expected PnL:      ${formatSol(tokenValue20x - copytraderBuySol)} SOL`);
  console.log(`   Match: ${totalPnl === tokenValue20x - copytraderBuySol ? '✅' : '❌'}`);
}

debugPnlScenario();
