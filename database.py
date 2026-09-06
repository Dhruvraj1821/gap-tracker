from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import os
DATABASE_URL = DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:devpassword@localhost/gaptracker")

IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"
engine = create_async_engine(DATABASE_URL, echo=not IS_PRODUCTION)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)