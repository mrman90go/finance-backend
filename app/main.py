import hashlib
import hmac
import secrets
import time
from html import escape
from urllib.parse import parse_qs
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
from .insights import finance_summary

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
def private_dashboard(request: Request, period: str = "monthly"):
    if not dashboard_session_valid(request):
        return dashboard_login_page()
    if period not in {"daily", "monthly", "yearly"}:
        period = "monthly"
    db = SessionLocal()
    try:
        accounts = db.scalars(select(Account).order_by(Account.institution_name, Account.name)).all()
        summary = finance_summary(db, period)
        total = sum((a.current_balance or 0) for a in accounts)
        account_rows = "".join(
            f"<tr><td>{escape(a.institution_name or '')}</td><td>{escape(a.name or '')}</td><td>•••• {escape(a.iban_last4 or '')}</td><td class='amount'>{float(a.current_balance or 0):,.2f} {escape(a.currency)}</td></tr>"
            for a in accounts
        )
        category_rows = "".join(
            f"<tr><td>{escape(category)}</td><td class='amount'>€{float(amount):,.2f}</td></tr>"
            for category, amount in summary["categories"]
        ) or "<tr><td colspan='2'>No expenses in this period.</td></tr>"
        transaction_rows = "".join(
            f"<tr><td>{t.booking_date or ''}</td><td>{escape(t.category)}</td><td>{escape(t.merchant or t.description or 'Transaction')}</td><td class='amount {'positive' if t.amount > 0 else 'negative'}'>{float(t.amount):,.2f} {escape(t.currency)}</td></tr>"
            for t in summary["recent"]
        ) or "<tr><td colspan='4'>No non-transfer transactions in this period.</td></tr>"
        trend = summary["trend"]
        maximum = max((max(income, expenses) for _, income, expenses in trend), default=1)
        trend_rows = "".join(
            f"<div class='trend-row'><span>{escape(label)}</span><div class='bar income' style='width:{float(income / maximum * 100):.1f}%'></div><b>+€{float(income):,.0f}</b><div class='bar expense' style='width:{float(expenses / maximum * 100):.1f}%'></div><b>-€{float(expenses):,.0f}</b></div>"
            for label, income, expenses in trend
        ) or "<p>No transaction history for this period.</p>"
        active = {"daily": "", "monthly": "", "yearly": ""}
        active[period] = "active"
        return HTMLResponse(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Personal Finance</title>
        <style>
        :root{{color-scheme:light;font-family:system-ui,-apple-system,sans-serif;color:#172033;background:#f5f7fb}}body{{margin:0}}main{{max-width:1120px;margin:auto;padding:28px 22px 48px}}header{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}}h1{{margin:0;font-size:2rem}}p{{color:#64748b}}a{{color:#0369a1}}.periods{{display:flex;gap:8px;margin:22px 0}}.periods a{{padding:8px 13px;border-radius:9px;text-decoration:none;background:#e7edf7;color:#334155}}.periods .active{{background:#0f766e;color:white}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}.card,section{{background:white;border-radius:15px;padding:18px;box-shadow:0 1px 4px #d8e0ed}}.card p{{margin:0 0 8px;font-size:.88rem}}.value{{font-size:1.7rem;font-weight:750;font-variant-numeric:tabular-nums}}.positive{{color:#087f5b}}.negative{{color:#c2410c}}section{{margin-top:16px}}h2{{font-size:1.15rem;margin:0 0 12px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px 8px;text-align:left;border-bottom:1px solid #e8edf4}}th{{font-size:.78rem;text-transform:uppercase;color:#64748b}}.amount{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}.trend-row{{display:grid;grid-template-columns:88px minmax(10px,1fr) 68px minmax(10px,1fr) 68px;align-items:center;gap:8px;margin:10px 0;font-size:.82rem}}.bar{{height:9px;border-radius:10px;min-width:2px}}.income{{background:#34d399}}.expense{{background:#fb923c}}.note{{font-size:.85rem;margin-bottom:0}}@media(max-width:760px){{main{{padding:18px 12px}}.cards{{grid-template-columns:repeat(2,1fr)}}.trend-row{{grid-template-columns:66px 1fr 58px}}.trend-row .expense{{display:none}}.trend-row b:last-child{{display:none}}table{{font-size:.86rem}}th,td{{padding:8px 5px}}}}@media(max-width:430px){{.cards{{grid-template-columns:1fr 1fr}}.value{{font-size:1.35rem}}}}
        </style></head><body><main><header><div><h1>Personal Finance</h1><p>{summary["label"]} · updated from your linked accounts</p></div><a href="/logout">Log out</a></header>
        <nav class="periods"><a class="{active['daily']}" href="/?period=daily">Daily</a><a class="{active['monthly']}" href="/?period=monthly">Monthly</a><a class="{active['yearly']}" href="/?period=yearly">Yearly</a></nav>
        <div class="cards"><div class="card"><p>Income</p><div class="value positive">€{float(summary["income"]):,.2f}</div></div><div class="card"><p>Expenses</p><div class="value negative">€{float(summary["expenses"]):,.2f}</div></div><div class="card"><p>Net</p><div class="value {'positive' if summary["net"] >= 0 else 'negative'}">€{float(summary["net"]):,.2f}</div></div><div class="card"><p>Total assets</p><div class="value">€{float(total):,.2f}</div></div></div>
        <section><h2>Income and expenses over time</h2>{trend_rows}</section>
        <section><h2>Expenses by category</h2><table><thead><tr><th>Category</th><th class="amount">Spent</th></tr></thead><tbody>{category_rows}</tbody></table></section>
        <section><h2>Accounts</h2><table><thead><tr><th>Institution</th><th>Account</th><th>IBAN</th><th class="amount">Balance</th></tr></thead><tbody>{account_rows}</tbody></table></section>
        <section><h2>Recent activity</h2><table><thead><tr><th>Date</th><th>Category</th><th>Description</th><th class="amount">Amount</th></tr></thead><tbody>{transaction_rows}</tbody></table><p class="note">Matched transfers between your linked accounts are excluded from income and expenses.</p></section>
        </main></body></html>""")
    finally:
        db.close()

@app.post("/login")
async def dashboard_login(request: Request):
    form = parse_qs((await request.body()).decode())
    password = form.get("password", [""])[0]
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
