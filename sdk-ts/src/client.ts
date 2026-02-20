import type {
  AccountResponse,
  BalanceResponse,
  BatchEscrowRequest,
  BatchEscrowResponse,
  DirectoryResponse,
  DisputeResponse,
  EscrowDetailResponse,
  EscrowListResponse,
  EscrowRequest,
  EscrowResponse,
  HealthResponse,
  RefundResponse,
  RegisterRequest,
  RegisterResponse,
  ReleaseResponse,
  ResolveResponse,
  RotateKeyResponse,
  StatsResponse,
  TransactionsResponse,
  UpdateSkillsResponse,
  WebhookDeleteResponse,
  WebhookResponse,
} from "./types.js";

function joinUrl(base: string, path: string): string {
  return base.replace(/\/+$/, "") + "/" + path.replace(/^\/+/, "");
}

function randomRequestId(): string {
  const hex = Array.from(crypto.getRandomValues(new Uint8Array(6)))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `req_${hex}`;
}

async function hmacSha256(key: string, message: Uint8Array): Promise<string> {
  const enc = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    enc.encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, message);
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Produce X-A2A-Signature and X-A2A-Timestamp headers for request signing.
 */
export async function signRequest(
  apiKey: string,
  method: string,
  path: string,
  body?: Uint8Array | string,
): Promise<Record<string, string>> {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const enc = new TextEncoder();
  const prefix = enc.encode(`${timestamp}${method.toUpperCase()}${path}`);
  const bodyBytes =
    body instanceof Uint8Array
      ? body
      : body
        ? enc.encode(body)
        : new Uint8Array(0);
  const message = new Uint8Array(prefix.length + bodyBytes.length);
  message.set(prefix, 0);
  message.set(bodyBytes, prefix.length);
  const sig = await hmacSha256(apiKey, message);
  return { "X-A2A-Signature": sig, "X-A2A-Timestamp": timestamp };
}

export interface ClientOptions {
  baseUrl: string;
  apiKey?: string;
  timeoutMs?: number;
  signRequests?: boolean;
}

/**
 * Synchronous-style client for the A2A Settlement Exchange REST API.
 * Uses the native `fetch` API (available in Node 18+ and all modern browsers).
 */
