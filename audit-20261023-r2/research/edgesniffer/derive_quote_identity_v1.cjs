// Offline PDA identity verification only; no RPC/signing.
const fs = require('node:fs');
const {PublicKey} = require(require('node:path').resolve(process.argv[2]));
const idl = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const program = new PublicKey(idl.address);
const feeProgram = new PublicKey('pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ');
const instruction = idl.instructions.find(x => x.name === 'buy');
function derive(name, owner) {
  const spec = instruction.accounts.find(x => x.name === name);
  if (!spec.pda.seeds.every(x => x.kind === 'const')) throw new Error('Nonconstant identity rule');
  const [key,bump] = PublicKey.findProgramAddressSync(spec.pda.seeds.map(x => Buffer.from(x.value)), owner);
  return {address:key.toBase58(),bump};
}
process.stdout.write(JSON.stringify({global:derive('global',program),fee:derive('fee_config',feeProgram)}));
