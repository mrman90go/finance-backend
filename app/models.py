from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import String, Date, DateTime, Numeric, Boolean, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class Connection(Base):
    __tablename__ = "connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[str] = mapped_column(String(255), index=True)
    institution_name: Mapped[str | None] = mapped_column(String(255))
    requisition_id: Mapped[str] = mapped_column(String(255), unique=True)
    reference: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(50), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    gocardless_account_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    connection_id: Mapped[int] = mapped_column(index=True)
    institution_name: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    iban_last4: Mapped[str | None] = mapped_column(String(4))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    account_role: Mapped[str] = mapped_column(String(50), default="unknown")
    current_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    available_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    balance_updated_at: Mapped[datetime | None] = mapped_column(DateTime)

class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(index=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(255))
    booking_date: Mapped[date | None] = mapped_column(Date)
    value_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    merchant: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), default="Other")
    transaction_type: Mapped[str] = mapped_column(String(50), default="unknown")
    is_internal_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    transfer_group: Mapped[str | None] = mapped_column(String(255), index=True)
    raw_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("account_id", "provider_transaction_id", name="uq_account_provider_tx"),)

class SyncLog(Base):
    __tablename__ = "sync_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(50))
    message: Mapped[str | None] = mapped_column(Text)
