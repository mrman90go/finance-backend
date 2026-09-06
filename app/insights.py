from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from .models import Transaction

TRANSFER_WORDS = ("transfer", "transferencia", "traspaso", "trf", "entre cuentas", "internal transfer")
CATEGORY_RULES = {
    "Groceries": ("mercadona", "carrefour", "lidl", "aldi", "dia ", "eroski", "supermerc"),
    "Dining": ("restaurant", "restaurante", "glovo", "uber eats", "just eat", "cafe", "bar "),
    "Transport": ("renfe", "metro", "bus ", "uber", "cabify", "bolt", "repsol", "cepsa", "parking"),
    "Housing": ("alquiler", "rent ", "inmobiliaria", "hipoteca", "mortgage"),
    "Utilities": ("iberdrola", "endesa", "naturgy", "vodafone", "movistar", "orange", "agua"),
    "Subscriptions": ("netflix", "spotify", "amazon prime", "apple.com", "google one", "disney"),
    "Health": ("farmacia", "hospital", "clinica", "dent", "sanitas"),
    "Shopping": ("amazon", "zara", "ikea", "decathlon", "paypal"),
    "Fees": ("commission", "comision", "fee", "cuota"),
    "Cash": ("cash withdrawal", "retirada", "cajero", "atm "),
}


def transaction_text(transaction):
    return " ".join(filter(None, [transaction.merchant, transaction.description])).lower()


def category_for(transaction):
    if transaction.amount > 0:
        return "Income"
    text = transaction_text(transaction)
    for category, words in CATEGORY_RULES.items():
        if any(word in text for word in words):
            return category
    return "Other"


def transfer_hint(transaction):
    return any(word in transaction_text(transaction) for word in TRANSFER_WORDS)

def same_counterparty(first, second):
    first_text = " ".join(transaction_text(first).split())
    second_text = " ".join(transaction_text(second).split())
    return len(first_text) >= 8 and first_text == second_text and len(first_text.split()) >= 2


def refresh_classifications(db):
    transactions = db.scalars(
        select(Transaction).order_by(Transaction.booking_date, Transaction.id)
    ).all()
    for transaction in transactions:
        transaction.category = category_for(transaction)
        transaction.is_internal_transfer = False
        transaction.transfer_group = None

    outgoing = [t for t in transactions if t.amount < 0]
    incoming = [t for t in transactions if t.amount > 0]
    used_incoming = set()
    for sent in outgoing:
        if not transfer_hint(sent):
            continue
        for received in incoming:
            if received.id in used_incoming or received.account_id == sent.account_id:
                continue
            if abs(sent.amount) != abs(received.amount):
                continue
            if not sent.booking_date or not received.booking_date:
                continue
            if abs((sent.booking_date - received.booking_date).days) > 3:
                continue
            explicit_transfer = transfer_hint(sent) and (
                transfer_hint(received) or sent.booking_date == received.booking_date
            )
            named_counterparty = same_counterparty(sent, received)
            if not (explicit_transfer or named_counterparty):
                continue
            group = f"transfer-{sent.id}-{received.id}"
            sent.is_internal_transfer = received.is_internal_transfer = True
            sent.transfer_group = received.transfer_group = group
            sent.category = received.category = "Transfers"
            used_incoming.add(received.id)
            break
    db.commit()


def finance_summary(db, period):
    refresh_classifications(db)
    transactions = db.scalars(select(Transaction)).all()
    today = date.today()
    if period == "daily":
        start = today - timedelta(days=13)
        label = "Last 14 days"
        key_for = lambda tx: tx.booking_date.isoformat() if tx.booking_date else "Unknown"
    elif period == "yearly":
        start = date.min
        label = "Yearly"
        key_for = lambda tx: str(tx.booking_date.year) if tx.booking_date else "Unknown"
    else:
        start = (today.replace(day=1) - timedelta(days=335)).replace(day=1)
        label = "Last 12 months"
        key_for = lambda tx: tx.booking_date.strftime("%Y-%m") if tx.booking_date else "Unknown"

    visible = [t for t in transactions if t.booking_date and t.booking_date >= start and not t.is_internal_transfer]
    income = sum((t.amount for t in visible if t.amount > 0), Decimal("0"))
    expenses = sum((-t.amount for t in visible if t.amount < 0), Decimal("0"))
    categories = defaultdict(Decimal)
    trend = defaultdict(lambda: {"income": Decimal("0"), "expenses": Decimal("0")})
    for transaction in visible:
        key = key_for(transaction)
        if transaction.amount > 0:
            trend[key]["income"] += transaction.amount
        else:
            value = -transaction.amount
            trend[key]["expenses"] += value
            categories[transaction.category] += value

    return {
        "label": label,
        "income": income,
        "expenses": expenses,
        "net": income - expenses,
        "categories": sorted(categories.items(), key=lambda item: item[1], reverse=True),
        "trend": [(key, values["income"], values["expenses"]) for key, values in sorted(trend.items())],
        "recent": sorted(visible, key=lambda t: (t.booking_date, t.id), reverse=True)[:25],
    }
