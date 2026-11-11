"""Local intent model for AI Guard: understands free-form requests without an external LLM.

A TF-IDF + logistic regression classifier trained on example phrases at startup (a few milliseconds),
plus entity extraction for amounts, phone numbers, emails, frequencies and contact names.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

EXAMPLES: dict[str, list[str]] = {
    "send": ["send it to", "pay that person", "i owe sam 20 pay him", "pay my share", "split the bill and pay 15", "reimburse kiran 35", "tip the driver 5", "pay the invoice amount to",
        "send 20 to priya", "pay my designer 150", "transfer 40 dollars to +1 201 555 0123", "pay sam for lunch",
             "give 25 to my friend", "can you pay 300 to the contractor", "send money to mom", "wire 80 to bob",
             "pay rent 900 to landlord", "please pay him 45", "settle up 30 with kiran", "pay back alex 12"],
    "risk": ["is it safe to send 450 to support@paypa1.com", "is this a scam", "check this payment for fraud",
             "is it risky to pay this person", "can i trust this email", "should i pay this", "is this legit",
             "fraud check on 200 to this account", "does this look suspicious", "how risky is paying 900"],
    "verify_phone": ["is this number a scam", "can i pay this number", "check +44 number", "is the phone linked to paypal",
        "verify +1 408 555 0134", "is this number on paypal", "who owns this number", "check this phone",
                     "is +1 900 555 0199 safe", "validate phone number", "is this number real", "look up this mobile"],
    "stuck": ["my money hasn't shown up", "money not showing up", "he never got my payment", "she didn't get the money", "did they receive my payment", "what happened to my transfer", "payment still not there", "my friend says he never got it", "the transfer hasn't arrived yet", "has my payout gone through", "track my payment", "where's the money i sent", "is my payment complete", "money missing", "why hasn't it been delivered",
        "which payments are stuck", "where is my money", "why is my payment pending", "payment not received",
              "what is still processing", "anything on hold", "my payout didn't arrive", "status of my payments",
              "is anything unclaimed", "what's taking so long"],
    "spending": ["what do i spend on food", "how much went on travel", "total spent last month", "spending on subscriptions", "money out this month", "how much did i pay out", "my expenses by category", "income this month",
        "where did my money go", "how much did i spend this month", "show my expenses", "spending summary",
                 "what did i spend on software", "monthly overview", "how much money came in", "my cash flow",
                 "break down my spending", "what are my biggest expenses"],
    "budget": ["how are my budgets", "am i over budget", "set a budget for travel", "budget status",
               "how much budget is left", "will i overspend this month", "my limits for food"],
    "recurring": ["any subscriptions", "what are my recurring payments", "list my subscriptions",
                  "what do i pay every month", "show repeating charges"],
    "tips": ["give me tips", "how can i save money", "any advice", "recommendations for my finances",
             "how do i improve my cash flow", "suggest ways to cut costs"],
    "invoice": ["invoice maya 250 for logo design", "bill my client 400", "send an invoice", "request payment from john",
                "charge acme 1200 for the website", "create an invoice for consulting", "list my invoices",
                "show unpaid invoices"],
    "disputes": ["any disputes", "show chargebacks", "do i have open claims", "customer complaints on payments"],
    "sync": ["sync my paypal", "import transactions", "refresh my paypal activity", "pull latest transactions"],
    "job_create": ["pay the plumber when he is done", "pay the designer once the logo is delivered", "when the job is complete pay 200", "release payment after the work is done",
        "create a job logo design for +1 201 555 0123 for 150", "add a task for my contractor paying 200",
                   "new job website fix 300 for dev@example.com", "set up a gig for priya 80",
                   "make a job for the plumber 120", "track a job and pay when done"],
    "job_complete": ["the job is done", "work delivered pay now", "contractor finished the work", "mark it complete and pay", "approve the finished work",
        "mark the logo job complete", "the website job is done", "job finished pay the contractor",
                     "logo design is complete", "complete job 3", "the plumber finished", "task done please pay"],
    "jobs": ["show my jobs", "list open jobs", "which jobs are unpaid", "what jobs are pending"],
    "schedule": ["pay priya 50 every friday", "send rent 900 every month", "pay 20 daily to my kid",
                 "schedule 100 to +1 201 555 0123 weekly", "set up a recurring payment", "auto pay my cleaner each week",
                 "pay this next monday", "monthly payment of 300 to the gym"],
    "negotiate": ["buy me a cheap camera", "find me a good price and buy it", "get it for less than 300", "agent to agent purchase", "make an offer to the seller", "talk the seller down",
        "negotiate a logo from +1 201 555 0123 with budget 300", "get me the best price for a website",
                  "haggle with the seller", "make a deal for the camera under 400", "bargain for this item",
                  "agent negotiate with the seller and pay", "buy a used bike for at most 150"],
    "shop": ["buy me a camera under 300", "shop for headphones", "find me a used bike and buy it", "purchase a desk lamp",
             "i need a laptop stand under 50", "get me a guitar for less than 200", "order a coffee machine",
             "look for a phone case and check out", "buy the cheapest monitor", "shop for a gift under 40"],
    "sell": ["sell my camera for 300", "list my bike for sale at 150", "i want to sell my old laptop for 400",
             "put my guitar on the market for 200", "create a listing for my desk", "sell headphones 60"],
    "split": ["split 90 dinner with priya and sam", "split the bill with sam", "divide 120 between me and kiran",
              "share the cab fare 30 with alex", "split rent 1500 with my roommates", "go halves on 40 with priya",
              "request my share from friends"],
    "agents": ["what did my agents do", "show agent activity", "automation log", "what payments were made automatically",
               "agent history"],
    "help": ["whats up", "hey there", "good morning", "ok", "cool", "what is payminute", "show me around", "hello", "hi", "what can you do", "help", "how does this work", "who are you", "thanks"],
}

AMOUNT_RE = re.compile(r"(?:\$|usd\s*|₹|rs\.?\s*)?(\d+(?:,\d{3})*(?:\.\d{1,2})?)", re.I)
EVERY_RE = {
    "daily": re.compile(r"\b(daily|every\s+day|each\s+day)\b", re.I),
    "weekly": re.compile(r"\b(weekly|every\s+week|each\s+week|every\s+(mon|tue|wed|thu|fri|sat|sun)\w*)\b", re.I),
    "monthly": re.compile(r"\b(monthly|every\s+month|each\s+month)\b", re.I),
}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@lru_cache(maxsize=1)
def _model():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline, make_union

    texts, labels = [], []
    for label, phrases in EXAMPLES.items():
        texts += phrases
        labels += [label] * len(phrases)
    features = make_union(TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True),
                          TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True))
    model = make_pipeline(features, LogisticRegression(max_iter=2000, C=8))
    model.fit(texts, labels)
    return model


def _clean(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", " contact ", text)
    text = re.sub(r"\+?\d[\d\s().-]{6,}\d", " number ", text)
    return re.sub(r"\$?\d+(?:\.\d+)?", " amount ", text).lower()


def classify(text: str) -> tuple[str, float]:
    """Top intent and its probability."""
    try:
        model = _model()
    except Exception:  # scikit-learn missing
        return "help", 0.0
    probs = model.predict_proba([_clean(text)])[0]
    i = int(probs.argmax())
    return model.classes_[i], float(probs[i])


def frequency(text: str) -> tuple[str | None, int | None]:
    """('weekly', weekday index) etc."""
    for every, rx in EVERY_RE.items():
        m = rx.search(text)
        if m:
            day = next((i for i, d in enumerate(WEEKDAYS) if re.search(rf"\b{d}\w*", m.group(0), re.I)), None)
            return every, day
    m = re.search(r"\b(next|on)\s+(mon|tue|wed|thu|fri|sat|sun)\w*", text, re.I)
    if m:
        return "once", WEEKDAYS.index(m.group(2).lower()[:3])
    return None, None


def contact_names(text: str, contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every contact mentioned by name."""
    low = text.lower()
    found = []
    for c in contacts:
        name = (c.get("name") or "").strip()
        if not name or name.startswith("+"):
            continue
        first = name.split()[0].lower()
        if re.search(rf"\b{re.escape(name.lower())}\b", low) or len(first) > 2 and re.search(rf"\b{re.escape(first)}\b", low):
            if c not in found:
                found.append(c)
    return found


def contact_name(text: str, contacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match a name in the text ("pay Priya") against people the user has paid or linked."""
    low = text.lower()
    best = None
    for c in contacts:
        name = (c.get("name") or "").strip()
        if not name or name.startswith("+"):
            continue
        first = name.split()[0].lower()
        if re.search(rf"\b{re.escape(name.lower())}\b", low):
            return c
        if len(first) > 2 and re.search(rf"\b{re.escape(first)}\b", low):
            best = best or c
    return best
