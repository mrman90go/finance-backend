import json
from datetime import datetime
from decimal import Decimal
from sqlalchemy import select
from .db import SessionLocal
from .models import Connection, Account, Transaction, SyncLog
from .gocardless import GoCardlessClient

async def sync_all():
    db = SessionLocal()
    log = SyncLog(status="running")
    db.add(log); db.commit()
    client = GoCardlessClient()
    try:
        connections = db.scalars(select(Connection)).all()
        inserted = 0
        for conn in connections:
            req = await client.requisition(conn.requisition_id)
            conn.status = req.get("status", "unknown")
            if req.get("status") not in {"LN", "SU"}:
                continue
            for gc_account_id in req.get("accounts", []):
                details = await client.account_details(gc_account_id)
                owner = details.get("account", {}) if isinstance(details, dict) else {}
                iban = owner.get("iban") or owner.get("accountNumber")
                if (
                    conn.institution_id == "BANCSABADELL_BSABESBB"
                    and settings.sabadell_iban_last4
                    and str(iban or "")[-4:] != settings.sabadell_iban_last4
                ):
                    continue
                account = db.scalar(select(Account).where(Account.gocardless_account_id == gc_account_id))
                if not account:
                    account = Account(gocardless_account_id=gc_account_id, connection_id=conn.id, institution_name=conn.institution_name)
                    db.add(account); db.flush()
                balances = await client.balances(gc_account_id)
                account.name = owner.get("name") or owner.get("ownerName") or account.name
                if iban: account.iban_last4 = str(iban)[-4:]
                txdata = await client.transactions(gc_account_id)
                bal_items = balances.get("balances", [])
                if bal_items:
                    current = next((b for b in bal_items if b.get("balanceType") == "closingBooked"), bal_items[0])
                    amount = current.get("balanceAmount", {}).get("amount")
                    if amount is not None: account.current_balance = Decimal(amount)
                    account.currency = current.get("balanceAmount", {}).get("currency", account.currency)
                    account.balance_updated_at = datetime.utcnow()
                for tx in txdata.get("transactions", {}).get("booked", []):
                    provider_id = tx.get("internalTransactionId") or tx.get("entryReference") or tx.get("transactionId")
                    if provider_id and db.scalar(select(Transaction).where(Transaction.account_id == account.id, Transaction.provider_transaction_id == provider_id)):
                        continue
                    amount = Decimal(tx["transactionAmount"]["amount"])
                    merchant = tx.get("creditorName") or tx.get("debtorName")
                    desc = tx.get("remittanceInformationUnstructured") or tx.get("additionalInformation")
                    t = Transaction(account_id=account.id, provider_transaction_id=provider_id,
                                    booking_date=tx.get("bookingDate"), value_date=tx.get("valueDate"),
                                    amount=amount, currency=tx["transactionAmount"].get("currency", "EUR"),
                                    merchant=merchant, description=desc, raw_json=json.dumps(tx))
                    t.transaction_type = "income" if amount > 0 else "expense"
                    db.add(t); inserted += 1
                conn.last_synced_at = datetime.utcnow()
        db.commit()
        log.status = "success"; log.message = f"Inserted {inserted} transactions"; log.finished_at = datetime.utcnow(); db.commit()
        return {"inserted": inserted}
    except Exception as e:
        db.rollback(); log.status = "error"; log.message = str(e)[:2000]; log.finished_at = datetime.utcnow(); db.commit(); raise
    finally:
        db.close()
