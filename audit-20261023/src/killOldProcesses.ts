import { execFileSync } from "node:child_process";

type Proc = {
  ProcessId: number;
  ParentProcessId: number;
  Name: string;
  CommandLine: string;
};

const scope = (process.argv[2] ?? "bot").toLowerCase();
const targets =
  scope === "all"
    ? ["src/copytrade.ts", "src/web.ts"]
    : ["src/copytrade.ts"];

function loadProcesses(): Proc[] {
  if (process.platform !== "win32") {
    console.log("Process cleanup skipped: this helper is currently Windows-only.");
    return [];
  }

  const script = [
    "$ErrorActionPreference = 'SilentlyContinue';",
    "Get-CimInstance Win32_Process |",
    "Select-Object ProcessId,ParentProcessId,Name,CommandLine |",
    "ConvertTo-Json -Compress",
  ].join(" ");
  const raw = execFileSync("powershell.exe", ["-NoProfile", "-Command", script], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();

  if (!raw) return [];
  const parsed = JSON.parse(raw) as Proc | Proc[];
  return Array.isArray(parsed) ? parsed : [parsed];
}

function normalize(text: string | null | undefined): string {
  return String(text ?? "").replace(/\\/g, "/").toLowerCase();
}

function unique(nums: number[]): number[] {
  return Array.from(new Set(nums)).filter((n) => Number.isInteger(n) && n > 0);
}

function descendantsOf(processes: Proc[], roots: number[]): number[] {
  const children = new Map<number, number[]>();
  for (const proc of processes) {
    const list = children.get(proc.ParentProcessId) ?? [];
    list.push(proc.ProcessId);
    children.set(proc.ParentProcessId, list);
  }

  const out: number[] = [];
  const queue = roots.slice();
  while (queue.length) {
    const pid = queue.shift()!;
    for (const child of children.get(pid) ?? []) {
      out.push(child);
      queue.push(child);
    }
  }
  return unique(out);
}

function commandMatches(proc: Proc): boolean {
  const cmd = normalize(proc.CommandLine);
  if (!cmd.includes("finalbot")) return false;
  return targets.some((target) => cmd.includes(target));
}

function killPids(pids: number[]): void {
  if (pids.length === 0) {
    console.log("No old bot processes found.");
    return;
  }

  const script =
    `$pids = @(${pids.join(",")}); ` +
    "foreach ($pid in $pids) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue }; exit 0";
  execFileSync("powershell.exe", ["-NoProfile", "-Command", script], {
    stdio: "ignore",
  });
  console.log(`Killed old bot process(es): ${pids.join(", ")}`);
}

try {
  const processes = loadProcesses();
  const matches = processes.filter(commandMatches).map((p) => p.ProcessId);
  const pids = unique([...descendantsOf(processes, matches), ...matches]).filter(
    (pid) => pid !== process.pid
  );
  killPids(pids);
} catch (err) {
  const message = err instanceof Error ? err.message : String(err);
  console.warn(`Old-process cleanup failed: ${message}`);
  console.warn("Continuing startup anyway.");
}
