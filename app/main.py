import hashlib
import hmac
import secrets
import time
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import delete, select, func
from sqlalchemy.orm import Session
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .config import settings
from .db import init_db, SessionLocal
from .models import Connection, Account, Transaction
from .gocardless import GoCardlessClient
from .sync import sync_all

app = FastAPI(title="Personal Finance Backend", version="0.2.0")
origins = [x.strip() for x in settings.cors_origins.split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["*"])
scheduler = AsyncIOScheduler()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(api_key: str | None = Security(api_key_header)):
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="API key is not configured")
    if api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

def remove_excluded_sabadell_data():
    if not settings.sabadell_iban_last4:
        return
    db = SessionLocal()
    try:
        connection_ids = db.scalars(
            select(Connection.id).where(Connection.institution_id == "BANCSABADELL_BSABESBB")
        ).all()
        if not connection_ids:
            return
        account_ids = db.scalars(
            select(Account.id).where(
                Account.connection_id.in_(connection_ids),
                (Account.iban_last4 != settings.sabadell_iban_last4) | Account.iban_last4.is_(None),
            )
        ).all()
        if account_ids:
            db.execute(delete(Transaction).where(Transaction.account_id.in_(account_ids)))
            db.execute(delete(Account).where(Account.id.in_(account_ids)))
            db.commit()
    finally:
        db.close()

class ConnectionRequest(BaseModel):
    institution_id: str
    institution_name: str | None = None

@app.on_event("startup")
async def startup():
    init_db()
    remove_excluded_sabadell_data()
    if settings.gc_secret_id and settings.gc_secret_key:
        scheduler.add_job(sync_all, "interval", hours=settings.sync_interval_hours, id="daily-sync", replace_existing=True)
        scheduler.start()

@app.on_event("shutdown")
async def shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)

def dashboard_session_valid(request: Request) -> bool:
    token = request.cookies.get("finance_dashboard")
    secret = settings.dashboard_session_secret
    if not token or not secret:
        return False
    try:
        issued_at, signature = token.split(".", 1)
        payload = issued_at.encode()
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and time.time() - int(issued_at) < 7 * 24 * 3600
    except (ValueError, TypeError):
        return False

