# Personal Finance Backend — GoCardless Bank Account Data

A single-user, read-only personal finance backend designed to ingest N26 and Banco Sabadell accounts through GoCardless Bank Account Data and expose normalized data for ChatGPT later via MCP/Apps SDK.

## Architecture

- FastAPI API
- SQLAlchemy database (SQLite locally; PostgreSQL recommended for production)
- GoCardless Bank Account Data integration
- Daily automatic synchronization (configurable)
- Transaction deduplication
- Account-level balances
- Internal-transfer detection hooks
- Normalized categories
- Dashboard endpoint
- No payment/write-to-bank functionality

## Important

This repo intentionally contains **no bank credentials or tokens**. Set them through environment variables/secrets.

## Production

Use PostgreSQL, HTTPS, and managed secrets. The user-facing interface should remain ChatGPT; this backend is plumbing only.
