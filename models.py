from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import ForeignKey, DateTime, func
import datetime

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True)
    hashed_password: Mapped[str]
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    problem_title: Mapped[str]
    problem_statement: Mapped[str]
    wrong_code: Mapped[str]
    correct_code: Mapped[str | None] = mapped_column(default=None)
    gap_category: Mapped[str | None] = mapped_column(default=None)
    gap_note: Mapped[str | None] = mapped_column(default=None)
    topic_tags: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="pending")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )