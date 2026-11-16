import os
from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

URL = os.getenv("DB_URL", "sqlite:///ivr.db")  # use postgresql+psycopg://... in production
engine = create_engine(URL, connect_args={"check_same_thread": False} if URL.startswith("sqlite") else {})
Session = sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase): pass

class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("org_id", "phone"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(default=1, index=True)
    name: Mapped[str]
    phone: Mapped[str]
    group_name: Mapped[str] = mapped_column(index=True)
    consent: Mapped[bool] = mapped_column(default=False)
    consent_at: Mapped[Optional[datetime]]
    consent_channel: Mapped[str] = mapped_column(default="voice")
    consent_purpose: Mapped[str] = mapped_column(default="transactional")
    dnd: Mapped[bool] = mapped_column(default=False)

class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(default=1, index=True)
    name: Mapped[str]
    prompt: Mapped[str]
    language: Mapped[str] = mapped_column(default="English")
    group_name: Mapped[str]
    category: Mapped[str] = mapped_column(default="transactional")  # or promotional
    mode: Mapped[str] = mapped_column(default="sandbox")            # or pilot
    max_retries: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="draft")
    audio_id: Mapped[Optional[str]]
    audio_secs: Mapped[Optional[int]]

class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("campaign_id", "contact_id", "attempt"),)  # no double-calling
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(default=1, index=True)
    campaign_id: Mapped[int] = mapped_column(index=True)
    contact_id: Mapped[int] = mapped_column(index=True)
    attempt: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="queued", index=True)  # queued|dialing|answered|no-answer|busy|failed|skipped
    run_at: Mapped[datetime]
    provider_call_id: Mapped[Optional[str]] = mapped_column(index=True)
    note: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime]

class Gate(Base):
    __tablename__ = "gates"
    __table_args__ = (UniqueConstraint("org_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(default=1)
    key: Mapped[str]
    done: Mapped[bool] = mapped_column(default=False)
