import type { SettlementMetadata } from "./types.js";

/**
 * Build the `a2a-se` metadata block to include in an A2A message.
 */
export function buildSettlementMetadata(
  meta: SettlementMetadata,
): Record<string, SettlementMetadata> {
  return { "a2a-se": meta };
}

/**
 * Attach settlement metadata to an existing A2A metadata object.
 */
export function attachSettlementMetadata(
  metadata: Record<string, unknown>,
  settlement: SettlementMetadata,
): Record<string, unknown> {
  return { ...metadata, "a2a-se": settlement };
}

/**
 * Extract the `a2a-se` block from A2A message/task metadata.
 * Returns `undefined` if not present.
 */
export function getSettlementBlock(
  metadata: Record<string, unknown> | undefined | null,
): SettlementMetadata | undefined {
  if (!metadata || !("a2a-se" in metadata)) return undefined;
  return metadata["a2a-se"] as SettlementMetadata;
}
