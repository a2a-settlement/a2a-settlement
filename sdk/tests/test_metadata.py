from __future__ import annotations

# Allow running `pytest` from repo root without installing sdk/ first.
import sys
from pathlib import Path

sdk_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(sdk_root))

from a2a_settlement.metadata import build_settlement_metadata, get_settlement_block  # noqa: E402


def test_build_and_extract_settlement_block():
    md = build_settlement_metadata(
        escrow_id="escrow-1",
        amount=10,
        fee_amount=1,
        exchange_url="http://example.test/v1",
        expires_at="2026-02-17T12:30:00Z",
    )
    assert md["a2a-se"]["escrowId"] == "escrow-1"

    msg = {"metadata": md}
    block = get_settlement_block(msg)
    assert block is not None
    assert block["escrowId"] == "escrow-1"

