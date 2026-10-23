import assert from "node:assert/strict";
import {
  createMachineFingerprint,
  canActivateMachine,
  generateLicenseKey,
  hashLicenseKey,
  hashMachineFingerprint,
  normalizeLicenseKey,
  parseArgs,
  shortKeyFingerprint,
} from "./licenseCore.js";

const key = generateLicenseKey();
assert.match(key, /^FBOT-[0-9A-F]{8}-[0-9A-F]{8}-[0-9A-F]{8}-[0-9A-F]{8}$/);

const mixed = key.toLowerCase();
assert.equal(normalizeLicenseKey(mixed), key);

const hash = hashLicenseKey(key, "server-secret");
assert.equal(hash.length, 64);
assert.notEqual(hash, key);
assert.equal(hashLicenseKey(key, "server-secret"), hashLicenseKey(mixed, "server-secret"));
assert.notEqual(hashLicenseKey(key, "server-secret"), hashLicenseKey(key, "other-secret"));

const short = shortKeyFingerprint(key);
assert.equal(short.length, 16);
assert.notEqual(short, key);

const machineA = createMachineFingerprint("salt-a");
const machineA2 = createMachineFingerprint("salt-a");
const machineB = createMachineFingerprint("salt-b");
assert.equal(machineA, machineA2);
assert.notEqual(machineA, machineB);
assert.equal(hashMachineFingerprint(machineA, "server-secret").length, 64);
assert.equal(canActivateMachine([], "machine-1", 1), "allowed");
assert.equal(canActivateMachine(["machine-1"], "machine-1", 1), "allowed");
assert.equal(canActivateMachine(["machine-1"], "machine-2", 1), "machine_limit");
assert.equal(canActivateMachine(["machine-1"], "machine-2", 2), "allowed");

const parsed = parseArgs(["--label", "Friend Name", "--dry", "--expires", "2026-06-20"]);
assert.deepEqual(parsed, {
  label: "Friend Name",
  dry: true,
  expires: "2026-06-20",
});

console.log("License tests passed");
