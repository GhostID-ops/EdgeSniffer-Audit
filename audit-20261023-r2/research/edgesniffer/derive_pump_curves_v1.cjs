// Offline derivation only. No connection, signer, RPC or transaction builder.
const fs = require('node:fs');
const path = require('node:path');
const moduleRoot = path.resolve(process.argv[2]);
const {PublicKey} = require(moduleRoot);
const sdk = require(path.join(moduleRoot, 'package.json')).version;
const program = new PublicKey('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P');
const mints = JSON.parse(fs.readFileSync(0, 'utf8'));
const mappings = {};
for (const mint of [...new Set(mints)].sort()) {
  const key = new PublicKey(mint);
  if (key.toBase58() !== mint) throw new Error('Noncanonical mint encoding');
  const [curve, bump] = PublicKey.findProgramAddressSync([Buffer.from('bonding-curve'), key.toBuffer()], program);
  mappings[mint] = {curve: curve.toBase58(), bump};
}
process.stdout.write(JSON.stringify({sdk_version: sdk, node_version: process.version, program: program.toBase58(), mappings}));
