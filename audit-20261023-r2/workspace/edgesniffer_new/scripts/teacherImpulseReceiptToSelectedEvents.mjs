#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const receiptFile = process.env.TEACHER_IMPULSE_RECEIPT_SOURCE
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1/children/mc5000/decision-receipts.jsonl";
const inputFile = process.env.TEACHER_IMPULSE_DECISION_INPUT_SOURCE
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1/decision-inputs.jsonl";
const outputFile = process.env.TEACHER_IMPULSE_SELECTED_EVENT_OUTPUT
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-mc5000-selected-events.jsonl";
const lane = process.env.TEACHER_IMPULSE_SELECTED_EVENT_LANE
  || "teacher_impulse_mc5000";
const policy = process.env.TEACHER_IMPULSE_SELECTED_EVENT_POLICY
  || "teacher_impulse_mc5000_tp20_sl20_dust_calibration";
const pollMs = clampInt(process.env.TEACHER_IMPULSE_SELECTED_EVENT_POLL_MS, 10, 1000, 25);
const startAtEnd = parseBool(process.env.TEACHER_IMPULSE_SELECTED_EVENT_START_AT_END, true);
const maxInputRows = clampInt(process.env.TEACHER_IMPULSE_SELECTED_EVENT_MAX_INPUT_ROWS, 1000, 500000, 100000);
const maxPendingReceipts = clampInt(process.env.TEACHER_IMPULSE_SELECTED_EVENT_MAX_PENDING_RECEIPTS, 10, 50000, 5000);
const maxExecutionTemplateRows = clampInt(process.env.TEACHER_IMPULSE_SELECTED_EVENT_MAX_EXECUTION_TEMPLATE_ROWS, 1000, 500000, 100000);
const maxExecutionTemplateAgeMs = clampInt(process.env.TEACHER_IMPULSE_SELECTED_EVENT_MAX_EXECUTION_TEMPLATE_AGE_MS, 100, 120000, 10000);
const maxExecutionTemplateWaitMs = clampInt(process.env.TEACHER_IMPULSE_SELECTED_EVENT_MAX_EXECUTION_TEMPLATE_WAIT_MS, 0, 5000, 100);
const maxReadBytesPerPoll = clampInt(process.env.TEACHER_IMPULSE_SELECTED_EVENT_MAX_READ_BYTES_PER_POLL, 65536, 64 * 1024 * 1024, 8 * 1024 * 1024);
const statusEveryMs = clampInt(process.env.TEACHER_IMPULSE_SELECTED_EVENT_STATUS_MS, 1000, 300000, 30000);

fs.mkdirSync(path.dirname(outputFile), { recursive: true });
fs.closeSync(fs.openSync(outputFile, "a"));

const inputByRawIdentity = new Map();
const inputOrder = [];
const executionTemplateByMint = new Map();
const executionTemplateOrder = [];
const pendingReceipts = new Map();
const pendingExecutionReceipts = new Map();
const emittedKeys = new Set();

let inputOffset = startOffset(inputFile, startAtEnd);
let receiptOffset = startOffset(receiptFile, startAtEnd);
let lastStatusAt = 0;
let stats = {
  inputRows: 0,
  receiptRows: 0,
  acceptedReceipts: 0,
  emitted: 0,
  pending: 0,
  missingInput: 0,
  pendingExecution: 0,
  skipped: 0,
  recoveredExecutionFields: 0,
  executionTemplateTimeouts: 0,
};

console.log(JSON.stringify({
  event: "teacher_impulse_receipt_to_selected_events_started",
  atMs: Date.now(),
  receiptFile,
  inputFile,
  outputFile,
  lane,
  policy,
  pollMs,
  startAtEnd,
}));

while (true) {
  inputOffset = processNewLines(inputFile, inputOffset, handleInputLine);
  receiptOffset = processNewLines(receiptFile, receiptOffset, handleReceiptLine);
  flushPending();
  flushPendingExecution();
  emitStatus(false);
  await sleep(pollMs);
}

function handleInputLine(line) {
  let row;
  try {
    row = JSON.parse(line);
  } catch {
    stats.skipped++;
    return;
  }
  const raw = row?.provider?.raw_record_identity;
  if (!raw) return;
  inputByRawIdentity.set(raw, row);
  inputOrder.push(raw);
  rememberExecutionTemplate(row);
  stats.inputRows++;
  while (inputOrder.length > maxInputRows) {
    const old = inputOrder.shift();
    if (old && inputByRawIdentity.get(old)?.provider?.raw_record_identity === old) {
      inputByRawIdentity.delete(old);
    }
  }
  const pending = pendingReceipts.get(raw);
  if (pending) {
    pendingReceipts.delete(raw);
    emitSelectedIfReady(pending, row);
  }
}

