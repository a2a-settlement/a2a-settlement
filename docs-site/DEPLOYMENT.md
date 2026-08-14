# Deploying docs.a2a-settlement.org

This Docusaurus site is served at **docs.a2a-settlement.org** (and aliased at **docs.settlebridge.ai**).

Production reverse proxy on the droplet is **Caddy** (`/etc/caddy/Caddyfile`), not nginx.

## Build

```bash
cd docs-site
npm ci
npm run build
```

Output: `build/` directory (static files).

## Deploy (this droplet)

```bash
cd docs-site && npm run build
rsync -a --delete build/ /var/www/docs.a2a-settlement.org/
# Caddy already serves /var/www/docs.a2a-settlement.org — no reload required for static-only updates
```

## Caddy site block (reference)

```
docs.a2a-settlement.org, docs.settlebridge.ai {
	encode zstd gzip
	root * /var/www/docs.a2a-settlement.org
	try_files {path} {path}/ /index.html
	file_server
}
```

## DNS

Point `docs.a2a-settlement.org` to the droplet IP. TLS is automatic via Caddy.
