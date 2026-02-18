export const A2A_SE_EXTENSION_URI =
  "https://a2a-settlement.org/extensions/settlement/v1";

export interface PricingEntry {
  baseTokens: number;
  model: "per-request" | "per-unit" | "per-minute" | "negotiable";
  currency?: string;
  unitSize?: number;
}

export interface SettlementExtensionOptions {
  /** Exchange URLs the agent is registered on. */
  exchangeUrls: string[];
  /** Map of exchange URL -> account ID. */
  accountIds: Record<string, string>;
  /** Preferred exchange URL (defaults to first in list). */
  preferredExchange?: string;
  /** Per-skill pricing configuration. */
  pricing?: Record<string, PricingEntry>;
  /** Default currency. */
  currency?: string;
  /** Agent reputation score (0.0 - 1.0). */
  reputation?: number;
  /** Agent availability score (0.0 - 1.0). */
  availability?: number;
  /** Whether settlement is required. */
  required?: boolean;
  /** Human-readable description. */
  description?: string;
}

/**
 * Build an AgentCard `capabilities.extensions[]` entry for A2A-SE.
 */
export function buildSettlementExtension(
  options: SettlementExtensionOptions,
): Record<string, unknown> {
  const params: Record<string, unknown> = {
    exchangeUrls: options.exchangeUrls,
    preferredExchange:
      options.preferredExchange ?? options.exchangeUrls[0],
    accountIds: options.accountIds,
    currency: options.currency ?? "ATE",
  };

  if (options.pricing) params["pricing"] = options.pricing;
  if (options.reputation !== undefined)
    params["reputation"] = options.reputation;
  if (options.availability !== undefined)
    params["availability"] = options.availability;

  return {
    uri: A2A_SE_EXTENSION_URI,
    description:
      options.description ??
      "Accepts token-based payment via A2A Settlement Exchange",
    required: options.required ?? false,
    params,
  };
}
