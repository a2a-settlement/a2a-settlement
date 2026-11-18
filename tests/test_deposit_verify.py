"""Tests for on-chain (Tempo/x402) receipt verification on /exchange/deposit.

The Tempo RPC is stubbed, so the real receipt-decoding logic in
``exchange.tempo_verify.verify_transfer`` runs against canned chain data and
the route is exercised end to end without touching the network.
"""

from __future__ import annotations

import base64
import time

import pytest
from fastapi.testclient import TestClient

TOKEN = "0x20c0000000000000000000000000000000000000"
RECIPIENT = "0x46f4c79bdb641c009b5774d0060f2108aab85cf3"
PAYER = "0x70f4740616a2b0644ceb89bcc1e74d45ad0c87ec"
OTHER = "0x1111111111111111111111111111111111111111"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ONE_TOKEN = 1_000_000  # 1 pathUSD in base units; buys 1000 ATE


@pytest.fixture(autouse=True)
def deposit_env(request, monkeypatch):
    """Turn verification on before ``exchange_app`` loads the settings.

    A test overrides variables with
    ``@pytest.mark.parametrize("deposit_env", [{...}], indirect=True)``.
    """
    env = {
        "A2A_EXCHANGE_DEPOSIT_VERIFY": "true",
        "A2A_EXCHANGE_TEMPO_RECIPIENT": RECIPIENT,
        "A2A_EXCHANGE_TEMPO_TOKEN": TOKEN,
        "A2A_EXCHANGE_TEMPO_TOKEN_DECIMALS": "6",
        "A2A_EXCHANGE_ATE_PER_TOKEN": "1000",
        "A2A_EXCHANGE_TEMPO_MIN_CONFIRMATIONS": "1",
    }
    env.update(getattr(request, "param", {}))
    monkeypatch.delenv("A2A_EXCHANGE_DEPOSIT_REQUIRE_PROOF", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


def _transfer_log(frm: str, to: str, amount: int, token: str = TOKEN) -> dict:
    return {
        "address": token,
        "topics": [TRANSFER_TOPIC, _topic(frm), _topic(to)],
        "data": hex(amount),
    }


def _receipt(logs, status="0x1", block=24_000_000) -> dict:
    return {"status": status, "blockNumber": hex(block), "logs": logs}


def _stub_rpc(monkeypatch, receipt, head_block=24_000_005, mined_at=None):
    """Stub the JSON-RPC layer used by verify_transfer."""
    import exchange.tempo_verify as tv

    timestamp = int(time.time()) + 60 if mined_at is None else mined_at

    def fake_rpc(rpc_url, method, params, timeout):
        if method == "eth_getTransactionReceipt":
            return receipt
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(timestamp)}
        if method == "eth_blockNumber":
            return hex(head_block)
        raise AssertionError(f"unexpected rpc method {method}")

    monkeypatch.setattr(tv, "_rpc", fake_rpc)


def _register(client, name: str = "DepositBot") -> str:
    resp = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": name,
            "developer_id": f"dev-{name.lower()}",
            "developer_name": "Test Dev",
            "contact_email": "test@test.dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["api_key"]


def _link(client, auth_header, api_key, address=PAYER):
    return client.post(
        "/v1/accounts/link-wallet",
        headers=auth_header(api_key),
        json={"tempo_address": address},
    )


def _deposit(client, auth_header, api_key, n, **proof):
    return client.post(
        "/v1/exchange/deposit",
        headers=auth_header(api_key),
        json={"amount": 1, "payment_proof": {"tx_hash": _tx(n), **proof}},
    )


def _available(client, auth_header, api_key) -> int:
    return client.get("/v1/exchange/balance", headers=auth_header(api_key)).json()[
        "available"
    ]


def _tx(n: int) -> str:
    return "0x" + f"{n:064x}"


def test_verified_deposit_credits_ate(exchange_app, monkeypatch, auth_header):
    _stub_rpc(monkeypatch, _receipt([_transfer_log(PAYER, RECIPIENT, ONE_TOKEN)]))
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        assert _link(client, auth_header, api_key).status_code == 200
        resp = _deposit(client, auth_header, api_key, 1, network="tempo-testnet")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # 1 pathUSD * 1000 ATE/token = 1000 ATE, on top of 100 starter tokens.
        assert body["verified"] is True
        assert body["amount"] == 1000
        assert body["new_balance"] == 1100
        assert body["payment_tx"] == _tx(1)


