"""Tests for the Security Shim (Economic Air Gap -- Component 3)."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch

from shim.config import ShimSettings
from shim.cost_model import FlatFeeCostModel, PerDestinationCostModel
from shim.credential_injector import CredentialInjector
from shim.escrow_gate import (
    EscrowGate,
    EscrowNotFoundError,
    InsufficientEscrowError,
)
from shim.models import ProxyRequest, ToolDefinition, ShimAuditEntry
from shim.proxy import ShimProxy
from shim.tool_registry import ToolRegistry, ToolNotFoundError


# ─── Cost Model Tests ──────────────────────────────────────────────────────


class TestFlatFeeCostModel:
    def test_returns_fixed_fee(self):
        model = FlatFeeCostModel(fee=2.5)
        req = ProxyRequest(escrow_id="e-1")
        assert model.compute_cost(req, "https://api.github.com/repos") == 2.5

    def test_default_fee_is_one(self):
        model = FlatFeeCostModel()
        req = ProxyRequest(escrow_id="e-1")
        assert model.compute_cost(req, "https://example.com") == 1.0


class TestPerDestinationCostModel:
    def test_exact_match(self):
        model = PerDestinationCostModel(
            default_fee=1.0,
            overrides={"api.github.com": 3.0},
        )
        req = ProxyRequest(escrow_id="e-1")
        assert model.compute_cost(req, "https://api.github.com/repos") == 3.0

    def test_suffix_match(self):
        model = PerDestinationCostModel(
            default_fee=1.0,
            overrides={"amazonaws.com": 5.0},
        )
        req = ProxyRequest(escrow_id="e-1")
        assert model.compute_cost(req, "https://lambda.us-east-1.amazonaws.com/fn") == 5.0

    def test_fallback_to_default(self):
        model = PerDestinationCostModel(
            default_fee=1.0,
            overrides={"api.github.com": 3.0},
        )
        req = ProxyRequest(escrow_id="e-1")
        assert model.compute_cost(req, "https://hooks.slack.com/webhook") == 1.0


# ─── Tool Registry Tests ──────────────────────────────────────────────────


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = ToolDefinition(
            tool_id="github-create-issue",
            destination_url="https://api.github.com/repos/owner/repo/issues",
            method="POST",
            secret_id="sec_github_pat",
        )
        registry.register(tool)
        resolved = registry.get("github-create-issue")
        assert resolved.destination_url == "https://api.github.com/repos/owner/repo/issues"
        assert resolved.secret_id == "sec_github_pat"

    def test_not_found_raises(self):
        registry = ToolRegistry()
        with pytest.raises(ToolNotFoundError, match="not registered"):
            registry.get("nonexistent")

    def test_unregister(self):
        registry = ToolRegistry()
        tool = ToolDefinition(tool_id="t1", destination_url="https://example.com")
        registry.register(tool)
        registry.unregister("t1")
        with pytest.raises(ToolNotFoundError):
            registry.get("t1")

    def test_list_tools(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(tool_id="t1", destination_url="https://a.com"))
        registry.register(ToolDefinition(tool_id="t2", destination_url="https://b.com"))
        assert len(registry.list_tools()) == 2


# ─── Escrow Gate Tests ─────────────────────────────────────────────────────


class TestEscrowGate:
    def test_register_and_deduct(self):
        gate = EscrowGate(cost_model=FlatFeeCostModel(2.0))
        gate.register_escrow("e-1", amount=10)
        req = ProxyRequest(escrow_id="e-1")
        cost = gate.check_and_deduct(req, "https://example.com")
        assert cost == 2.0
        status = gate.get_status("e-1")
        assert status.remaining == 8.0

    def test_insufficient_balance_raises(self):
        gate = EscrowGate(cost_model=FlatFeeCostModel(5.0))
        gate.register_escrow("e-1", amount=3)
        req = ProxyRequest(escrow_id="e-1")
        with pytest.raises(InsufficientEscrowError) as exc_info:
            gate.check_and_deduct(req, "https://example.com")
        assert exc_info.value.required == 5.0
        assert exc_info.value.available == 3.0

    def test_not_found_raises(self):
        gate = EscrowGate()
        req = ProxyRequest(escrow_id="e-nonexistent")
        with pytest.raises(EscrowNotFoundError):
            gate.check_and_deduct(req, "https://example.com")

    def test_depletes_over_multiple_calls(self):
        gate = EscrowGate(cost_model=FlatFeeCostModel(1.0))
        gate.register_escrow("e-1", amount=3)
        req = ProxyRequest(escrow_id="e-1")

        gate.check_and_deduct(req, "https://example.com")
        gate.check_and_deduct(req, "https://example.com")
        gate.check_and_deduct(req, "https://example.com")

        with pytest.raises(InsufficientEscrowError):
            gate.check_and_deduct(req, "https://example.com")

    def test_cost_override(self):
        gate = EscrowGate(cost_model=FlatFeeCostModel(1.0))
        gate.register_escrow("e-1", amount=100)
        req = ProxyRequest(escrow_id="e-1")
        cost = gate.check_and_deduct(req, "https://example.com", cost_override=7.5)
        assert cost == 7.5


# ─── Credential Injector Tests ────────────────────────────────────────────


class TestCredentialInjector:
    def test_inject_bearer(self):
        injector = CredentialInjector()
        headers, url, body = injector.inject(
            credential="ghp_abc123",
            headers={},
            url="https://api.github.com/repos",
            body=None,
            inject_as="bearer",
            inject_key="Authorization",
        )
        assert headers["Authorization"] == "Bearer ghp_abc123"

    def test_inject_header(self):
        injector = CredentialInjector()
        headers, url, body = injector.inject(
            credential="xoxb-slack-token",
            headers={},
            url="https://hooks.slack.com/webhook",
            body=None,
            inject_as="header",
            inject_key="X-Slack-Token",
        )
        assert headers["X-Slack-Token"] == "xoxb-slack-token"

    def test_inject_query(self):
        injector = CredentialInjector()
        headers, url, body = injector.inject(
            credential="my_api_key",
            headers={},
            url="https://api.example.com/data",
            body=None,
            inject_as="query",
            inject_key="api_key",
        )
        assert "api_key=my_api_key" in url

    def test_inject_body(self):
        injector = CredentialInjector()
        headers, url, body = injector.inject(
            credential="secret_token",
            headers={},
            url="https://api.example.com",
            body='{"text": "hello"}',
            inject_as="body",
            inject_key="token",
        )
        parsed = json.loads(body)
        assert parsed["token"] == "secret_token"
        assert parsed["text"] == "hello"


# ─── Destination Policy Tests ─────────────────────────────────────────────


class TestDestinationPolicy:
    def test_allow_mode_no_list_allows_all(self):
        settings = ShimSettings()
        settings.destination_mode = "allow"
        settings.destination_list = []
        assert settings.is_destination_allowed("https://anything.com/path") is True

    def test_allow_mode_with_blocklist(self):
        settings = ShimSettings()
        settings.destination_mode = "allow"
        settings.destination_list = ["evil.com"]
        assert settings.is_destination_allowed("https://good.com") is True
        assert settings.is_destination_allowed("https://evil.com/api") is False
        assert settings.is_destination_allowed("https://sub.evil.com/api") is False

    def test_deny_mode_with_allowlist(self):
        settings = ShimSettings()
        settings.destination_mode = "deny"
        settings.destination_list = ["api.github.com", "hooks.slack.com"]
        assert settings.is_destination_allowed("https://api.github.com/repos") is True
        assert settings.is_destination_allowed("https://hooks.slack.com/wh") is True
        assert settings.is_destination_allowed("https://evil.com") is False

    def test_deny_mode_empty_list_blocks_all(self):
        settings = ShimSettings()
        settings.destination_mode = "deny"
        settings.destination_list = []
        assert settings.is_destination_allowed("https://anything.com") is False


# ─── Full Proxy Pipeline Tests ─────────────────────────────────────────────


class TestShimProxy:
    def _make_proxy(self, vault=None) -> tuple[ShimProxy, EscrowGate, ToolRegistry]:
        gate = EscrowGate(cost_model=FlatFeeCostModel(1.0))
        registry = ToolRegistry()
        injector = CredentialInjector(vault=vault)
        audit_log: list[ShimAuditEntry] = []
        proxy = ShimProxy(
            escrow_gate=gate,
            tool_registry=registry,
            credential_injector=injector,
            audit_log=audit_log,
        )
        return proxy, gate, registry

    @pytest.mark.asyncio
    async def test_direct_mode_success(self):
        proxy, gate, _ = self._make_proxy()
        gate.register_escrow("e-1", amount=10)

        with patch.object(proxy, "_forward", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = (200, {"content-type": "application/json"}, '{"ok": true}')

            req = ProxyRequest(
                escrow_id="e-1",
                destination_url="https://api.github.com/repos",
                method="GET",
                agent_id="bot-1",
            )
            resp = await proxy.handle(req)

        assert resp.status_code == 200
        assert resp.cost_charged == 1.0
        assert resp.escrow_remaining == 9.0

    @pytest.mark.asyncio
    async def test_tool_id_mode_success(self):
        proxy, gate, registry = self._make_proxy()
        gate.register_escrow("e-1", amount=10)
        registry.register(ToolDefinition(
            tool_id="github-list-repos",
            destination_url="https://api.github.com/user/repos",
            method="GET",
        ))

        with patch.object(proxy, "_forward", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = (200, {}, '[]')

            req = ProxyRequest(
                escrow_id="e-1",
                tool_id="github-list-repos",
                agent_id="bot-1",
            )
            resp = await proxy.handle(req)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_escrow_depletes_returns_402(self):
        proxy, gate, _ = self._make_proxy()
        gate.register_escrow("e-1", amount=2)

        with patch.object(proxy, "_forward", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = (200, {}, '{"ok": true}')

            req = ProxyRequest(
                escrow_id="e-1",
                destination_url="https://api.github.com/repos",
                agent_id="bot-1",
            )

            # Two calls succeed (cost 1 each)
            resp1 = await proxy.handle(req)
            assert resp1.status_code == 200
            resp2 = await proxy.handle(req)
            assert resp2.status_code == 200

            # Third call: 402 Payment Required
            resp3 = await proxy.handle(req)
            assert resp3.status_code == 402
            assert "insufficient" in json.loads(resp3.body)["error"].lower() or "need" in json.loads(resp3.body)["error"].lower()

    @pytest.mark.asyncio
    async def test_tool_not_found_returns_404(self):
        proxy, gate, _ = self._make_proxy()
        gate.register_escrow("e-1", amount=10)

        req = ProxyRequest(
            escrow_id="e-1",
            tool_id="nonexistent-tool",
            agent_id="bot-1",
        )
        resp = await proxy.handle(req)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_destination_returns_400(self):
        proxy, gate, _ = self._make_proxy()
        gate.register_escrow("e-1", amount=10)

        req = ProxyRequest(escrow_id="e-1", agent_id="bot-1")
        resp = await proxy.handle(req)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_audit_log_records_all_requests(self):
        proxy, gate, _ = self._make_proxy()
        gate.register_escrow("e-1", amount=5)

        with patch.object(proxy, "_forward", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = (200, {}, "ok")

            for _ in range(3):
                await proxy.handle(ProxyRequest(
                    escrow_id="e-1",
                    destination_url="https://api.github.com",
                    agent_id="bot-1",
                ))

        assert len(proxy.audit_log) == 3
        assert all(e.agent_id == "bot-1" for e in proxy.audit_log)
        assert all(e.destination == "https://api.github.com" for e in proxy.audit_log)

    @pytest.mark.asyncio
    async def test_tool_cost_override(self):
        proxy, gate, registry = self._make_proxy()
        gate.register_escrow("e-1", amount=100)
        registry.register(ToolDefinition(
            tool_id="expensive-tool",
            destination_url="https://api.expensive.com",
            cost_override=25.0,
        ))

        with patch.object(proxy, "_forward", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = (200, {}, "ok")

            resp = await proxy.handle(ProxyRequest(
                escrow_id="e-1",
                tool_id="expensive-tool",
                agent_id="bot-1",
            ))

        assert resp.cost_charged == 25.0
        assert resp.escrow_remaining == 75.0


class TestRequestPayment:
    @pytest.mark.asyncio
    async def test_actor_destination_is_denied(self):
        from shim.payment_tool import register_request_payment

        proxy, gate, registry = TestShimProxy._make_proxy(self)
        gate.register_escrow("e-1", amount=10)
        register_request_payment(registry, "http://exchange:8000", "requester-secret")
        resp = await proxy.handle(ProxyRequest(
            escrow_id="e-1",
            tool_id="request_payment",
            destination_url="http://evil.example/exchange/release",
            agent_id="supplier",
        ))
        assert resp.status_code == 403
        tool = registry.get("request_payment")
        assert tool.destination_url == "http://exchange:8000/exchange/release"
        assert tool.secret_id == "requester-secret"
