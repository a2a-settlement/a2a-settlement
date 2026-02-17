from __future__ import annotations

import secrets

import bcrypt
from sqlalchemy.orm import Session

from exchange.config import SessionLocal, settings
from exchange.models import Account, Balance, Transaction


DEMO_BOTS = [
    {
        "bot_name": "SentimentBot",
        "developer_id": "dev-demo-1",
        "description": "Analyzes text sentiment.",
        "skills": ["sentiment-analysis", "text-classification"],
    },
    {
        "bot_name": "SummarizerBot",
        "developer_id": "dev-demo-2",
        "description": "Summarizes long-form content.",
        "skills": ["summarization", "text-extraction"],
    },
]


def seed(session: Session) -> None:
    print("Seeding demo accounts...")
    with session.begin():
        for bot in DEMO_BOTS:
            api_key = f"ate_{bot['bot_name'].lower()}_{secrets.token_hex(4)}"
            api_key_hash = bcrypt.hashpw(
                api_key.encode("utf-8"),
                bcrypt.gensalt(rounds=settings.api_key_salt_rounds),
            ).decode("utf-8")

            acct = Account(
                bot_name=bot["bot_name"],
                developer_id=bot["developer_id"],
                api_key_hash=api_key_hash,
                description=bot["description"],
                skills=bot["skills"],
            )
            session.add(acct)
            session.flush()

            session.add(Balance(account_id=acct.id, available=settings.starter_tokens))
            session.add(
                Transaction(
                    from_account=None,
                    to_account=acct.id,
                    amount=settings.starter_tokens,
                    tx_type="mint",
                    description="Starter token allocation (seed)",
                )
            )

            print(f"- {acct.bot_name}  id={acct.id}  api_key={api_key}")


def main() -> int:
    session = SessionLocal()
    try:
        seed(session)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

