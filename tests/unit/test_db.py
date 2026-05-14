# test_db.py
import asyncio
import uuid
import contextlib
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.core.config import get_settings
from app.db.models import User
from app.domain.batch import BatchCreate
from app.domain.prediction import DocumentLabel, PredictionCreate
from app.domain.user import UserCreate
from app.repositories.batch_repo import BatchRepository
from app.repositories.prediction_repo import PredictionRepository

# Import FastAPI Users dependencies
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users import BaseUserManager, IntegerIDMixin
from fastapi_users.password import PasswordHelper

# Minimal UserManager for testing
class UserManagerTest(IntegerIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = "test-secret"
    verification_token_secret = "test-secret"

    async def on_after_register(self, user: User, request=None):
        print(f"User {user.id} has registered.")

async def get_user_db(session: AsyncSession):
    yield SQLAlchemyUserDatabase(session, User)

@contextlib.asynccontextmanager
async def get_user_manager(session: AsyncSession):
    async for user_db in get_user_db(session):
        yield UserManagerTest(user_db, PasswordHelper())

async def test():
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=True)

    # Clean tables
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE predictions, batches, users CASCADE"))

    # FIX: Create session with expire_on_commit=False
    async with AsyncSession(engine, expire_on_commit=False) as session:
        # Create test user using UserManager
        async with get_user_manager(session) as user_manager:
            user_create = UserCreate(
                email="test@example.com",
                password="testpassword123",
                role="admin"
            )
            user = await user_manager.create(user_create, safe=False)
            print(f"✅ Created user: {user.id}")

        # Create batch
        batch_repo = BatchRepository(session)
        batch_create = BatchCreate(sftp_path="/drop/test/file.tif")
        batch = await batch_repo.create(batch_create, owner_id=user.id)
        print(f"✅ Created batch: {batch.id}, status={batch.status}")

        # Create prediction
        pred_repo = PredictionRepository(session)
        pred_create = PredictionCreate(
            batch_id=batch.id,
            filename="file.tif",
            label=DocumentLabel.resume,
            confidence=0.95,
            overlay_path="minio://overlays/test.png",
        )
        prediction = await pred_repo.create(pred_create)
        print(f"✅ Created prediction: {prediction.id}, label={prediction.label}")

        await session.commit()

        # Now access attributes (they won't expire, no lazy reload triggered)
        batch_id = batch.id
        print(f"Batch ID: {batch_id}")

        # Verify batch predictions (using eager loading from repo)
        batch_from_db = await batch_repo.get_with_predictions(batch.id)
        print(f"✅ Batch has {len(batch_from_db.predictions)} prediction(s)")
        assert len(batch_from_db.predictions) == 1

        # List recent predictions
        recent = await pred_repo.list_recent(limit=5)
        print(f"✅ Recent predictions count: {len(recent)}")

    await engine.dispose()
    print("🎉 All tests passed!")

if __name__ == "__main__":
    asyncio.run(test())