def test_replay_is_rejected(exchange_app, monkeypatch, auth_header):
    _stub_rpc(monkeypatch, _receipt([_transfer_log(PAYER, RECIPIENT, ONE_TOKEN)]))
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        _link(client, auth_header, api_key)
        assert _deposit(client, auth_header, api_key, 2).status_code == 201
        second = _deposit(client, auth_header, api_key, 2)
        assert second.status_code == 409, second.text
        assert _available(client, auth_header, api_key) == 1100


def test_wrong_recipient_rejected(exchange_app, monkeypatch, auth_header):
    _stub_rpc(monkeypatch, _receipt([_transfer_log(PAYER, OTHER, ONE_TOKEN)]))
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        _link(client, auth_header, api_key)
        assert _deposit(client, auth_header, api_key, 3).status_code == 402


def test_reverted_tx_rejected(exchange_app, monkeypatch, auth_header):
    _stub_rpc(
        monkeypatch,
        _receipt([_transfer_log(PAYER, RECIPIENT, ONE_TOKEN)], status="0x0"),
    )
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        _link(client, auth_header, api_key)
        assert _deposit(client, auth_header, api_key, 4).status_code == 402


def test_amount_too_small_rejected(exchange_app, monkeypatch, auth_header):
    # 100 base units * 1000 / 1e6 = 0 ATE.
    _stub_rpc(monkeypatch, _receipt([_transfer_log(PAYER, RECIPIENT, 100)]))
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        _link(client, auth_header, api_key)
        assert _deposit(client, auth_header, api_key, 5).status_code == 422


@pytest.mark.parametrize(
    "deposit_env", [{"A2A_EXCHANGE_TEMPO_MIN_CONFIRMATIONS": "10"}], indirect=True
)
def test_unconfirmed_tx_rejected(exchange_app, monkeypatch, auth_header):
    # Head is 2 blocks ahead: 3 confirmations, 10 required.
    _stub_rpc(
        monkeypatch,
        _receipt([_transfer_log(PAYER, RECIPIENT, ONE_TOKEN)], block=24_000_000),
        head_block=24_000_002,
    )
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        _link(client, auth_header, api_key)
        assert _deposit(client, auth_header, api_key, 6).status_code == 402


def test_wrong_network_rejected(exchange_app, monkeypatch, auth_header):
    _stub_rpc(monkeypatch, _receipt([_transfer_log(PAYER, RECIPIENT, ONE_TOKEN)]))
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        _link(client, auth_header, api_key)
        resp = _deposit(client, auth_header, api_key, 7, network="tempo-mainnet")
        assert resp.status_code == 400, resp.text


# --- The payment must be bound to the account -------------------------------


def test_deposit_without_a_linked_wallet_is_rejected(
    exchange_app, monkeypatch, auth_header
):
    """Otherwise anyone who saw the transaction on-chain could claim it."""
    _stub_rpc(monkeypatch, _receipt([_transfer_log(PAYER, RECIPIENT, ONE_TOKEN)]))
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        resp = _deposit(client, auth_header, api_key, 8)
        assert resp.status_code == 403, resp.text
        assert _available(client, auth_header, api_key) == 100


def test_payment_from_another_wallet_is_rejected(
    exchange_app, monkeypatch, auth_header
):
    _stub_rpc(monkeypatch, _receipt([_transfer_log(PAYER, RECIPIENT, ONE_TOKEN)]))
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        assert _link(client, auth_header, api_key, OTHER).status_code == 200
        resp = _deposit(client, auth_header, api_key, 9)
        assert resp.status_code == 403, resp.text
        assert _available(client, auth_header, api_key) == 100


def test_another_account_cannot_claim_the_payment(
    exchange_app, monkeypatch, auth_header
):
    _stub_rpc(monkeypatch, _receipt([_transfer_log(PAYER, RECIPIENT, ONE_TOKEN)]))
    with TestClient(exchange_app) as client:
        payer_key = _register(client, "PayerBot")
        thief_key = _register(client, "ThiefBot")
        assert _link(client, auth_header, payer_key, PAYER).status_code == 200

        # The payer's address cannot be linked to a second account.
        taken = _link(client, auth_header, thief_key, PAYER)
        assert taken.status_code == 409, taken.text
        # Mixed case is the same address.
        assert _link(client, auth_header, thief_key, PAYER.upper().replace("0X", "0x")).status_code == 409

        # With no wallet, or its own wallet, the thief cannot claim the tx.
        assert _deposit(client, auth_header, thief_key, 10).status_code == 403
        assert _link(client, auth_header, thief_key, OTHER).status_code == 200
        assert _deposit(client, auth_header, thief_key, 10).status_code == 403
        assert _available(client, auth_header, thief_key) == 100

        # The payer still can.
        assert _deposit(client, auth_header, payer_key, 10).status_code == 201
        assert _available(client, auth_header, payer_key) == 1100


