from datetime import date, timedelta
from html import escape

from fastapi.responses import HTMLResponse
from sqlalchemy import select

from .db import SessionLocal
from .insights import finance_summary, transaction_text
from .models import Account, Transaction


def period_start(period: str) -> date:
    today = date.today()
    if period == "daily":
        return today - timedelta(days=13)
    if period == "yearly":
        return today.replace(month=1, day=1)
    return today.replace(day=1)


def dashboard_transactions(db, period: str):
    start = period_start(period)
    rows = db.scalars(
        select(Transaction)
        .where(Transaction.booking_date >= start)
        .order_by(Transaction.booking_date.desc(), Transaction.id.desc())
    ).all()
    seen = set()
    result = []
    for row in rows:
        fingerprint = (
            row.account_id,
            row.booking_date,
            row.amount,
            row.currency,
            " ".join(transaction_text(row).split()),
        )
        if row.is_internal_transfer or fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(row)
    return result[:250]


def render_dashboard(period: str, view: str) -> HTMLResponse:
    if period not in {"daily", "monthly", "yearly"}:
        period = "monthly"
    if view not in {"overview", "transactions", "categories", "accounts"}:
        view = "overview"

    db = SessionLocal()
    try:
        summary = finance_summary(db, period)
        accounts = db.scalars(
            select(Account).order_by(Account.institution_name, Account.name)
        ).all()
        account_names = {
            account.id: f"{account.institution_name or 'Account'} · {account.name or 'Unnamed'}"
            for account in accounts
        }
        transactions = dashboard_transactions(db, period)
        total_assets = sum((account.current_balance or 0) for account in accounts)

        links = {
            "overview": f"/?view=overview&period={period}",
            "transactions": f"/?view=transactions&period={period}",
            "categories": f"/?view=categories&period={period}",
            "accounts": f"/?view=accounts&period={period}",
        }
        nav = "".join(
            f"<a class='nav-item {'selected' if item == view else ''}' href='{href}'>{label}</a>"
            for item, label, href in [
                ("overview", "Overview", links["overview"]),
                ("transactions", "Transactions", links["transactions"]),
                ("categories", "Categories", links["categories"]),
                ("accounts", "Accounts", links["accounts"]),
            ]
        )
        period_nav = "".join(
            f"<a class='period {'selected' if choice == period else ''}' href='/?view={view}&period={choice}'>{label}</a>"
            for choice, label in [("daily", "Daily"), ("monthly", "Month"), ("yearly", "Year")]
        )
        category_rows = "".join(
            f"<tr><td>{escape(category)}</td><td class='money negative'>−€{float(amount):,.2f}</td></tr>"
            for category, amount in summary["categories"]
        ) or "<tr><td colspan='2' class='muted'>No expenses in this period.</td></tr>"
        transaction_rows = "".join(
            f"<tr><td>{transaction.booking_date}</td><td>{escape(account_names.get(transaction.account_id, 'Account'))}</td><td class='payee'>{escape(transaction.merchant or transaction.description or 'Transaction')}</td><td><span class='category'>{escape(transaction.category)}</span></td><td class='money {'positive' if transaction.amount >= 0 else 'negative'}'>{'+' if transaction.amount >= 0 else '−'}€{abs(float(transaction.amount)):,.2f}</td></tr>"
            for transaction in transactions
        ) or "<tr><td colspan='5' class='muted'>No non-transfer transactions in this period.</td></tr>"
        account_rows = "".join(
            f"<tr><td>{escape(account.institution_name or '')}</td><td>{escape(account.name or 'Unnamed account')}</td><td>•••• {escape(account.iban_last4 or '')}</td><td class='money'>€{float(account.current_balance or 0):,.2f}</td></tr>"
            for account in accounts
        )
        recent_rows = "".join(
            f"<tr><td>{transaction.booking_date}</td><td class='payee'>{escape(transaction.merchant or transaction.description or 'Transaction')}</td><td><span class='category'>{escape(transaction.category)}</span></td><td class='money {'positive' if transaction.amount >= 0 else 'negative'}'>{'+' if transaction.amount >= 0 else '−'}€{abs(float(transaction.amount)):,.2f}</td></tr>"
            for transaction in summary["recent"]
        )

        if view == "transactions":
            content = f"""<section class='panel ledger'><div class='section-head'><div><h2>Transactions</h2><p>{summary["label"]} · imported bank activity</p></div><span class='count'>{len(transactions)} shown</span></div><div class='table-wrap'><table><thead><tr><th>Date</th><th>Account</th><th>Payee</th><th>Category</th><th class='money'>Amount</th></tr></thead><tbody>{transaction_rows}</tbody></table></div><p class='footnote'>Matched transfers and exact duplicate imports are excluded from this working view.</p></section>"""
        elif view == "categories":
            content = f"""<section class='panel'><div class='section-head'><div><h2>Categories</h2><p>Where money went in {escape(summary["label"])}</p></div><div class='big-money negative'>−€{float(summary["expenses"]):,.2f}</div></div><div class='table-wrap category-table'><table><thead><tr><th>Category</th><th class='money'>Spent</th></tr></thead><tbody>{category_rows}</tbody></table></div><p class='footnote'>Automatic categories are based on merchant names. We can add review and custom rules next.</p></section>"""
        elif view == "accounts":
            content = f"""<section class='panel'><div class='section-head'><div><h2>Accounts</h2><p>Current balances from your connected banks</p></div><div class='big-money'>€{float(total_assets):,.2f}</div></div><div class='table-wrap'><table><thead><tr><th>Institution</th><th>Account</th><th>IBAN</th><th class='money'>Balance</th></tr></thead><tbody>{account_rows}</tbody></table></div></section>"""
        else:
            content = f"""<div class='metrics'><section class='metric'><span>Income</span><strong class='positive'>+€{float(summary["income"]):,.2f}</strong></section><section class='metric'><span>Spent</span><strong class='negative'>−€{float(summary["expenses"]):,.2f}</strong></section><section class='metric'><span>Net</span><strong class='{'positive' if summary["net"] >= 0 else 'negative'}'>€{float(summary["net"]):,.2f}</strong></section><section class='metric'><span>All accounts</span><strong>€{float(total_assets):,.2f}</strong></section></div><div class='grid'><section class='panel'><div class='section-head'><div><h2>Spending by category</h2><p>{escape(summary["label"])}</p></div><a href='{links["categories"]}'>View all</a></div><table><tbody>{category_rows}</tbody></table></section><section class='panel'><div class='section-head'><div><h2>Recent activity</h2><p>Review your latest imported entries</p></div><a href='{links["transactions"]}'>Open ledger</a></div><table><thead><tr><th>Date</th><th>Payee</th><th>Category</th><th class='money'>Amount</th></tr></thead><tbody>{recent_rows}</tbody></table></section></div>"""

        return HTMLResponse(f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>Personal Finance</title><style>
        :root{{--ink:#102a43;--page:#e8ecf0;--surface:#fff;--text:#272630;--muted:#627d98;--border:#d6dfe7;--accent:#8719e0;--positive:#147d64;--negative:#e12d39}}*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--text);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-feature-settings:"tnum","ss01","ss04"}}.shell{{display:grid;grid-template-columns:220px minmax(0,1fr);min-height:100vh}}aside{{background:var(--ink);color:#e8ecf0;padding:24px 12px;display:flex;flex-direction:column}}.brand{{font-size:19px;font-weight:700;padding:0 10px 28px}}.brand small{{display:block;color:#9fb3c8;font-size:12px;font-weight:400;margin-top:4px}}.nav-item{{color:#c8d5e2;text-decoration:none;padding:9px 10px;border-radius:4px;margin:2px 0;font-size:14px}}.nav-item:hover{{background:#173b5b}}.nav-item.selected{{color:white;background:#254d70;box-shadow:inset 3px 0 var(--accent)}}.logout{{margin-top:auto;color:#c8d5e2;text-decoration:none;padding:9px 10px;font-size:14px}}main{{min-width:0;padding:30px 34px;max-width:1400px;width:100%}}header{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;border-bottom:1px solid var(--border);padding-bottom:18px}}h1,h2{{margin:0;color:var(--ink)}}h1{{font-size:27px;letter-spacing:.1px}}h2{{font-size:18px}}p{{color:var(--muted);margin:5px 0 0;font-size:13px}}.periods{{display:flex;border:1px solid var(--border);border-radius:5px;overflow:hidden;background:var(--surface)}}.period{{text-decoration:none;color:var(--ink);padding:7px 11px;font-size:13px;border-right:1px solid var(--border)}}.period:last-child{{border:0}}.period.selected{{background:var(--accent);color:white}}.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:22px 0}}.metric,.panel{{background:var(--surface);border:1px solid var(--border);border-radius:6px}}.metric{{padding:15px}}.metric span{{color:var(--muted);font-size:13px;display:block;margin-bottom:7px}}.metric strong,.big-money{{font-size:21px;font-weight:650;white-space:nowrap}}.positive{{color:var(--positive)}}.negative{{color:var(--negative)}}.grid{{display:grid;grid-template-columns:minmax(280px,.75fr) minmax(450px,1.5fr);gap:16px}}.panel{{padding:16px;margin-top:22px;overflow:hidden}}.grid .panel{{margin-top:0}}.section-head{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;gap:16px}}.section-head a{{color:#1980d4;text-decoration:none;font-size:13px;padding-top:3px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th{{text-align:left;color:var(--muted);font-weight:500;font-size:12px;padding:9px 8px;border-bottom:1px solid var(--border);white-space:nowrap}}td{{padding:10px 8px;border-bottom:1px solid var(--border);vertical-align:middle}}tbody tr:hover{{background:#f7fafc}}.money{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}.payee{{font-weight:500;max-width:270px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.category{{border-radius:4px;background:#f0edf6;color:#4f3968;padding:3px 6px;font-size:12px;white-space:nowrap}}.table-wrap{{overflow:auto}}.count{{font-size:12px;color:var(--muted);padding-top:3px}}.footnote{{margin:14px 0 0;font-size:12px}}@media(max-width:850px){{.shell{{display:block}}aside{{padding:12px;display:flex;flex-direction:row;align-items:center;overflow:auto;gap:4px}}.brand{{padding:0 14px 0 0;white-space:nowrap}}.brand small{{display:none}}.nav-item{{white-space:nowrap}}.logout{{margin:0 0 0 auto;white-space:nowrap}}main{{padding:20px 14px}}.grid{{grid-template-columns:1fr}}}}@media(max-width:580px){{header{{display:block}}.periods{{display:inline-flex;margin-top:14px}}.metrics{{grid-template-columns:repeat(2,1fr)}}.payee{{max-width:150px}}.grid .panel{{padding:12px}}}}
        </style></head><body><div class='shell'><aside><div class='brand'>Personal Finance<small>Private bank ledger</small></div>{nav}<a class='logout' href='/logout'>Log out</a></aside><main><header><div><h1>{escape(view.title())}</h1><p>{escape(summary["label"])}</p></div><nav class='periods'>{period_nav}</nav></header>{content}</main></div></body></html>""")
    finally:
        db.close()
