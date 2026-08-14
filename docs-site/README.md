# A2A Settlement Documentation Hub

Centralized documentation for the A2A Settlement ecosystem. Built with [Docusaurus](https://docusaurus.io).

## What's Included

- **Big Picture** — End-to-end agent transaction lifecycle (discovery → escrow → settlement)
- **Architecture** — x402 vs AP2 vs A2A-SE, WORM Merkle trees, NIST compliance
- **Specification** — A2A-SE spec summary and links
- **API Reference** — Exchange REST API
- **SDKs & Integrations** — Python, TypeScript, ADK, CrewAI, Mediator

## Development

```bash
npm install
npm start
```

Open http://localhost:3000

## Build

```bash
npm run build
```

Output: `build/` (static files for deployment)

## Deploy to docs.a2a-settlement.org

See [DEPLOYMENT.md](./DEPLOYMENT.md) for Nginx, GitHub Pages, Vercel, and Docker options.
