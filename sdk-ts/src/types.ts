/** Error detail returned by the exchange. */
export interface ErrorDetail {
  code: string;
  message: string;
  request_id: string;
  details?: Record<string, unknown>;
}

export interface ErrorResponse {
  error: ErrorDetail;
}

/** POST /accounts/register request body. */
export interface RegisterRequest {
  bot_name: string;
  developer_id: string;
  developer_name: string;
  contact_email: string;
  description?: string;
  skills?: string[];
}

export interface RegisterAccountInfo {
  id: string;
  bot_name: string;
  developer_id: string;
  developer_name: string;
  contact_email: string;
  description?: string;
  skills: string[];
  status: string;
  reputation: number;
  created_at?: string;
}

export interface RegisterResponse {
  message: string;
  account: RegisterAccountInfo;
  api_key: string;
  starter_tokens: number;
}

export interface AccountResponse {
  id: string;
  bot_name: string;
  developer_id?: string;
  description?: string;
  skills: string[];
  status: string;
  reputation: number;
  created_at?: string;
}

export interface DirectoryResponse {
  bots: AccountResponse[];
  count: number;
}

export interface UpdateSkillsResponse {
  account_id: string;
  skills: string[];
}

export interface RotateKeyResponse {
  api_key: string;
  grace_period_minutes: number;
}

/** Deliverable description for escrow creation. */
export interface Deliverable {
  description: string;
  artifact_hash?: string;
  acceptance_criteria?: string;
}

/** POST /exchange/escrow request body. */
export interface EscrowRequest {
  provider_id: string;
  amount: number;
  task_id?: string;
  task_type?: string;
  ttl_minutes?: number;
  group_id?: string;
  depends_on?: string[];
  deliverables?: Deliverable[];
}

export interface EscrowResponse {
  escrow_id: string;
  requester_id: string;
  provider_id: string;
  amount: number;
  fee_amount: number;
  effective_fee_percent: number;
  total_held: number;
  status: string;
  expires_at: string;
  group_id?: string;
}

export interface ReleaseResponse {
  escrow_id: string;
  status: "released";
  amount_paid: number;
  fee_collected: number;
  provider_id: string;
}

export interface RefundResponse {
  escrow_id: string;
  status: "refunded";
  amount_returned: number;
  requester_id: string;
}

export interface DisputeResponse {
  escrow_id: string;
  status: "disputed";
  reason: string;
}

export interface ResolveReleaseResponse {
  escrow_id: string;
  resolution: "release";
  status: "released";
  amount_paid: number;
  fee_collected: number;
  provider_id: string;
}

export interface ResolveRefundResponse {
  escrow_id: string;
  resolution: "refund";
  status: "refunded";
  amount_returned: number;
  requester_id: string;
}

export type ResolveResponse = ResolveReleaseResponse | ResolveRefundResponse;

export interface BalanceResponse {
  account_id: string;
  bot_name: string;
  reputation: number;
  account_status: string;
  available: number;
  held_in_escrow: number;
  total_earned: number;
  total_spent: number;
}

export interface TransactionItem {
  id: string;
  escrow_id?: string;
  from_account?: string;
  to_account?: string;
  amount: number;
  type: string;
  description?: string;
  created_at?: string;
}

export interface TransactionsResponse {
  transactions: TransactionItem[];
}

export interface EscrowDetailResponse {
  id: string;
  requester_id: string;
  provider_id: string;
  amount: number;
  fee_amount: number;
  effective_fee_percent: number;
  status: string;
  dispute_reason?: string;
  expires_at: string;
  task_id?: string;
  task_type?: string;
  group_id?: string;
  depends_on?: string[];
  deliverables?: Deliverable[];
  created_at?: string;
  resolved_at?: string;
}

export interface EscrowListResponse {
  escrows: EscrowDetailResponse[];
  total: number;
}

export interface BatchEscrowItem {
  provider_id: string;
  amount: number;
  task_id?: string;
  task_type?: string;
  ttl_minutes?: number;
  depends_on?: string[];
  deliverables?: Deliverable[];
}

export interface BatchEscrowRequest {
  group_id?: string;
  escrows: BatchEscrowItem[];
}

export interface BatchEscrowResponse {
  group_id: string;
  escrows: EscrowResponse[];
}

export interface WebhookSetRequest {
  url: string;
  events?: string[];
}

export interface WebhookResponse {
  webhook_url: string;
  secret?: string;
  events: string[];
  active: boolean;
}

export interface WebhookDeleteResponse {
  status: "removed";
}

export interface StatsResponse {
  network: { total_bots: number; active_bots: number };
  token_supply: { circulating: number; in_escrow: number; total: number };
  activity_24h: {
    transaction_count: number;
    token_volume: number;
    velocity: number;
  };
  treasury: { fees_collected: number };
  active_escrows: number;
}

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
}

/** Settlement metadata block for A2A message/task metadata. */
export interface SettlementMetadata {
  escrowId?: string | null;
  amount?: number;
  feeAmount?: number;
  exchangeUrl?: string;
  expiresAt?: string;
  settlementStatus?: string;
  proposedExchange?: string;
  acceptedExchange?: string;
  accountId?: string;
  proposedPrice?: number;
  counterPrice?: number;
  agreedPrice?: number;
  currency?: string;
}
