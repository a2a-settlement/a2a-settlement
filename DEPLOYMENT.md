# Production deployment

This describes how the reference exchange at **exchange.a2a-settlement.org** is
run. To run your own exchange, see [docs/self-hosting.md](docs/self-hosting.md).
The docs site has its own note: [docs-site/DEPLOYMENT.md](docs-site/DEPLOYMENT.md).

## Layout

Production is one Linux host. The exchange runs from a git checkout of this
repo, not from a container image.

| Piece | How it runs |
|---|---|
| Exchange | systemd unit `a2a-exchange`: gunicorn with 4 uvicorn workers, bound to `127.0.0.1:8000`, as user `clawdbot` |
| Checkout | `/home/clawdbot/a2a-settlement`, branch `main`, virtualenv in `.venv/` |
| Configuration | `/etc/a2a-exchange/env`, loaded by the unit. It is not in the checkout. Variables are listed in [docs/self-hosting.md](docs/self-hosting.md) |
| Postgres | The `postgres` service from this repo's `docker-compose.yml`, container `a2a-settlement-postgres-1`, published on `127.0.0.1:5432`, data in the `a2a-settlement_pgdata` volume |
| Merkle tree | `compliance_merkle.db` (SQLite) in the checkout |
| TLS and routing | Caddy (`/etc/caddy/Caddyfile`), not nginx |
| Firewall | ufw: inbound 80, 443, and rate-limited 22 only |

Only the `postgres` service of `docker-compose.yml` is used in production. The
other services in that file (`exchange`, `dashboard`, `mediator`, `shim`, `mcp`)
are for local development.

Reference copies of the unit file and the Caddy site block are in
[`deploy/`](deploy/). The files on the host are authoritative; update the
copies when they change.

## Routing

Caddy serves `exchange.a2a-settlement.org`:

- `/` redirects to `/docs` (the OpenAPI UI).
- `/mediator/*` is proxied to the mediator on `127.0.0.1:3100`, with the prefix stripped.
- Everything else is proxied to the exchange on `127.0.0.1:8000`.
- Requests are limited to 60 per minute per client address.
- CORS is allowed from `https://a2a-settlement.org` and `https://www.a2a-settlement.org` only.

`a2a-settlement.org/api/*` is also proxied to the exchange, with the same rate limit.

## Services that depend on the exchange

These run on the same host from their own repositories:

| Service | Unit | Port | Repository |
|---|---|---|---|
| Mediator | `a2a-mediator` | 3100 | a2a-settlement-mediator |
| MCP server | `a2a-mcp` | 3200 | a2a-settlement-mcp |
| Trust gateway | `mcp-trust-gateway` | 3300 | mcp-trust-gateway |
| SettleBridge backend | `settlebridge` | 8002 | settlebridge-ai |

`a2a-mediator` and `a2a-mcp` declare `Requires=a2a-exchange.service`, so
systemd stops them when the exchange is stopped or restarted. Check both after
every exchange restart and start them if they are down.

## Releasing a change

Every production change is written as a numbered deploy plan before it is run.
A plan pins the exact commit, states what it will and will not change, and is
run one block at a time, stopping at the first failure. The shape of an
exchange release:

1. **Pre-flight (read-only).** Confirm the checkout is on the expected commit
   and branch, the working tree is in the expected state, the units are active,
   and `/health` answers 200.
2. **Back up.** Copy `/etc/a2a-exchange/env`, save any local edits as a patch,
   and create a `backup/pre-<name>-<timestamp>` branch at the current commit.
3. **Fast-forward.** Run git as `clawdbot`. Fetch `main`, check it is the
   pinned commit and a descendant of the current one, check the changed file
   list is the expected one, then `git merge --ff-only <commit>`.
4. **Restart.** `systemctl restart a2a-exchange`, wait for `/health` to answer
   200, and read the journal for tracebacks. Expect a few seconds of 502 from
   callers. Start `a2a-mediator` and `a2a-mcp` if the restart stopped them.
5. **Smoke test.** Exchange and SettleBridge health, an MCP `initialize` call,
   and any check specific to the change.
6. **Rollback** (only if the release is broken). `git reset --keep` to the
   previous commit and restart.

A plan must say so explicitly when a release changes the schema or the
dependencies, and include the steps for it.

Health check:

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","service":"a2a-settlement-exchange","version":"1.0.0","database":"ok"}
```

Logs: `journalctl -u a2a-exchange`.

## Backups

[`scripts/backup_exchange.sh`](scripts/backup_exchange.sh) dumps Postgres with
`pg_dump` and snapshots the Merkle database into
`/home/clawdbot/backups/exchange`, keeping 14 days. It is installed as
`/etc/cron.d/backup-exchange`.

## Secrets

Never print or commit the contents of `/etc/a2a-exchange/env` or any `.env`
file. Do not put host addresses, account IDs, or keys in this repository.
