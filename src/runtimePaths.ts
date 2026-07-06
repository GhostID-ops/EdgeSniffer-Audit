import path from "node:path";

function cleanId(value: string | undefined): string {
  const raw = (value ?? "main").trim();
  const safe = raw.replace(/[^a-zA-Z0-9_.-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  return safe || "main";
}

export const instanceId = cleanId(process.env.INSTANCE_ID);
export const instanceRoot = path.resolve(process.env.INSTANCE_ROOT ?? process.cwd());
export const dataDir = path.resolve(process.env.DATA_DIR ?? path.join(instanceRoot, "data"));
export const logDir = path.resolve(process.env.LOG_DIR ?? path.join(instanceRoot, "logs"));
export const envPath = path.resolve(process.env.INSTANCE_ENV_PATH ?? path.join(instanceRoot, ".env"));

export function dataPath(...parts: string[]): string {
  return path.resolve(dataDir, ...parts);
}

export function logPath(...parts: string[]): string {
  return path.resolve(logDir, ...parts);
}