def dashboard_login_page(error: str = "") -> HTMLResponse:
    message = '<p class="error">Incorrect password.</p>' if error else ""
    return HTMLResponse(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Personal Finance</title>
    <style>body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:grid;place-items:center;min-height:100vh;margin:0}}main{{background:#172554;padding:2rem;border-radius:16px;width:min(360px,88vw)}}input,button{{box-sizing:border-box;width:100%;padding:.75rem;margin:.5rem 0;border-radius:8px;border:0}}button{{background:#38bdf8;color:#082f49;font-weight:700}}.error{{color:#fca5a5}}</style></head><body><main><h1>Personal Finance</h1><p>Enter your dashboard password.</p>{message}<form method="post" action="/login"><input type="password" name="password" autocomplete="current-password" autofocus required><button type="submit">Open dashboard</button></form></main></body></html>""")

@app.get("/", response_class=HTMLResponse)
def private_dashboard(request: Request):
    if not dashboard_session_valid(request):
        return dashboard_login_page()
    db = SessionLocal()
    try:
        accounts = db.scalars(select(Account).order_by(Account.institution_name, Account.name)).all()
        recent = db.scalars(select(Transaction).order_by(Transaction.booking_date.desc()).limit(20)).all()
        total = sum((a.current_balance or 0) for a in accounts)
        account_rows = "".join(f"<tr><td>{a.institution_name or ''}</td><td>{a.name or ''}</td><td>•••• {a.iban_last4 or ''}</td><td class='amount'>{float(a.current_balance or 0):,.2f} {a.currency}</td></tr>" for a in accounts)
        transaction_rows = "".join(f"<tr><td>{t.booking_date or ''}</td><td>{t.merchant or t.description or 'Transaction'}</td><td class='amount'>{float(t.amount):,.2f} {t.currency}</td></tr>" for t in recent)
        return HTMLResponse(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Personal Finance</title>
        <style>body{{font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a;margin:0}}main{{max-width:960px;margin:auto;padding:2rem}}header{{display:flex;justify-content:space-between;align-items:center}}a{{color:#0369a1}}.total{{font-size:2.4rem;font-weight:700}}section{{background:white;border-radius:14px;padding:1.25rem;margin-top:1.25rem;box-shadow:0 1px 4px #cbd5e1}}table{{width:100%;border-collapse:collapse}}td,th{{padding:.7rem;text-align:left;border-bottom:1px solid #e2e8f0}}.amount{{text-align:right;font-variant-numeric:tabular-nums}}@media(max-width:600px){{main{{padding:1rem}}td,th{{padding:.5rem;font-size:.9rem}}}}</style></head><body><main><header><div><h1>Personal Finance</h1><p>Last synced balances and recent activity</p></div><a href="/logout">Log out</a></header><section><p>Total assets</p><div class="total">€{float(total):,.2f}</div><p>{len(accounts)} linked accounts</p></section><section><h2>Accounts</h2><table><thead><tr><th>Institution</th><th>Account</th><th>IBAN</th><th class="amount">Balance</th></tr></thead><tbody>{account_rows}</tbody></table></section><section><h2>Recent transactions</h2><table><thead><tr><th>Date</th><th>Description</th><th class="amount">Amount</th></tr></thead><tbody>{transaction_rows}</tbody></table></section></main></body></html>""")
    finally:
        db.close()

@app.post("/login")
async def dashboard_login(request: Request):
    body = (await request.body()).decode()
    password = body.partition("password=")[2].replace("+", " ")
    if not settings.dashboard_password or not hmac.compare_digest(password, settings.dashboard_password):
        return dashboard_login_page("incorrect")
    issued_at = str(int(time.time()))
    signature = hmac.new(settings.dashboard_session_secret.encode(), issued_at.encode(), hashlib.sha256).hexdigest()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("finance_dashboard", f"{issued_at}.{signature}", httponly=True, secure=True, samesite="strict", max_age=7 * 24 * 3600)
    return response

@app.get("/logout")
def dashboard_logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("finance_dashboard")
    return response

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/banks", dependencies=[Depends(require_api_key)])
async def banks(country: str = "es"):
    return await GoCardlessClient().institutions(country)

@app.post("/connections", dependencies=[Depends(require_api_key)])
async def create_connection(body: ConnectionRequest):
    if not settings.gc_secret_id or not settings.gc_secret_key:
        raise HTTPException(500, "GoCardless credentials are not configured")
    client = GoCardlessClient()
    reference = "personal-finance-" + secrets.token_hex(8)
    agreement = await client.create_agreement(body.institution_id)
    redirect = f"{settings.app_base_url}/connections/callback"
    req = await client.create_requisition(body.institution_id, redirect, reference, agreement=agreement["id"])
    db = SessionLocal()
    try:
        db.add(Connection(institution_id=body.institution_id, institution_name=body.institution_name, requisition_id=req["id"], reference=reference, status=req.get("status", "created")))
        db.commit()
    finally:
        db.close()
    return {"requisition_id": req["id"], "link": req.get("link"), "reference": reference}

@app.get("/connections/callback")
async def callback(requisition_id: str | None = None):
    return {"message": "Bank connection completed. You can now sync.", "requisition_id": requisition_id}

@app.post("/sync", dependencies=[Depends(require_api_key)])
async def sync():
    if not settings.gc_secret_id or not settings.gc_secret_key:
        raise HTTPException(500, "GoCardless credentials are not configured")
    return await sync_all()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/accounts", dependencies=[Depends(require_api_key)])
def accounts(db: Session = Depends(get_db)):
    rows = db.scalars(select(Account)).all()
    return [{"id": a.id, "institution": a.institution_name, "name": a.name, "iban_last4": a.iban_last4, "currency": a.currency, "role": a.account_role, "current_balance": float(a.current_balance) if a.current_balance is not None else None, "balance_updated_at": a.balance_updated_at} for a in rows]

@app.get("/transactions", dependencies=[Depends(require_api_key)])
def transactions(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.scalars(select(Transaction).order_by(Transaction.booking_date.desc()).limit(min(max(limit, 1), 1000))).all()
    return [{"id": t.id, "account_id": t.account_id, "date": t.booking_date, "amount": float(t.amount), "currency": t.currency, "merchant": t.merchant, "description": t.description, "category": t.category, "transaction_type": t.transaction_type, "is_internal_transfer": t.is_internal_transfer} for t in rows]

@app.get("/dashboard", dependencies=[Depends(require_api_key)])
def dashboard(db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account)).all()
    total = sum((a.current_balance or 0) for a in accounts)
    expenses = db.scalar(select(func.sum(Transaction.amount)).where(Transaction.amount < 0, Transaction.is_internal_transfer == False)) or 0
    income = db.scalar(select(func.sum(Transaction.amount)).where(Transaction.amount > 0, Transaction.is_internal_transfer == False)) or 0
    return {"total_assets": float(total), "income_recorded": float(income), "external_spending_recorded": float(abs(expenses)), "accounts": [{"institution": a.institution_name, "name": a.name, "balance": float(a.current_balance) if a.current_balance is not None else None} for a in accounts]}
