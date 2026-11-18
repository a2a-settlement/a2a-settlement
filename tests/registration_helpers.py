"""Registration helper for tests that put several agents under one developer_id.

A legacy_open developer_id that already has an agent only accepts new
registrations carrying the API key of an existing agent in that ID.
"""

from __future__ import annotations


def post_register(client, payload: dict):
    """POST /v1/accounts/register, passing an earlier member's key for a reused developer_id."""
    keys = client.__dict__.setdefault("_member_keys", {})
    developer_id = payload.get("developer_id")
    if developer_id in keys and "owner_api_key" not in payload:
        payload = {**payload, "owner_api_key": keys[developer_id]}
    resp = client.post("/v1/accounts/register", json=payload)
    if resp.status_code == 201:
        keys.setdefault(developer_id, resp.json()["api_key"])
    return resp
