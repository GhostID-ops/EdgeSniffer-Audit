#!/usr/bin/env node
import {execFileSync} from 'node:child_process';
import {existsSync, mkdirSync, writeFileSync} from 'node:fs';

const service = process.env.EDGESNIFFER_SERVICE || 'edgesniffer-2cqv4.service';
const dropInDir = `/etc/systemd/system/${service}.d`;
const dropInFile = `${dropInDir}/20-resource-isolation.conf`;
const restart = process.argv.includes('--restart');

if (process.platform !== 'linux') {
  console.error('EdgeSniffer resource isolation is a Linux/systemd VPS step.');
  process.exit(1);
}

if (typeof process.getuid === 'function' && process.getuid() !== 0) {
  console.error('Run this as root so it can write the systemd drop-in.');
  process.exit(1);
}

const unitPath = `/etc/systemd/system/${service}`;
if (!existsSync(unitPath) && !existsSync(dropInDir)) {
  console.error(`Systemd service was not found: ${service}`);
  process.exit(1);
}

const content = `[Service]
MemoryAccounting=true
MemoryHigh=900M
MemoryMax=1300M
CPUAccounting=true
CPUWeight=60
IOSchedulingClass=best-effort
IOSchedulingPriority=6
OOMPolicy=stop
OOMScoreAdjust=500
`;

mkdirSync(dropInDir, {recursive: true});
writeFileSync(dropInFile, content, 'utf8');
execFileSync('systemctl', ['daemon-reload'], {stdio: 'inherit'});

if (restart) {
  execFileSync('systemctl', ['restart', service], {stdio: 'inherit'});
}

console.log(`Installed ${dropInFile}`);
console.log(`Service: ${service}`);
console.log(restart ? 'Restarted service with isolation active.' : 'Restart required for active process to pick up isolation.');