function handleReceiptLine(line) {
  let receipt;
  try {
    receipt = JSON.parse(line);
  } catch {
    stats.skipped++;
    return;
  }
  stats.receiptRows++;
  if (receipt?.status !== "accepted") return;
  stats.acceptedReceipts++;
  const key = receipt.deterministic_event_id || receipt.raw_record_identity || receipt.receipt_hash || receipt.mint;
  if (!key || emittedKeys.has(key)) return;
  const raw = receipt.raw_record_identity;
  const input = raw ? inputByRawIdentity.get(raw) : null;
  if (input) {
    emitSelectedIfReady(receipt, input);
    return;
  }
  if (raw) {
    pendingReceipts.set(raw, receipt);
    stats.pending = pendingReceipts.size;
    while (pendingReceipts.size > maxPendingReceipts) {
      const first = pendingReceipts.keys().next().value;
      pendingReceipts.delete(first);
      stats.missingInput++;
    }
  }
}

function flushPending() {
  for (const [raw, receipt] of pendingReceipts) {
    const input = inputByRawIdentity.get(raw);
    if (!input) continue;
    pendingReceipts.delete(raw);
    emitSelectedIfReady(receipt, input);
  }
  stats.pending = pendingReceipts.size;
}

function emitSelectedIfReady(receipt, input) {
  const candidate = input?.candidate || {};
  const provider = input?.provider || {};
  const canonical = input?.canonical_state || {};
  const execution = input?.execution || {};
  const extra = input?.extra_decision_fields || {};
  const mint = receipt.mint || candidate.mint;
  const key = receipt.deterministic_event_id || receipt.raw_record_identity || receipt.receipt_hash || mint;
  const receiptAtMs = receiptTimeMs(receipt, provider);
  const recoveredTemplate = mint ? recoverExecutionTemplate(mint, receiptAtMs) : null;
  const execInput = executionReadyInput(input) ? input : recoveredTemplate?.input;
  const execCandidate = execInput?.candidate || candidate;
  const execProvider = execInput?.provider || provider;
  const execCanonical = execInput?.canonical_state || canonical;
  const execExtra = execInput?.extra_decision_fields || extra;
  const expected = receipt?.expected_entry || {};
  const sourceIxAccounts = Array.isArray(execExtra.sourceIxAccounts) ? execExtra.sourceIxAccounts : [];
  const sourceIxDataBase64 = execCandidate.source_ix_data_base64;
  const bondingCurve = execExtra.bondingCurve;
  // The recovered execution template can supply missing accounts/data from a
  // later swap, but the mint token program must remain tied to the original
  // candidate/creation. Letting a recovered template overwrite this can build
  // a Token-2022 ATA for a legacy mint and fail before the Pump buy ix.
  const tokenProgramId = candidate.token_program || execCandidate.token_program;

  if (!mint || !key || emittedKeys.has(key)) return;
  const missing = [];
  if (!bondingCurve) missing.push("bondingCurve");
  if (!sourceIxDataBase64) missing.push("sourceIxDataBase64");
  if (sourceIxAccounts.length === 0) missing.push("sourceIxAccounts");
  if (!canonical.virtual_sol_reserves && !canonical.market_cap_usd) {
    // virtual reserves are not top-level in old reset rows, but MC must exist for reporting.
  }
  if (missing.length > 0) {
    const pendingKey = key;
    const now = Date.now();
    const existing = pendingExecutionReceipts.get(pendingKey);
    if (!existing && maxExecutionTemplateWaitMs > 0) {
      pendingExecutionReceipts.set(pendingKey, {
        receipt,
        input,
        firstSeenAtMs: now,
        missing,
      });
      stats.pendingExecution = pendingExecutionReceipts.size;
      return;
    }
    if (existing && now - existing.firstSeenAtMs < maxExecutionTemplateWaitMs) {
      return;
    }
    if (existing) {
      pendingExecutionReceipts.delete(pendingKey);
      stats.pendingExecution = pendingExecutionReceipts.size;
      stats.executionTemplateTimeouts++;
    }
    stats.skipped++;
    console.log(JSON.stringify({
      event: "teacher_impulse_selected_event_adapter_skip",
      atMs: Date.now(),
      key,
      mint,
      reason: existing ? "execution_template_timeout" : "missing_required_execution_fields",
      missing,
      waitedMs: existing ? now - existing.firstSeenAtMs : 0,
    }));
    return;
  }
  if (recoveredTemplate && execInput !== input) {
    stats.recoveredExecutionFields++;
    console.log(JSON.stringify({
      event: "teacher_impulse_selected_event_adapter_recovered_execution_fields",
      atMs: Date.now(),
      key,
      mint,
      receiptRawIdentity: receipt.raw_record_identity || null,
      recoveredRawIdentity: recoveredTemplate.raw,
      recoveredAgeMs: receiptAtMs ? Math.max(0, receiptAtMs - recoveredTemplate.atMs) : null,
      maxExecutionTemplateAgeMs,
    }));
  }

  const entryAtMs = provider.candidate_ready_ts_ms || provider.decode_complete_ts_ms || provider.local_receive_ts_ms || recoveredTemplate?.atMs || Date.now();
  const event = {
    schemaVersion: "two-cq-continuous-shadow-event-1",
    event: "candidate_selected",
    at: new Date(entryAtMs).toISOString(),
    atMs: entryAtMs,
    entryAtMs,
    lane,
    policy,
    key,
    mint,
    sourceSignature: execProvider.transaction_signature || provider.transaction_signature || key,
    entryMarketCapUsd: receipt.calculated_market_cap_usd ?? expected.market_cap_usd ?? candidate.market_cap_usd,
    mayhemRetryPromoted: false,
    isWeakMayhemRecovered: false,
    mayhemBlockReason: input?.mayhem?.reason || null,
    directEntryQuote: {
      status: "ok",
      solInLamports: expected.position_size_lamports ?? execution.position_size_lamports,
      tokensOutRaw: expected.expected_tokens_out_raw ?? execution.expected_tokens_out_raw,
      minTokensOutRaw: expected.min_tokens_out_raw ?? execution.min_tokens_out_raw,
      slippageBps: expected.slippage_bps ?? execution.slippage_bps,
      curveSnapshot: {
        pda: bondingCurve,
        bondingCurve,
        creator: "source-template-only",
        tokenProgramId,
        quoteMint: "So11111111111111111111111111111111111111112",
        virtualSolReserves: reserveU64FromStateIdentity(canonical.state_identity, 0) ?? reserveU64FromStateIdentity(execCanonical.state_identity, 0),
        virtualTokenReserves: reserveU64FromStateIdentity(canonical.state_identity, 1) ?? reserveU64FromStateIdentity(execCanonical.state_identity, 1),
        slot: canonical.source_slot ?? provider.source_slot ?? execCanonical.source_slot ?? execProvider.source_slot,
        observedAtMs: canonical.observed_at_ms ?? execCanonical.observed_at_ms ?? entryAtMs,
      },
    },
    features: {
      mint,
      key,
      sourceSignature: execProvider.transaction_signature || provider.transaction_signature || key,
      sourceWallet: "teacher-impulse-reset",
      wallet: "teacher-impulse-reset",
      observedAtMs: entryAtMs,
      marketCapUsd: candidate.market_cap_usd ?? receipt.calculated_market_cap_usd,
      sourceIxAccounts,
      sourceIxDataBase64,
      tokenProgramId,
      creationAgeMs: candidate.creation_age_ms,
      netBuySol60: candidate.net_buy_sol_60,
      buyCount100ms: candidate.buy_count_100ms,
      buyCount250ms: candidate.buy_count_250ms,
      buyCount500ms: candidate.buy_count_500ms,
      buyCount1000ms: candidate.buy_count_1000ms,
      buyCount60: candidate.buy_count_60,
      sellPressure60: candidate.sell_pressure_60,
      dominantBuyShare60: candidate.dominant_buy_share_60,
      uniqueWallets60: candidate.unique_wallets_60,
      firstBuyerQuoteShareBps: candidate.first_buyer_quote_share_bps,
      largestWalletQuoteShareBps: candidate.largest_wallet_quote_share_bps,
    },
    adapter: {
      schemaVersion: "teacher-impulse-receipt-to-selected-event-1",
      receiptHash: receipt.receipt_hash,
      inputRawRecordIdentity: provider.raw_record_identity,
      sourceReceipt: receiptFile,
      sourceInput: inputFile,
      recoveredExecutionFields: Boolean(recoveredTemplate && execInput !== input),
      recoveredExecutionRawIdentity: recoveredTemplate?.raw || null,
    },
  };

  if (!event.directEntryQuote.curveSnapshot.virtualSolReserves || !event.directEntryQuote.curveSnapshot.virtualTokenReserves) {
    stats.skipped++;
    console.log(JSON.stringify({
      event: "teacher_impulse_selected_event_adapter_skip",
      atMs: Date.now(),
      key,
      mint,
      reason: "missing_virtual_reserves",
      stateIdentity: canonical.state_identity || null,
    }));
    return;
  }

  fs.appendFileSync(outputFile, `${JSON.stringify(event)}\n`);
  emittedKeys.add(key);
  stats.emitted++;
}

