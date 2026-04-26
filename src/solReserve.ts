// SOL Reserve System - Prevents wallet from going to 0 SOL for gas fees
// Fast balance checking with minimal latency impact

import { Connection, PublicKey, LAMPORTS_PER_SOL } from "@solana/web3.js";
import { solReserveLamports } from "./config.js";

// Cache balance to minimize RPC calls (updates every 10 seconds)
let cachedBalance: number = 0;
let lastBalanceCheck: number = 0;
const BALANCE_CACHE_MS = 10000; // 10 seconds

/**
 * Fast check if wallet has enough SOL after reserving gas fees
 * Uses cached balance for speed, only fetches fresh balance if cache is stale
 */
export function hasEnoughSOLForTrade(tradeAmountLamports: number, currentBalance?: number): boolean {
  // Use provided balance or cached balance
  const balance = currentBalance ?? cachedBalance;
  
  // Check if balance - trade amount - reserve >= 0
  const remainingAfterTrade = balance - tradeAmountLamports - solReserveLamports;
  
  return remainingAfterTrade >= 0;
}

/**
 * Get remaining SOL after reserving gas fees
 */
export function getAvailableSOL(currentBalance: number): number {
  const available = currentBalance - solReserveLamports;
  return Math.max(0, available);
}

/**
 * Fast balance update with caching
 * Only fetches fresh balance if cache is stale
 */
export async function updateBalanceCache(
  connection: Connection, 
  walletPublicKey: PublicKey,
  forceRefresh: boolean = false
): Promise<number> {
  const now = Date.now();
  
  // Use cached balance if fresh and not forced refresh
  if (!forceRefresh && cachedBalance > 0 && (now - lastBalanceCheck) < BALANCE_CACHE_MS) {
    return cachedBalance;
  }
  
  try {
    // Fetch fresh balance
    const freshBalance = await connection.getBalance(walletPublicKey, { commitment: "confirmed" });
    
    // Update cache
    cachedBalance = freshBalance;
    lastBalanceCheck = now;
    
    return freshBalance;
  } catch (error) {
    console.error("❌ Failed to update balance cache:", error);
    // Return cached balance even if stale
    return cachedBalance;
  }
}

/**
 * Get current reserve amount in SOL for display
 */
export function getSOLReserveAmount(): number {
  return solReserveLamports / LAMPORTS_PER_SOL;
}

/**
 * Check if wallet balance is critically low (below reserve + small buffer)
 */
export function isBalanceCriticallyLow(currentBalance: number): boolean {
  const buffer = solReserveLamports * 2; // 2x reserve as buffer
  return currentBalance < buffer;
}

/**
 * Get formatted balance info for logging/notifications
 */
export function getBalanceInfo(currentBalance: number): {
  total: string;
  available: string;
  reserved: string;
  isLow: boolean;
} {
  const totalSOL = currentBalance / LAMPORTS_PER_SOL;
  const availableSOL = getAvailableSOL(currentBalance) / LAMPORTS_PER_SOL;
  const reservedSOL = getSOLReserveAmount();
  const isLow = isBalanceCriticallyLow(currentBalance);
  
  return {
    total: totalSOL.toFixed(4),
    available: availableSOL.toFixed(4),
    reserved: reservedSOL.toFixed(4),
    isLow
  };
}
