from __future__ import annotations

import base64

import pytest

from exchange.identity.crypto import (
    canonicalize_json,
    decode_multibase_key,
    generate_keypair,
    sign_ed25519,
    verify_ed25519_signature,
)


class TestDecodeMultibaseKey:
    def test_roundtrip_via_generate(self):
        _priv, pub_mb = generate_keypair()
        raw = decode_multibase_key(pub_mb)
        assert len(raw) == 32

    def test_unsupported_prefix(self):
        with pytest.raises(ValueError, match="Unsupported multibase prefix"):
            decode_multibase_key("Mabc")

    def test_missing_multicodec(self):
        import base58 as b58

        bad = "z" + b58.b58encode(b"\x00\x00" + b"\x00" * 32).decode()
        with pytest.raises(ValueError, match="multicodec prefix"):
            decode_multibase_key(bad)

    def test_wrong_key_length(self):
        import base58 as b58

        bad = "z" + b58.b58encode(b"\xed\x01" + b"\x00" * 16).decode()
        with pytest.raises(ValueError, match="32-byte"):
            decode_multibase_key(bad)


class TestSignVerify:
    def test_roundtrip(self):
        priv, pub = generate_keypair()
        sig = sign_ed25519("hello world", priv)
        assert verify_ed25519_signature("hello world", sig, pub)

    def test_roundtrip_bytes(self):
        priv, pub = generate_keypair()
        msg = b"\x00\x01\x02binary"
        sig = sign_ed25519(msg, priv)
        assert verify_ed25519_signature(msg, sig, pub)

    def test_tampered_message(self):
        priv, pub = generate_keypair()
        sig = sign_ed25519("original", priv)
        assert not verify_ed25519_signature("tampered", sig, pub)

    def test_wrong_key(self):
        priv1, _pub1 = generate_keypair()
        _priv2, pub2 = generate_keypair()
        sig = sign_ed25519("msg", priv1)
        assert not verify_ed25519_signature("msg", sig, pub2)

    def test_invalid_signature_b64(self):
        _priv, pub = generate_keypair()
        assert not verify_ed25519_signature("msg", "not-valid-b64!!!", pub)

    def test_empty_message(self):
        priv, pub = generate_keypair()
        sig = sign_ed25519("", priv)
        assert verify_ed25519_signature("", sig, pub)


class TestGenerateKeypair:
    def test_unique_keys(self):
        _, pub1 = generate_keypair()
        _, pub2 = generate_keypair()
        assert pub1 != pub2

    def test_private_key_length(self):
        priv, _ = generate_keypair()
        assert len(priv) == 32

    def test_public_key_format(self):
        _, pub = generate_keypair()
        assert pub.startswith("z")


class TestCanonicalizeJson:
    def test_sorted_keys(self):
        assert canonicalize_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'

    def test_deterministic(self):
        d1 = {"x": 1, "y": {"b": 2, "a": 1}}
        d2 = {"y": {"a": 1, "b": 2}, "x": 1}
        assert canonicalize_json(d1) == canonicalize_json(d2)

    def test_no_whitespace(self):
        result = canonicalize_json({"key": "value"})
        assert b" " not in result
        assert b"\n" not in result

    def test_utf8(self):
        result = canonicalize_json({"emoji": "\u2764"})
        assert isinstance(result, bytes)
        assert "\\u2764" not in result.decode("utf-8")
