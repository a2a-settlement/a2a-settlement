from __future__ import annotations

import math
from typing import Any


class PricingError(ValueError):
    pass


def compute_price_tokens(
    pricing_entry: dict[str, Any],
    *,
    units: int | None = None,
    minutes: float | None = None,
) -> int:
    """Compute token price from an A2A-SE pricing entry."""

    model = pricing_entry.get("model")
    base_tokens = int(pricing_entry.get("baseTokens", 0))
    if base_tokens <= 0:
        raise PricingError("pricing.baseTokens must be > 0")

    if model == "per-request":
        return base_tokens

    if model == "per-unit":
        if units is None:
            raise PricingError("units is required for per-unit pricing")
        unit_size = int(pricing_entry.get("unitSize", 1000))
        if unit_size <= 0:
            raise PricingError("pricing.unitSize must be > 0")
        return int(math.ceil((units / unit_size) * base_tokens))

    if model == "per-minute":
        if minutes is None:
            raise PricingError("minutes is required for per-minute pricing")
        return int(math.ceil(float(minutes) * base_tokens))

    if model == "negotiable":
        raise PricingError("negotiable pricing requires an out-of-band negotiation step")

    raise PricingError(f"Unknown pricing model: {model!r}")

