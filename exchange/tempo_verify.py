"""On-chain verification of Tempo (x402 / MPP) payment receipts.

This module lets the exchange independently verify that a payment claimed by
a depositor actually landed on the Tempo chain — paid to the exchange's
address, in the expected token, from the expected wallet, and with enough
confirmations. It only trusts the chain (queried via JSON-RPC), never the
caller, and uses the Python standard library so it adds no dependencies.

It also builds the MPP "push" payment challenge that the exchange hands back
in a quote response so an mppx client can pay it.
"""

from __future__ import annotations

import base64
import json
import urllib.request
from dataclasses import dataclass

# keccak256("Transfer(address,address,uint256)")
_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)


class TempoVerificationError(Exception):
    """Raised when a payment receipt cannot be verified on-chain."""


class PayerMismatchError(TempoVerificationError):
    """The transaction pays the exchange, but not from the expected wallet."""


@dataclass
class VerifiedPayment:
    tx_hash: str
    token: str
    payer: str
    recipient: str
    amount: int  # token base units (e.g. pathUSD: 6 decimals)
    block_number: int
    block_timestamp: int  # seconds since the epoch, from the block header
    confirmations: int


def _rpc(rpc_url: str, method: str, params: list, timeout: int) -> object:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "a2a-settlement-exchange/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network / decode errors
        raise TempoVerificationError(f"RPC call {method} failed: {exc}") from exc
    if isinstance(body, dict) and body.get("error"):
        raise TempoVerificationError(f"RPC error for {method}: {body['error']}")
    return body.get("result") if isinstance(body, dict) else None


def _topic_to_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def verify_transfer(
    *,
    tx_hash: str,
    recipient: str,
    token: str,
    expected_payer: str,
    rpc_url: str,
    min_confirmations: int = 1,
    min_amount: int | None = None,
    timeout: int = 20,
) -> VerifiedPayment:
    """Verify a TIP-20 transfer of ``token`` from ``expected_payer`` to ``recipient``.

    Only transfers sent by ``expected_payer`` count toward the amount. Raises
    :class:`PayerMismatchError` when the transaction pays ``recipient`` but
    from another wallet, and :class:`TempoVerificationError` when it is
    missing, reverted, under-confirmed, or holds no matching transfer.
    """
    if not (isinstance(tx_hash, str) and tx_hash.startswith("0x") and len(tx_hash) == 66):
        raise TempoVerificationError("Malformed transaction hash")

    recipient_l = recipient.lower()
    token_l = token.lower()
    payer_l = expected_payer.lower()

    receipt = _rpc(rpc_url, "eth_getTransactionReceipt", [tx_hash], timeout)
    if not isinstance(receipt, dict):
        raise TempoVerificationError(
            "Transaction not found or not yet mined on Tempo"
        )

    status = str(receipt.get("status", "")).lower()
    if status not in ("0x1", "1"):
        raise TempoVerificationError(f"Transaction did not succeed (status={status})")

    block_hex = receipt["blockNumber"]
    block_number = int(block_hex, 16)

    total = 0
    from_others = 0
    for log in receipt.get("logs", []):
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        if topics[0].lower() != _TRANSFER_TOPIC:
            continue
        if (log.get("address") or "").lower() != token_l:
            continue
        if _topic_to_address(topics[2]) != recipient_l:
            continue
        amount = int(log.get("data", "0x0"), 16)
        if amount <= 0:
            continue
        if _topic_to_address(topics[1]) == payer_l:
            total += amount
        else:
            from_others += amount

    if total <= 0:
        if from_others > 0:
            raise PayerMismatchError(
                "Payment sender does not match the wallet linked to this account"
            )
        raise TempoVerificationError(
            "No matching token transfer to the exchange address in this transaction"
        )

    if min_amount is not None and total < min_amount:
        raise TempoVerificationError(
            f"Paid amount {total} is below the required {min_amount} base units"
        )

    block = _rpc(rpc_url, "eth_getBlockByNumber", [block_hex, False], timeout)
    if not isinstance(block, dict) or not block.get("timestamp"):
        raise TempoVerificationError("Could not read the block for this transaction")
    block_timestamp = int(block["timestamp"], 16)

    head = _rpc(rpc_url, "eth_blockNumber", [], timeout)
    if not isinstance(head, str):
        raise TempoVerificationError("Could not read the chain head")
    confirmations = max(0, int(head, 16) - block_number + 1)
    if confirmations < min_confirmations:
        raise TempoVerificationError(
            f"Only {confirmations} confirmation(s); need {min_confirmations}"
        )

    return VerifiedPayment(
        tx_hash=tx_hash.lower(),
        token=token_l,
        payer=payer_l,
        recipient=recipient_l,
        amount=total,
        block_number=block_number,
        block_timestamp=block_timestamp,
        confirmations=confirmations,
    )


def build_push_challenge(
    *,
    challenge_id: str,
    realm: str,
    recipient: str,
    token: str,
    chain_id: int,
    amount_base_units: int,
    description: str,
    expires_iso: str,
) -> str:
    """Build a WWW-Authenticate 'Payment' challenge value for mppx (push mode).

    Push mode means the client broadcasts the payment itself and presents the
    resulting tx hash, so the exchange never needs a private key or facilitator.
    """
    request_obj = {
        "amount": str(amount_base_units),
        "currency": token,
        "methodDetails": {"chainId": chain_id, "supportedModes": ["push"]},
        "recipient": recipient,
    }
    request_b64 = (
        base64.b64encode(json.dumps(request_obj, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    return (
        f'Payment id="{challenge_id}", realm="{realm}", method="tempo", '
        f'intent="charge", request="{request_b64}", '
        f'description="{description}", expires="{expires_iso}"'
    )
