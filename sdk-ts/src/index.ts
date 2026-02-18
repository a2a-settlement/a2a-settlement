export { SettlementExchangeClient } from "./client.js";
export type { ClientOptions } from "./client.js";

export {
  A2A_SE_EXTENSION_URI,
  buildSettlementExtension,
} from "./agentcard.js";
export type {
  PricingEntry,
  SettlementExtensionOptions,
} from "./agentcard.js";

export {
  buildSettlementMetadata,
  attachSettlementMetadata,
  getSettlementBlock,
} from "./metadata.js";

export type {
  ErrorDetail,
  ErrorResponse,
  RegisterRequest,
  RegisterResponse,
  AccountResponse,
  DirectoryResponse,
  UpdateSkillsResponse,
  RotateKeyResponse,
  EscrowRequest,
  EscrowResponse,
  ReleaseResponse,
  RefundResponse,
  DisputeResponse,
  ResolveReleaseResponse,
  ResolveRefundResponse,
  ResolveResponse,
  BalanceResponse,
  TransactionItem,
  TransactionsResponse,
  EscrowDetailResponse,
  WebhookResponse,
  WebhookDeleteResponse,
  StatsResponse,
  HealthResponse,
  SettlementMetadata,
} from "./types.js";
