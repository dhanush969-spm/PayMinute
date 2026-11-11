"""Shared formatting helpers."""
from __future__ import annotations


def fmt_money(x: float) -> str:
    """Format an amount in the configured currency, e.g. $1,209.05."""
    from .. import config
    cur = config.settings.currency
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "AUD": "A$", "CAD": "C$", "JPY": "¥"}.get(cur)
    return f"{sym}{x:,.2f}" if sym else f"{x:,.2f} {cur}"