function flushPendingExecution() {
  for (const [key, pending] of pendingExecutionReceipts) {
    if (emittedKeys.has(key)) {
      pendingExecutionReceipts.delete(key);
      continue;
    }
    emitSelectedIfReady(pending.receipt, pending.input);
  }
  stats.pendingExecution = pendingExecutionReceipts.size;
}

function rememberExecutionTemplate(row) {
  if (!executionReadyInput(row)) return;
  const mint = row?.candidate?.mint;
  if (!mint) return;
  const atMs = row?.provider?.candidate_ready_ts_ms
    || row?.provider?.decode_complete_ts_ms
    || row?.provider?.local_receive_ts_ms
    || Date.now();
  const raw = row?.provider?.raw_record_identity || null;
  executionTemplateByMint.set(mint, { input: row, atMs, raw });
  executionTemplateOrder.push({ mint, raw });
  while (executionTemplateOrder.length > maxExecutionTemplateRows) {
    const oldRef = executionTemplateOrder.shift();
    const old = oldRef ? executionTemplateByMint.get(oldRef.mint) : null;
    if (old && old.raw === oldRef.raw) executionTemplateByMint.delete(oldRef.mint);
  }
}

function executionReadyInput(row) {
  const extra = row?.extra_decision_fields || {};
  return Boolean(
    row?.candidate?.source_ix_data_base64
      && extra.bondingCurve
      && Array.isArray(extra.sourceIxAccounts)
      && extra.sourceIxAccounts.length > 0
  );
}

