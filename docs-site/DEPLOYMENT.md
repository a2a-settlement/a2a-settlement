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

`package-lock.json` is committed (the repo root `.gitignore` negates it for this
directory only) and is the record of the tree that produces the deployed site.
Use `npm ci` so the build is reproducible. Regenerating the lockfile from scratch
currently breaks the build: webpack arrives only as a transitive dependency of
`@docusaurus/core` at `^5.95.0`, and releases after 5.105.3 tightened the
`ProgressPlugin` schema so it rejects the `name` and `color` options that
`webpackbar` 6.0.1 passes. Upgrading Docusaurus is the real remedy; until then,
keep the lockfile authoritative.

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

	@docs_root path /docs /docs/
	redir @docs_root /docs/intro/ permanent

	try_files {path} {path}/
	file_server

	handle_errors 404 {
		rewrite * /404.html
		file_server {
			status 404
		}
	}
}
```

Do not add `/index.html` to `try_files`. Docusaurus pre-renders every route, so an
index fallback makes removed pages and missing assets answer `200` with homepage
HTML instead of `404` — which silently hides dangling references such as `og:image`.

## DNS

Point `docs.a2a-settlement.org` to the droplet IP. TLS is automatic via Caddy.