def test_payment_made_before_the_wallet_was_linked_is_rejected(
    exchange_app, monkeypatch, auth_header
):
    """Linking an address after seeing its payment must not earn the credit."""
    _stub_rpc(
        monkeypatch,
        _receipt([_transfer_log(PAYER, RECIPIENT, ONE_TOKEN)]),
        mined_at=int(time.time()) - 3600,
    )
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        _link(client, auth_header, api_key)
        resp = _deposit(client, auth_header, api_key, 11)
        assert resp.status_code == 403, resp.text
        assert _available(client, auth_header, api_key) == 100


def test_relinking_the_same_wallet_keeps_the_link_time(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        first = _link(client, auth_header, api_key).json()
        second = _link(client, auth_header, api_key).json()
        assert first["tempo_address"] == PAYER
        assert second["linked_at"] == first["linked_at"]


def test_only_transfers_from_the_expected_payer_count(monkeypatch):
    import exchange.tempo_verify as tv

    _stub_rpc(
        monkeypatch,
        _receipt(
            [
                _transfer_log(PAYER, RECIPIENT, 400),
                _transfer_log(OTHER, RECIPIENT, 5_000),
                _transfer_log(PAYER, RECIPIENT, 600),
                _transfer_log(PAYER, RECIPIENT, 9_999, token=OTHER),
            ]
        ),
    )
    verified = tv.verify_transfer(
        tx_hash=_tx(12),
        recipient=RECIPIENT,
        token=TOKEN,
        expected_payer=PAYER,
        rpc_url="http://stub",
    )
    assert verified.amount == 1_000
    assert verified.payer == PAYER


# --- The unverified path ----------------------------------------------------


def test_deposit_without_proof_is_rejected_when_verification_is_on(
    exchange_app, auth_header
):
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        resp = client.post(
            "/v1/exchange/deposit", headers=auth_header(api_key), json={"amount": 50}
        )
        assert resp.status_code == 402, resp.text
        assert _available(client, auth_header, api_key) == 100


@pytest.mark.parametrize(
    "deposit_env", [{"A2A_EXCHANGE_DEPOSIT_REQUIRE_PROOF": "false"}], indirect=True
)
def test_unverified_path_can_be_kept_open_explicitly(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        resp = client.post(
            "/v1/exchange/deposit", headers=auth_header(api_key), json={"amount": 50}
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["new_balance"] == 150
        assert resp.json()["verified"] is False


@pytest.mark.parametrize(
    "deposit_env", [{"A2A_EXCHANGE_DEPOSIT_VERIFY": "false"}], indirect=True
)
def test_behaviour_is_unchanged_when_verification_is_off(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        resp = client.post(
            "/v1/exchange/deposit", headers=auth_header(api_key), json={"amount": 50}
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["new_balance"] == 150

        # The on-chain endpoints are off.
        assert _deposit(client, auth_header, api_key, 13).status_code == 503
        assert _link(client, auth_header, api_key).status_code == 503
        quote = client.post(
            "/v1/exchange/deposit/quote", headers=auth_header(api_key), json={"ate": 1}
        )
        assert quote.status_code == 503


def test_quote_returns_challenge(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        api_key = _register(client)
        resp = client.post(
            "/v1/exchange/deposit/quote",
            headers=auth_header(api_key),
            json={"ate": 1000},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 1000 ATE / 1000 ATE-per-token = 1 pathUSD = 1_000_000 base units.
        assert body["amount_base_units"] == "1000000"
        assert body["recipient"] == RECIPIENT
        assert 'method="tempo"' in body["challenge"]
        request_b64 = body["challenge"].split('request="')[1].split('"')[0]
        assert RECIPIENT in base64.b64decode(request_b64 + "==").decode()