function recoverExecutionTemplate(mint, receiptAtMs) {
  const cached = executionTemplateByMint.get(mint);
  if (!cached) return null;
  if (receiptAtMs && cached.atMs && Math.abs(receiptAtMs - cached.atMs) > maxExecutionTemplateAgeMs) {
    return null;
  }
  return cached;
}

function receiptTimeMs(receipt, provider) {
  if (provider?.candidate_ready_ts_ms) return provider.candidate_ready_ts_ms;
  if (provider?.decode_complete_ts_ms) return provider.decode_complete_ts_ms;
  if (provider?.local_receive_ts_ms) return provider.local_receive_ts_ms;
  const raw = String(receipt?.raw_record_identity || "");
  const match = raw.match(/:(\d{13})(?::|$)/);
  if (match) return Number(match[1]);
  return Date.now();
}

function reserveU64FromStateIdentity(identity, offset) {
  if (typeof identity !== "string") return null;
  const parts = identity.split(":");
  const tail = parts.slice(-2);
  const raw = tail[offset];
  if (!/^\d+$/.test(raw || "")) return null;
  return Number(raw);
}

function processNewLines(file, offset, handler) {
  if (!fs.existsSync(file)) return 0;
  const stat = fs.statSync(file);
  if (stat.size < offset) offset = 0;
  if (stat.size === offset) return offset;
  const fd = fs.openSync(file, "r");
  try {
    const len = Math.min(stat.size - offset, maxReadBytesPerPoll);
    const buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, offset);
    const text = buf.toString("utf8");
    const lines = text.split(/\n/);
    const complete = text.endsWith("\n") ? lines.slice(0, -1) : lines.slice(0, -1);
    for (const line of complete) {
      if (line.trim()) handler(line);
    }
    return offset + len - Buffer.byteLength(lines.at(-1) || "", "utf8");
  } finally {
    fs.closeSync(fd);
  }
}

function startOffset(file, atEnd) {
  if (!atEnd || !fs.existsSync(file)) return 0;
  return fs.statSync(file).size;
}

function parseBool(value, fallback) {
  if (value == null || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function clampInt(value, min, max, fallback) {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

function emitStatus(force) {
  const now = Date.now();
  if (!force && now - lastStatusAt < statusEveryMs) return;
  lastStatusAt = now;
  console.log(JSON.stringify({
    event: "teacher_impulse_receipt_to_selected_events_status",
    atMs: now,
    ...stats,
    pending: pendingReceipts.size,
    inputCacheSize: inputByRawIdentity.size,
    outputFile,
  }));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
