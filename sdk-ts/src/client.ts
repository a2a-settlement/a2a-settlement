import type {
  AccountResponse,
  BalanceResponse,
  DirectoryResponse,
  DisputeResponse,
  EscrowDetailResponse,
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

export interface ClientOptions {
  baseUrl: string;
  apiKey?: string;
  timeoutMs?: number;
}

/**
 * Synchronous-style client for the A2A Settlement Exchange REST API.
 * Uses the native `fetch` API (available in Node 18+ and all modern browsers).
 */
export class SettlementExchangeClient {
  private baseUrl: string;
  private apiKey?: string;
  private timeoutMs: number;

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl;
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? 10_000;
  }

  private headers(idempotencyKey?: string): Record<string, string> {
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

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const resp = await fetch(url, {
        method,
        headers: this.headers(options?.idempotencyKey),
        body: body ? JSON.stringify(body) : undefined,
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
  ): Promise<ResolveResponse> {
    return this.request("POST", "/v1/exchange/resolve", {
      escrow_id: escrowId,
      resolution,
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

  // --- Stats ---

  async getStats(): Promise<StatsResponse> {
    return this.request("GET", "/v1/stats");
  }
}