export class SettlementExchangeClient {
  private baseUrl: string;
  private apiKey?: string;
  private timeoutMs: number;
  private signReqs: boolean;

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl;
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? 10_000;
    this.signReqs = options.signRequests ?? false;
  }

  private async headers(
    method: string,
    path: string,
    idempotencyKey?: string,
    body?: string,
  ): Promise<Record<string, string>> {
    const h: Record<string, string> = {
      "Content-Type": "application/json",
      "X-Request-Id": randomRequestId(),
    };
    if (this.apiKey) {
      h["Authorization"] = `Bearer ${this.apiKey}`;
    }
    if (idempotencyKey) {
      h["Idempotency-Key"] = idempotencyKey;
    }
    if (this.signReqs && this.apiKey) {
      const sigHeaders = await signRequest(this.apiKey, method, path, body);
      Object.assign(h, sigHeaders);
    }
    return h;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    options?: { idempotencyKey?: string; params?: Record<string, string> },
  ): Promise<T> {
    let url = joinUrl(this.baseUrl, path);
    if (options?.params) {
      const qs = new URLSearchParams(options.params);
      url += `?${qs.toString()}`;
    }

    const bodyStr = body ? JSON.stringify(body) : undefined;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const resp = await fetch(url, {
        method,
        headers: await this.headers(method, path, options?.idempotencyKey, bodyStr),
        body: bodyStr,
        signal: controller.signal,
      });

      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${text}`);
      }

      return (await resp.json()) as T;
    } finally {
      clearTimeout(timeout);
    }
  }

  // --- Health ---

  async health(): Promise<HealthResponse> {
    return this.request("GET", "/health");
  }

  // --- Accounts ---

  async registerAccount(
    req: RegisterRequest,
    idempotencyKey?: string,
  ): Promise<RegisterResponse> {
    return this.request("POST", "/v1/accounts/register", req, {
      idempotencyKey,
    });
  }

  async directory(options?: {
    skill?: string;
    limit?: number;
    offset?: number;
  }): Promise<DirectoryResponse> {
    const params: Record<string, string> = {};
    if (options?.skill) params["skill"] = options.skill;
    if (options?.limit !== undefined) params["limit"] = String(options.limit);
    if (options?.offset !== undefined)
      params["offset"] = String(options.offset);
    return this.request("GET", "/v1/accounts/directory", undefined, {
      params,
    });
  }

  async getAccount(accountId: string): Promise<AccountResponse> {
    return this.request("GET", `/v1/accounts/${accountId}`);
  }

  async updateSkills(skills: string[]): Promise<UpdateSkillsResponse> {
    return this.request("PUT", "/v1/accounts/skills", { skills });
  }

  async rotateKey(): Promise<RotateKeyResponse> {
    return this.request("POST", "/v1/accounts/rotate-key");
  }

  // --- Webhooks ---

  async setWebhook(
    url: string,
    events?: string[],
  ): Promise<WebhookResponse> {
    const body: { url: string; events?: string[] } = { url };
    if (events) body.events = events;
    return this.request("PUT", "/v1/accounts/webhook", body);
  }

  async deleteWebhook(): Promise<WebhookDeleteResponse> {
    return this.request("DELETE", "/v1/accounts/webhook");
  }

  // --- Settlement ---

  async createEscrow(
    req: EscrowRequest,
    idempotencyKey?: string,
  ): Promise<EscrowResponse> {
    return this.request("POST", "/v1/exchange/escrow", req, {
      idempotencyKey,
    });
  }

  async releaseEscrow(
    escrowId: string,
    idempotencyKey?: string,
  ): Promise<ReleaseResponse> {
    return this.request(
      "POST",
      "/v1/exchange/release",
      { escrow_id: escrowId },
      { idempotencyKey },
    );
  }

  async refundEscrow(
    escrowId: string,
    reason?: string,
    idempotencyKey?: string,
  ): Promise<RefundResponse> {
    const body: { escrow_id: string; reason?: string } = {
      escrow_id: escrowId,
    };
    if (reason) body.reason = reason;
    return this.request("POST", "/v1/exchange/refund", body, {
      idempotencyKey,
    });
  }

  async disputeEscrow(
    escrowId: string,
    reason: string,
  ): Promise<DisputeResponse> {
    return this.request("POST", "/v1/exchange/dispute", {
      escrow_id: escrowId,
      reason,
    });
  }

  async resolveEscrow(
    escrowId: string,
    resolution: "release" | "refund",
    options?: { strategy?: string },
  ): Promise<ResolveResponse> {
    return this.request("POST", "/v1/exchange/resolve", {
      escrow_id: escrowId,
      resolution,
      ...(options?.strategy != null && { strategy: options.strategy }),
    });
  }

  async getBalance(): Promise<BalanceResponse> {
    return this.request("GET", "/v1/exchange/balance");
  }

  async getTransactions(options?: {
    limit?: number;
    offset?: number;
  }): Promise<TransactionsResponse> {
    const params: Record<string, string> = {};
    if (options?.limit !== undefined) params["limit"] = String(options.limit);
    if (options?.offset !== undefined)
      params["offset"] = String(options.offset);
    return this.request("GET", "/v1/exchange/transactions", undefined, {
      params,
    });
  }

  async getEscrow(escrowId: string): Promise<EscrowDetailResponse> {
    return this.request("GET", `/v1/exchange/escrows/${escrowId}`);
  }

  async listEscrows(options?: {
    task_id?: string;
    group_id?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }): Promise<EscrowListResponse> {
    const params: Record<string, string> = {};
    if (options?.task_id) params["task_id"] = options.task_id;
    if (options?.group_id) params["group_id"] = options.group_id;
    if (options?.status) params["status"] = options.status;
    if (options?.limit !== undefined) params["limit"] = String(options.limit);
    if (options?.offset !== undefined)
      params["offset"] = String(options.offset);
    return this.request("GET", "/v1/exchange/escrows", undefined, { params });
  }

  async batchCreateEscrow(
    req: BatchEscrowRequest,
    idempotencyKey?: string,
  ): Promise<BatchEscrowResponse> {
    return this.request("POST", "/v1/exchange/escrow/batch", req, {
      idempotencyKey,
    });
  }

  // --- Stats ---

  async getStats(): Promise<StatsResponse> {
    return this.request("GET", "/v1/stats");
  }
}
