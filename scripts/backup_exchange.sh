#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/home/clawdbot/backups/exchange"
MERKLE_DB="/home/clawdbot/a2a-settlement/compliance_merkle.db"
TIMESTAMP=$(date -u +%Y%m%d-%H%M%S)
RETAIN_DAYS=14

mkdir -p "$BACKUP_DIR"

# --- Postgres dump via Docker ---
PG_FILE="$BACKUP_DIR/pg-${TIMESTAMP}.sql.gz"
docker exec a2a-settlement-postgres-1 \
  pg_dump -U a2a --no-owner --no-acl a2a_exchange \
  | gzip > "$PG_FILE"
echo "[$(date -u +%FT%TZ)] Postgres backup: $PG_FILE ($(du -h "$PG_FILE" | cut -f1))"

# --- Merkle tree SQLite snapshot ---
if [ -f "$MERKLE_DB" ]; then
  MERKLE_FILE="$BACKUP_DIR/merkle-${TIMESTAMP}.db"
  sqlite3 "$MERKLE_DB" ".backup '$MERKLE_FILE'"
  gzip "$MERKLE_FILE"
  echo "[$(date -u +%FT%TZ)] Merkle backup:   ${MERKLE_FILE}.gz ($(du -h "${MERKLE_FILE}.gz" | cut -f1))"
else
  echo "[$(date -u +%FT%TZ)] WARNING: Merkle DB not found at $MERKLE_DB"
fi

# --- Prune backups older than RETAIN_DAYS ---
PRUNED=$(find "$BACKUP_DIR" -name "pg-*.sql.gz" -o -name "merkle-*.db.gz" | while read f; do
  age=$(( ($(date +%s) - $(stat -c %Y "$f")) / 86400 ))
  if [ "$age" -gt "$RETAIN_DAYS" ]; then
    rm "$f"
    echo "$f"
  fi
done)

if [ -n "$PRUNED" ]; then
  echo "[$(date -u +%FT%TZ)] Pruned $(echo "$PRUNED" | wc -l) backup(s) older than ${RETAIN_DAYS}d"
fi

echo "[$(date -u +%FT%TZ)] Backup complete."
