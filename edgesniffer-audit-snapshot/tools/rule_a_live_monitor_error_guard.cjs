#!/usr/bin/env node
"use strict";

const EXACT_ERROR_FIELDS = new Set([
  "error",
  "err",
  "programError",
  "decodedError",
  "instructionError",
  "transactionError",
  "finalityError",
]);

function parseJsonMaybe(value) {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  if (
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]"))
  ) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return value;
    }
  }
  return value;
}

function exactCustomCodeFromHex(code) {
  if (typeof code !== "string") return null;
  const match = code.match(/^0x([0-9a-f]+)$/i);
  if (!match) return null;
  return Number.parseInt(match[1], 16);
}

function fieldMayContainError(fieldName) {
  if (!fieldName) return true;
  return EXACT_ERROR_FIELDS.has(fieldName);
}

function hasExactCustomError(value, targetCode, fieldName = null) {
  value = parseJsonMaybe(value);

  if (value == null) return false;

  if (typeof value === "number") {
    return fieldMayContainError(fieldName) && value === targetCode;
  }

  if (typeof value === "string") {
    if (!fieldMayContainError(fieldName)) return false;

    const hex = exactCustomCodeFromHex(value);
    if (hex === targetCode) return true;

    return (
      new RegExp(`\\bCustom\\s*\\(\\s*${targetCode}\\s*\\)`).test(value) ||
      new RegExp(`\\bcustom program error:\\s*${targetCode}\\b`, "i").test(value) ||
      (targetCode === 6074 && /\bcustom program error:\s*0x17ba\b/i.test(value)) ||
      (targetCode === 6074 && /\bIncorrectProgramId\b/.test(value))
    );
  }

  if (Array.isArray(value)) {
    return value.some((item) => hasExactCustomError(item, targetCode, fieldName));
  }

  if (typeof value === "object") {
    for (const [key, inner] of Object.entries(value)) {
      if (key === "Custom" && Number(inner) === targetCode) return true;
      if (targetCode === 6074 && key === "InstructionError" && hasExactCustomError(inner, targetCode, key)) {
        return true;
      }
      if (targetCode === 6074 && key === "IncorrectProgramId" && inner === true) return true;
      if (hasExactCustomError(inner, targetCode, key)) return true;
    }
  }

  return false;
}

function isGenuine6074(event) {
  return hasExactCustomError(event?.error, 6074, "error") ||
    hasExactCustomError(event?.err, 6074, "err") ||
    hasExactCustomError(event?.programError, 6074, "programError") ||
    hasExactCustomError(event?.decodedError, 6074, "decodedError") ||
    hasExactCustomError(event?.instructionError, 6074, "instructionError") ||
    hasExactCustomError(event?.transactionError, 6074, "transactionError") ||
    hasExactCustomError(event?.finalityError, 6074, "finalityError");
}

function selfTest() {
  const fixtures = [
    [{ error: '{"InstructionError":[2,{"Custom":6074}]}' }, true, "json-string-custom-6074"],
    [{ error: { InstructionError: [2, { Custom: 6074 }] } }, true, "object-custom-6074"],
    [{ error: "custom program error: 0x17ba" }, true, "hex-17ba"],
    [{ error: "Custom(6074)" }, true, "display-custom-6074"],
    [{ error: "InstructionError: IncorrectProgramId" }, true, "incorrect-program-id"],
    [{ slot: 443486074, amount: 6074, error: null }, false, "random-digits-outside-error"],
    [{ kind: "rustSelectedCandidateCurveCacheExitBridge", slot: 443486074 }, false, "slot-containing-6074"],
    [{ message: "slot 443486074 moved", error: null }, false, "message-containing-6074"],
    [{ error: '{"InstructionError":[2,{"Custom":6042}]}' }, false, "other-custom-code"],
  ];

  let failed = 0;
  for (const [event, expected, name] of fixtures) {
    const actual = isGenuine6074(event);
    if (actual !== expected) {
      failed += 1;
      console.error(`FAIL ${name}: expected ${expected}, got ${actual}`);
    }
  }
  if (failed) process.exit(1);
  console.log("PASS rule_a_live_monitor_error_guard self-test");
}

if (require.main === module) {
  if (process.argv.includes("--self-test")) {
    selfTest();
  } else {
    const input = require("fs").readFileSync(0, "utf8").trim();
    if (!input) process.exit(0);
    for (const line of input.replace(/}\s*{/g, "}\n{").split(/\r?\n/)) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (isGenuine6074(event)) console.log(line);
    }
  }
}

module.exports = { hasExactCustomError, isGenuine6074 };
