#!/usr/bin/env python
"""Interactive REPL to test repositories using Python repos."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.core.config import get_settings
from app.db.models import User
from app.domain.batch import BatchCreate, BatchStatus
from app.domain.prediction import DocumentLabel, PredictionCreate, PredictionUpdate
from app.domain.user import UserCreate
from app.repositories.batch_repo import BatchRepository
from app.repositories.prediction_repo import PredictionRepository

# User repository (simple wrapper, not using UserManager for clarity)
class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, email: str, role: str) -> User:
        user = User(
            id=uuid.uuid4(),
            email=email,
            role=role,
            hashed_password="fake_hashed_for_testing",  # only for manual testing
            is_active=True,
            is_superuser=False,
            is_verified=False,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def get(self, user_id: uuid.UUID) -> User | None:
        from sqlalchemy import select
        stmt = select(User).where(User.id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self) -> list[User]:
        from sqlalchemy import select
        stmt = select(User)
        result = await self.session.execute(stmt)
        return result.scalars().all()


# Global engine and session (recreated each command for simplicity)
engine = None

async def get_session():
    global engine
    if engine is None:
        settings = get_settings()
        engine = create_async_engine(settings.database_url, echo=False)
    return AsyncSession(engine, expire_on_commit=False)


async def cmd_help():
    print("""
Available commands:
  create_user <email> <role>          - role: admin, reviewer, viewer
  list_users
  create_batch <user_id> <sftp_path>
  list_batches
  create_prediction <batch_id> <filename> <label> <confidence> [overlay_path]
  list_predictions
  update_prediction <pred_id> <label>
  delete_prediction <pred_id>
  delete_batch <batch_id>
  exit / quit
""")

async def cmd_create_user(email: str, role: str):
    async with await get_session() as session:
        repo = UserRepository(session)
        user = await repo.create(email, role)
        await session.commit()
        print(f"✅ User created: id={user.id}, email={user.email}, role={user.role}")

async def cmd_list_users():
    async with await get_session() as session:
        repo = UserRepository(session)
        users = await repo.list()
        for u in users:
            print(f"{u.id} | {u.email} | {u.role}")

async def cmd_create_batch(user_id_str: str, sftp_path: str):
    user_id = uuid.UUID(user_id_str)
    async with await get_session() as session:
        repo = BatchRepository(session)
        batch_create = BatchCreate(sftp_path=sftp_path)
        batch = await repo.create(batch_create, owner_id=user_id)
        await session.commit()
        print(f"✅ Batch created: id={batch.id}, status={batch.status}")

async def cmd_list_batches():
    async with await get_session() as session:
        repo = BatchRepository(session)
        # Temporary method to list all batches (add to repo if needed)
        from sqlalchemy import select
        from app.db.models import Batch
        result = await session.execute(select(Batch))
        batches = result.scalars().all()
        for b in batches:
            print(f"{b.id} | owner={b.owner_id} | {b.status} | {b.sftp_path}")

async def cmd_create_prediction(batch_id_str: str, filename: str, label_str: str, confidence_str: str, overlay_path: str = None):
    batch_id = uuid.UUID(batch_id_str)
    confidence = float(confidence_str)
    label = DocumentLabel(label_str)
    async with await get_session() as session:
        repo = PredictionRepository(session)
        pred_create = PredictionCreate(
            batch_id=batch_id,
            filename=filename,
            label=label,
            confidence=confidence,
            overlay_path=overlay_path,
        )
        pred = await repo.create(pred_create)
        await session.commit()
        print(f"✅ Prediction created: id={pred.id}, label={pred.label}, confidence={pred.confidence}")

async def cmd_list_predictions():
    async with await get_session() as session:
        from sqlalchemy import select
        from app.db.models import Prediction
        result = await session.execute(select(Prediction))
        preds = result.scalars().all()
        for p in preds:
            print(f"{p.id} | batch={p.batch_id} | {p.label} | {p.confidence} | {p.filename}")

async def cmd_update_prediction(pred_id_str: str, label_str: str):
    pred_id = uuid.UUID(pred_id_str)
    label = DocumentLabel(label_str)
    async with await get_session() as session:
        repo = PredictionRepository(session)
        update = PredictionUpdate(label=label)
        pred = await repo.update(pred_id, update)
        await session.commit()
        if pred:
            print(f"✅ Prediction {pred.id} updated to label={pred.label}")
        else:
            print("❌ Prediction not found")

async def cmd_delete_prediction(pred_id_str: str):
    pred_id = uuid.UUID(pred_id_str)
    async with await get_session() as session:
        from sqlalchemy import delete
        from app.db.models import Prediction
        stmt = delete(Prediction).where(Prediction.id == pred_id)
        result = await session.execute(stmt)
        await session.commit()
        if result.rowcount:
            print(f"✅ Deleted prediction {pred_id}")
        else:
            print("❌ Prediction not found")

async def cmd_delete_batch(batch_id_str: str):
    batch_id = uuid.UUID(batch_id_str)
    async with await get_session() as session:
        from sqlalchemy import delete
        from app.db.models import Batch
        # Need to delete predictions first (foreign key constraint)
        from app.db.models import Prediction
        await session.execute(delete(Prediction).where(Prediction.batch_id == batch_id))
        stmt = delete(Batch).where(Batch.id == batch_id)
        result = await session.execute(stmt)
        await session.commit()
        if result.rowcount:
            print(f"✅ Deleted batch {batch_id} and its predictions")
        else:
            print("❌ Batch not found")

async def main():
    print("DB Repository REPL. Type 'help' for commands.")
    while True:
        line = input(">> ").strip()
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        if cmd in ("exit", "quit"):
            break
        elif cmd == "help":
            await cmd_help()
        elif cmd == "create_user" and len(parts) == 3:
            await cmd_create_user(parts[1], parts[2])
        elif cmd == "list_users":
            await cmd_list_users()
        elif cmd == "create_batch" and len(parts) == 3:
            await cmd_create_batch(parts[1], parts[2])
        elif cmd == "list_batches":
            await cmd_list_batches()
        elif cmd == "create_prediction" and len(parts) >= 5:
            overlay = parts[5] if len(parts) > 5 else None
            await cmd_create_prediction(parts[1], parts[2], parts[3], parts[4], overlay)
        elif cmd == "list_predictions":
            await cmd_list_predictions()
        elif cmd == "update_prediction" and len(parts) == 3:
            await cmd_update_prediction(parts[1], parts[2])
        elif cmd == "delete_prediction" and len(parts) == 2:
            await cmd_delete_prediction(parts[1])
        elif cmd == "delete_batch" and len(parts) == 2:
            await cmd_delete_batch(parts[1])
        else:
            print("Unknown command. Type 'help'.")

if __name__ == "__main__":
    asyncio.run(main())