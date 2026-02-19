from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from compliance.models import PreDisputeAttestationPayload

EMPTY_ROOT = "0" * 64

_LEAF_DOMAIN = b"\x00"
_NODE_DOMAIN = b"\x01"


def _hash_leaf(data: bytes) -> str:
    return hashlib.sha256(_LEAF_DOMAIN + data).hexdigest()


def _hash_node(left: str, right: str) -> str:
    return hashlib.sha256(
        _NODE_DOMAIN + bytes.fromhex(left) + bytes.fromhex(right)
    ).hexdigest()


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS merkle_leaves (
    position   INTEGER PRIMARY KEY,
    data_hash  TEXT    NOT NULL,
    payload_json TEXT  NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS merkle_nodes (
    level    INTEGER NOT NULL,
    position INTEGER NOT NULL,
    hash     TEXT    NOT NULL,
    PRIMARY KEY (level, position)
);
"""


class MerkleTree:
    """Append-only, SQLite-backed Merkle tree.

    Leaves are domain-separated (``0x00 || data``) and internal nodes
    use ``0x01 || left || right`` to prevent second-preimage attacks.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def root(self) -> str:
        count = self._leaf_count()
        if count == 0:
            return EMPTY_ROOT
        return self._compute_root(count)

    @property
    def leaf_count(self) -> int:
        return self._leaf_count()

    def append(self, payload: PreDisputeAttestationPayload) -> tuple[str, int]:
        """Append a payload and return ``(new_root_hash, leaf_index)``."""
        canonical = payload.canonical_bytes()
        leaf_hash = _hash_leaf(canonical)
        position = self._leaf_count()
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT INTO merkle_leaves (position, data_hash, payload_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (position, leaf_hash, canonical.decode("utf-8"), now),
        )
        self._store_node(0, position, leaf_hash)

        new_count = position + 1
        self._rebuild_path(position, new_count)
        self._conn.commit()

        root = self._compute_root(new_count)
        return root, position

    def verify(self, leaf_index: int, data_hash: str) -> bool:
        """Verify that *data_hash* is the leaf at *leaf_index*."""
        row = self._conn.execute(
            "SELECT data_hash FROM merkle_leaves WHERE position = ?",
            (leaf_index,),
        ).fetchone()
        if row is None:
            return False
        if row[0] != data_hash:
            return False

        proof = self.get_proof(leaf_index)
        computed = data_hash
        for sibling_hash, side in proof:
            if side == "left":
                computed = _hash_node(sibling_hash, computed)
            else:
                computed = _hash_node(computed, sibling_hash)
        return computed == self.root

    def get_proof(self, leaf_index: int) -> list[tuple[str, str]]:
        """Return the audit proof for *leaf_index*.

        Each element is ``(sibling_hash, side)`` where *side* indicates
        whether the sibling is on the ``"left"`` or ``"right"``.
        """
        count = self._leaf_count()
        if leaf_index < 0 or leaf_index >= count:
            raise IndexError(f"leaf index {leaf_index} out of range [0, {count})")

        proof: list[tuple[str, str]] = []
        level = 0
        pos = leaf_index
        n = count

        while n > 1:
            if pos % 2 == 0:
                sibling_pos = pos + 1
                side = "right"
            else:
                sibling_pos = pos - 1
                side = "left"

            if sibling_pos < n:
                sibling_hash = self._get_node(level, sibling_pos)
            else:
                sibling_hash = self._get_node(level, pos)

            proof.append((sibling_hash, side))
            pos //= 2
            n = (n + 1) // 2
            level += 1

        return proof

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _leaf_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM merkle_leaves").fetchone()
        return row[0] if row else 0

    def _store_node(self, level: int, position: int, h: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO merkle_nodes (level, position, hash) "
            "VALUES (?, ?, ?)",
            (level, position, h),
        )

    def _get_node(self, level: int, position: int) -> str:
        row = self._conn.execute(
            "SELECT hash FROM merkle_nodes WHERE level = ? AND position = ?",
            (level, position),
        ).fetchone()
        if row is None:
            raise ValueError(f"missing node at level={level}, position={position}")
        return row[0]

    def _rebuild_path(self, position: int, count: int) -> None:
        """Recompute internal nodes along the path from *position* to root."""
        level = 0
        n = count
        pos = position

        while n > 1:
            parent_pos = pos // 2
            left_pos = parent_pos * 2
            right_pos = left_pos + 1

            left_hash = self._get_node(level, left_pos)
            if right_pos < n:
                right_hash = self._get_node(level, right_pos)
            else:
                right_hash = left_hash

            parent_hash = _hash_node(left_hash, right_hash)
            self._store_node(level + 1, parent_pos, parent_hash)

            pos = parent_pos
            n = (n + 1) // 2
            level += 1

    def _compute_root(self, count: int) -> str:
        if count == 0:
            return EMPTY_ROOT
        level = 0
        n = count
        while n > 1:
            n = (n + 1) // 2
            level += 1
        return self._get_node(level, 0)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MerkleTree:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
