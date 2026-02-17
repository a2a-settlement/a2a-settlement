from __future__ import annotations

import uvicorn

from exchange.config import settings


def main() -> int:
    uvicorn.run(
        "exchange.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

