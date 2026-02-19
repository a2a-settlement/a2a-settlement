from __future__ import annotations

import hashlib
import sqlite3

import pytest

from compliance.merkle import EMPTY_ROOT, MerkleTree, _hash_leaf
from compliance.models import (
    AP2MandateBinding,
    AttestationHeader,
    MediationState,
    PreDisputeAttestationPayload,
)


def _payload(escrow_id: str = "esc-001") -> PreDisputeAttestationPayload:
    return PreDisputeAttestationPayload(
        header=AttestationHeader(issuer_id="agent-001"),
        mandate=AP2MandateBinding(
            intent_did="did:example:intent:1",
            cart_did="did:example:cart:2",
            payment_did="did:example:pay:3",
        ),
        mediation=MediationState(escrow_id=escrow_id, escrow_status="held"),
    )


class TestEmptyTree:
    def test_root_is_empty_sentinel(self, tmp_path):
        with MerkleTree(tmp_path / "empty.db") as tree:
            assert tree.root == EMPTY_ROOT

    def test_leaf_count_zero(self, tmp_path):
        with MerkleTree(tmp_path / "empty.db") as tree:
            assert tree.leaf_count == 0


class TestSingleLeaf:
    def test_append_returns_root_and_index(self, tmp_path):
        with MerkleTree(tmp_path / "one.db") as tree:
            root, idx = tree.append(_payload())
            assert idx == 0
            assert root != EMPTY_ROOT
            assert len(root) == 64  # hex SHA-256

    def test_root_equals_leaf_hash(self, tmp_path):
        p = _payload()
        with MerkleTree(tmp_path / "one.db") as tree:
            root, _ = tree.append(p)
            expected = _hash_leaf(p.canonical_bytes())
            assert root == expected

    def test_verify_single(self, tmp_path):
        p = _payload()
        with MerkleTree(tmp_path / "one.db") as tree:
            _, idx = tree.append(p)
            leaf_hash = _hash_leaf(p.canonical_bytes())
            assert tree.verify(idx, leaf_hash)

    def test_get_proof_single(self, tmp_path):
        with MerkleTree(tmp_path / "one.db") as tree:
            tree.append(_payload())
            proof = tree.get_proof(0)
            assert proof == []


class TestMultipleLeaves:
    def test_root_changes_on_each_append(self, tmp_path):
        with MerkleTree(tmp_path / "multi.db") as tree:
            roots = []
            for i in range(5):
                root, _ = tree.append(_payload(f"esc-{i}"))
                roots.append(root)
            assert len(set(roots)) == 5

    def test_verify_all_leaves(self, tmp_path):
        payloads = [_payload(f"esc-{i}") for i in range(7)]
        with MerkleTree(tmp_path / "verify.db") as tree:
            for p in payloads:
                tree.append(p)
            for i, p in enumerate(payloads):
                leaf_hash = _hash_leaf(p.canonical_bytes())
                assert tree.verify(i, leaf_hash), f"leaf {i} failed verification"

    def test_proof_verifies_externally(self, tmp_path):
        """Manually walk the proof to confirm correctness."""
        payloads = [_payload(f"esc-{i}") for i in range(4)]
        with MerkleTree(tmp_path / "proof.db") as tree:
            for p in payloads:
                tree.append(p)

            for i in range(4):
                leaf_hash = _hash_leaf(payloads[i].canonical_bytes())
                proof = tree.get_proof(i)
                computed = leaf_hash
                for sibling, side in proof:
                    if side == "left":
                        computed = hashlib.sha256(
                            b"\x01" + bytes.fromhex(sibling) + bytes.fromhex(computed)
                        ).hexdigest()
                    else:
                        computed = hashlib.sha256(
                            b"\x01" + bytes.fromhex(computed) + bytes.fromhex(sibling)
                        ).hexdigest()
                assert computed == tree.root

    def test_wrong_hash_fails_verify(self, tmp_path):
        with MerkleTree(tmp_path / "bad.db") as tree:
            tree.append(_payload())
            assert not tree.verify(0, "0" * 64)

    def test_out_of_range_index_raises(self, tmp_path):
        with MerkleTree(tmp_path / "range.db") as tree:
            tree.append(_payload())
            with pytest.raises(IndexError):
                tree.get_proof(5)


class TestPersistence:
    def test_survives_reopen(self, tmp_path):
        db = tmp_path / "persist.db"
        p = _payload()

        tree1 = MerkleTree(db)
        root1, _ = tree1.append(p)
        tree1.close()

        tree2 = MerkleTree(db)
        assert tree2.root == root1
        assert tree2.leaf_count == 1
        leaf_hash = _hash_leaf(p.canonical_bytes())
        assert tree2.verify(0, leaf_hash)
        tree2.close()


class TestAppendOnlyEnforcement:
    def test_no_update_on_leaves(self, tmp_path):
        db = tmp_path / "readonly.db"
        with MerkleTree(db) as tree:
            tree.append(_payload())

        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO merkle_leaves (position, data_hash, payload_json, created_at) "
                "VALUES (0, 'tampered', '{}', '2024-01-01')"
            )
        conn.close()
