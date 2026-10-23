type FetchLike = typeof fetch;

export type HeliusKeyEvent = {
  ts: string;
  event: "key_selected" | "key_cooldown" | "key_exhausted" | "request_retry";
  keySlot: number;
  status?: number;
  detail?: string;
};

type KeyState = { value: string; cooldownUntil: number; exhausted: boolean };

export type HeliusKeyPoolOptions = {
  fetcher?: FetchLike;
  cooldownMs?: number;
  serverErrorCooldownMs?: number;
  maxAttempts?: number;
  onEvent?: (event: HeliusKeyEvent) => void;
};

function unique(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

export function heliusKeysFromEnv(env: NodeJS.ProcessEnv = process.env): string[] {
  const includePrimary = !/^(false|0|no)$/i.test(
    env.HELIUS_API_POOL_INCLUDE_PRIMARY ?? "true"
  );
  const numbered = Object.entries(env)
    .filter(([name, value]) => /^HELIUS_API_KEY_\d+$/.test(name) && value)
    .sort(([a], [b]) => Number(a.slice(a.lastIndexOf("_") + 1)) - Number(b.slice(b.lastIndexOf("_") + 1)))
    .map(([, value]) => value!);
  const pooled = (env.HELIUS_API_KEYS ?? "").split(/[\s,;]+/);
  return unique([
    includePrimary ? env.HELIUS_API_KEY ?? "" : "",
    ...pooled,
    ...numbered,
  ]);
}

function isQuotaExhausted(text: string): boolean {
  return /max usage|quota|credit|plan limit|monthly limit/i.test(text);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class HeliusKeyPool {
  private readonly states: KeyState[];
  private readonly fetcher: FetchLike;
  private readonly cooldownMs: number;
  private readonly serverErrorCooldownMs: number;
  private readonly maxAttempts: number;
  private readonly onEvent?: (event: HeliusKeyEvent) => void;
  private cursor = 0;

  constructor(keys: string[], options: HeliusKeyPoolOptions = {}) {
    this.states = unique(keys).map((value) => ({ value, cooldownUntil: 0, exhausted: false }));
    this.fetcher = options.fetcher ?? fetch;
    this.cooldownMs = options.cooldownMs ?? 60_000;
    this.serverErrorCooldownMs = options.serverErrorCooldownMs ?? 5_000;
    this.maxAttempts = options.maxAttempts ?? Math.max(8, this.states.length * 4);
    this.onEvent = options.onEvent;
  }

  get size(): number {
    return this.states.length;
  }

  private emit(event: Omit<HeliusKeyEvent, "ts">): void {
    this.onEvent?.({ ts: new Date().toISOString(), ...event });
  }

  private nextAvailable(): { state: KeyState; slot: number } | null {
    const now = Date.now();
    for (let offset = 0; offset < this.states.length; offset++) {
      const slot = (this.cursor + offset) % this.states.length;
      const state = this.states[slot]!;
      if (!state.exhausted && state.cooldownUntil <= now) {
        this.cursor = (slot + 1) % this.states.length;
        return { state, slot };
      }
    }
    return null;
  }

  private waitUntilNextKey(): number | null {
    const active = this.states.filter((state) => !state.exhausted);
    if (active.length === 0) return null;
    return Math.max(25, Math.min(...active.map((state) => state.cooldownUntil)) - Date.now());
  }

  async request(buildUrl: (apiKey: string) => string, init?: RequestInit): Promise<Response> {
    if (this.states.length === 0) throw new Error("No Helius API keys configured");
    let lastError = "Helius request failed";
    for (let attempt = 1; attempt <= this.maxAttempts; attempt++) {
      let selected = this.nextAvailable();
      if (!selected) {
        const waitMs = this.waitUntilNextKey();
        if (waitMs == null) throw new Error(`All Helius API keys exhausted: ${lastError}`);
        await sleep(waitMs);
        selected = this.nextAvailable();
        if (!selected) continue;
      }
      const { state, slot } = selected;
      try {
        const response = await this.fetcher(buildUrl(state.value), init);
        if (response.status === 429) {
          const text = (await response.text()).slice(0, 240);
          lastError = `Helius 429: ${text}`;
          if (isQuotaExhausted(text)) {
            state.exhausted = true;
            this.emit({ event: "key_exhausted", keySlot: slot + 1, status: 429, detail: "quota" });
          } else {
            state.cooldownUntil = Date.now() + this.cooldownMs;
            this.emit({ event: "key_cooldown", keySlot: slot + 1, status: 429, detail: `${this.cooldownMs}ms` });
          }
          continue;
        }
        if (response.status >= 500) {
          lastError = `Helius ${response.status}`;
          state.cooldownUntil = Date.now() + this.serverErrorCooldownMs;
          this.emit({ event: "request_retry", keySlot: slot + 1, status: response.status });
          continue;
        }
        return response;
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error);
        state.cooldownUntil = Date.now() + this.serverErrorCooldownMs;
        this.emit({ event: "request_retry", keySlot: slot + 1, detail: lastError.slice(0, 120) });
      }
    }
    throw new Error(`${lastError} after ${this.maxAttempts} attempts`);
  }
}
